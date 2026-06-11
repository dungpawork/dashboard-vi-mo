# -*- coding: utf-8 -*-
"""
fetch.py — robot lấy tin theo MÔ HÌNH NHIỀU BLOCK.
Chạy mỗi giờ (GitHub Actions). Mỗi lần thức:
  - đọc config.json (danh sách block, mỗi block có giờ cập nhật riêng)
  - chỉ chạy những block "tới giờ" (hoặc tất cả nếu bấm Run workflow tay)
  - với mỗi block: lấy RSS -> lọc theo TỪ KHÓA của block -> đọc toàn văn ->
    AI tóm tắt/phân loại/đánh giá -> lưu vào data.json theo block.

Biến môi trường: GEMINI_API_KEY (bắt buộc), FORCE_ALL ("true" để chạy mọi block).
"""

import os
import time
import json
import hashlib
from datetime import datetime, timezone, timedelta

import feedparser

IMPACTS = ["Tích cực", "Tiêu cực", "Trung lập"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

MAX_NEW_PER_BLOCK = 10      # mỗi block xử lý tối đa bao nhiêu tin mới mỗi lần
MAX_STORE_PER_BLOCK = 100   # mỗi block giữ tối đa bao nhiêu tin
MAX_CONTENT_CHARS = 4000
REQUEST_DELAY_SEC = 3

CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
VN_TZ = timezone(timedelta(hours=7))


def load_blocks():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("blocks", [])
        except Exception:
            return []
    return []


def load_data():
    """Trả về dict {block_id: {name, updated_at, articles}}."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d.get("blocks"), dict):
                return d["blocks"]
        except Exception:
            pass
    return {}


def save_data(blocks_data):
    payload = {
        "updated_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks_data,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def make_id(link):
    return hashlib.md5(link.encode("utf-8")).hexdigest()


def matches_keywords(text, keywords):
    t = (text or "").lower()
    return any((k or "").lower() in t for k in keywords)


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


def analyze(api_key, instructions, topics, title, content, max_retries=3):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)

    topic_line = (f'- "topic": chọn ĐÚNG MỘT trong: {topics}'
                  if topics else '- "topic": để là "Khác"')
    prompt = f"""{instructions}

Tiêu đề: {title}
Nội dung bài: {content}

Sau khi tóm tắt theo yêu cầu trên, hãy:
{topic_line}
- "impact": đánh giá tác động tới kinh tế / thị trường, chọn ĐÚNG MỘT trong: {IMPACTS}

Chỉ trả về JSON đúng định dạng: {{"summary": "...", "topic": "...", "impact": "..."}}"""

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            data = json.loads(resp.text)
            topic = data.get("topic", "Khác")
            if topics and topic not in topics:
                topic = topics[0]
            impact = data.get("impact", "Trung lập")
            if impact not in IMPACTS:
                impact = "Trung lập"
            return {"ok": True, "summary": data.get("summary", "").strip(),
                    "topic": topic, "impact": impact}
        except Exception as e:
            msg = str(e); low = msg.lower()
            if "resource_exhausted" in low or "429" in msg:
                return {"ok": False, "error": msg, "kind": "quota"}
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            return {"ok": False, "error": msg, "kind": "other"}


def process_block(api_key, block, bdata):
    """Xử lý một block, cập nhật bdata tại chỗ. Trả về (số_thêm, lỗi_nếu_có)."""
    topics = block.get("topics", [])
    rss = block.get("rss_feeds", {})
    instructions = block.get("prompt_instructions", "Tóm tắt tin trong 5-10 câu.")
    articles = bdata.get("articles", [])
    existing_ids = {a["id"] for a in articles}

    # Gom tin mới + lọc theo từ khóa
    candidates = []
    for source_name, url in rss.items():
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
            title = entry.get("title", "")
            rss_sum = entry.get("summary", "") or entry.get("description", "")
            if topics and not matches_keywords(title + " " + rss_sum, topics):
                continue   # không khớp từ khóa -> bỏ qua, không tốn lượt AI
            existing_ids.add(aid)
            candidates.append({"id": aid, "title": title or "(Không có tiêu đề)",
                               "source": source_name, "link": link,
                               "published": entry.get("published", ""),
                               "rss_summary": rss_sum})

    candidates = candidates[:MAX_NEW_PER_BLOCK]
    added = 0
    for art in candidates:
        full_text = get_full_text(art["link"], fallback=art["rss_summary"])
        result = analyze(api_key, instructions, topics, art["title"], full_text)
        if not result["ok"]:
            return added, result
        articles.insert(0, {
            "id": art["id"], "title": art["title"], "source": art["source"],
            "link": art["link"], "published": art["published"],
            "summary": result["summary"], "topic": result["topic"],
            "impact": result["impact"],
            "created_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
        })
        added += 1
        print(f"    + {art['title'][:55]}")
        time.sleep(REQUEST_DELAY_SEC)

    bdata["articles"] = articles[:MAX_STORE_PER_BLOCK]
    bdata["name"] = block.get("name", block.get("id"))
    bdata["updated_at"] = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M")
    return added, None


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("LỖI: chưa có GEMINI_API_KEY."); return

    force_all = os.environ.get("FORCE_ALL", "").lower() == "true"
    current_hour = datetime.now(VN_TZ).hour
    print(f"Giờ VN hiện tại: {current_hour}h. Chạy tất cả block: {force_all}")

    blocks = load_blocks()
    blocks_data = load_data()

    total_added = 0
    for block in blocks:
        bid = block.get("id")
        if not bid:
            continue
        due = force_all or (current_hour in block.get("update_hours", []))
        if not due:
            continue
        print(f"== Block '{block.get('name', bid)}' đang tới giờ, xử lý...")
        bdata = blocks_data.get(bid, {"articles": []})
        added, err = process_block(api_key, block, bdata)
        blocks_data[bid] = bdata
        total_added += added
        if err:
            print(f"  Dừng vì lỗi ({err.get('kind')}): {str(err.get('error'))[:120]}")
            break   # gặp lỗi quota thì dừng để không tốn thêm

    save_data(blocks_data)
    print(f"Xong. Tổng cộng thêm {total_added} tin.")


if __name__ == "__main__":
    main()
