# -*- coding: utf-8 -*-
"""
fetch.py — "con robot" lấy tin, chạy trên GitHub Actions theo lịch.
Đọc RSS -> đọc toàn văn bài -> AI tóm tắt/phân loại/đánh giá -> ghi data.json.
App Streamlit chỉ đọc data.json để hiển thị.

Chạy: python fetch.py   (cần biến môi trường GEMINI_API_KEY)
"""

import os
import time
import json
import hashlib
from datetime import datetime, timezone, timedelta

import feedparser

# ---------------- Cấu hình (giống app, có thể chỉnh) ----------------

RSS_FEEDS = {
    "VnExpress - Kinh doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
    "CafeF":                  "https://cafef.vn/trang-chu.rss",
    "VietnamBiz - Kinh tế":   "https://vietnambiz.vn/kinh-te.rss",
    "Tuổi Trẻ - Kinh doanh":  "https://tuoitre.vn/rss/kinh-doanh.rss",
    "Báo Đầu tư":             "https://baodautu.vn/rss/home.rss",
}

TOPICS = [
    "GDP & Tăng trưởng", "Lạm phát", "Lãi suất", "Tỷ giá", "Chứng khoán",
    "Bất động sản", "Xuất nhập khẩu", "Doanh nghiệp", "Chính sách tiền tệ", "Khác",
]
IMPACTS = ["Tích cực", "Tiêu cực", "Trung lập"]

# Model AI. Nếu một model hết lượt, đổi sang model khác trong dòng này.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

MAX_NEW_PER_RUN = 15        # số tin mới xử lý mỗi lần chạy
MAX_STORE = 300             # giữ tối đa bao nhiêu tin trong data.json
MAX_CONTENT_CHARS = 4000
REQUEST_DELAY_SEC = 3

DATA_FILE = "data.json"
VN_TZ = timezone(timedelta(hours=7))   # giờ Việt Nam


# ---------------- Đọc / ghi kho data.json ----------------

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("articles", [])
        except Exception:
            return []
    return []


def save_data(articles):
    payload = {
        "updated_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
        "articles": articles,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def make_id(link):
    return hashlib.md5(link.encode("utf-8")).hexdigest()


# ---------------- Đọc toàn văn bài báo ----------------

def get_full_text(link, fallback=""):
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(link)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False,
                                       include_tables=False)
            if text and len(text.strip()) > 120:
                return text.strip()[:MAX_CONTENT_CHARS]
    except Exception:
        pass
    return fallback


# ---------------- AI: tóm tắt + phân loại + đánh giá tác động ----------------

def analyze(api_key, title, content, max_retries=3):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)

    prompt = f"""Bạn là chuyên gia phân tích tin kinh tế. Đọc bài sau và trả về JSON.

Tiêu đề: {title}
Nội dung bài: {content}

Yêu cầu:
1. "summary": tóm tắt CỐT LÕI của bài trong 5 đến 10 câu ngắn gọn bằng tiếng Việt.
   Tập trung nêu rõ các con số, số liệu cụ thể nếu bài có.
2. "topic": chọn ĐÚNG MỘT chủ đề phù hợp nhất trong: {TOPICS}
3. "impact": đánh giá tác động tới nền kinh tế / thị trường, chọn ĐÚNG MỘT trong: {IMPACTS}

Chỉ trả về JSON: {{"summary": "...", "topic": "...", "impact": "..."}}"""

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            data = json.loads(resp.text)
            topic = data.get("topic", "Khác")
            if topic not in TOPICS:
                topic = "Khác"
            impact = data.get("impact", "Trung lập")
            if impact not in IMPACTS:
                impact = "Trung lập"
            return {"ok": True, "summary": data.get("summary", "").strip(),
                    "topic": topic, "impact": impact}
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if "resource_exhausted" in low or "429" in msg:
                return {"ok": False, "error": msg, "kind": "quota"}
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return {"ok": False, "error": msg, "kind": "other"}


# ---------------- Chạy chính ----------------

def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("LỖI: chưa có GEMINI_API_KEY trong môi trường.")
        return

    articles = load_data()
    existing_ids = {a["id"] for a in articles}

    # Gom tin mới
    new_items = []
    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for entry in feed.entries:
            link = entry.get("link", "")
            if not link:
                continue
            aid = make_id(link)
            if aid in existing_ids:
                continue
            existing_ids.add(aid)
            new_items.append({
                "id": aid,
                "title": entry.get("title", "(Không có tiêu đề)"),
                "source": source_name,
                "link": link,
                "published": entry.get("published", ""),
                "rss_summary": entry.get("summary", "") or entry.get("description", ""),
            })

    new_items = new_items[:MAX_NEW_PER_RUN]
    print(f"Tìm thấy {len(new_items)} tin mới để xử lý.")

    added = 0
    for art in new_items:
        full_text = get_full_text(art["link"], fallback=art["rss_summary"])
        result = analyze(api_key, art["title"], full_text)
        if not result["ok"]:
            print(f"Dừng vì lỗi ({result.get('kind')}): {result.get('error')[:120]}")
            break
        articles.insert(0, {
            "id": art["id"],
            "title": art["title"],
            "source": art["source"],
            "link": art["link"],
            "published": art["published"],
            "summary": result["summary"],
            "topic": result["topic"],
            "impact": result["impact"],
            "created_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
        })
        added += 1
        print(f"  + Đã thêm: {art['title'][:60]}")
        time.sleep(REQUEST_DELAY_SEC)

    articles = articles[:MAX_STORE]
    save_data(articles)
    print(f"Xong. Thêm {added} tin. Tổng cộng đang lưu {len(articles)} tin.")


if __name__ == "__main__":
    main()
