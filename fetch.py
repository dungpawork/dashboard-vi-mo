# -*- coding: utf-8 -*-
"""
fetch.py — robot lấy tin theo MÔ HÌNH GỘP (mỗi bài chỉ gọi AI một lần).
Chạy mỗi giờ. Mỗi lần thức:
  - đọc config.json (prompt chung + danh sách block; mỗi block có giờ riêng)
  - xác định block "tới giờ" (hoặc tất cả nếu bấm Run workflow tay)
  - quét GỘP tất cả nguồn của các block đó MỘT LẦN, loại trùng
  - lọc theo từ khóa (bỏ bài rác, không tốn lượt AI)
  - mỗi bài còn lại gọi AI MỘT LẦN: vừa tóm tắt, vừa chọn block phù hợp nhất
  - lưu vào data.json theo block.

Biến môi trường: GEMINI_API_KEY (bắt buộc), FORCE_ALL ("true" = chạy mọi block).
"""

import os
import time
import json
import hashlib
from datetime import datetime, timezone, timedelta

import feedparser

IMPACTS = ["Tích cực", "Tiêu cực", "Trung lập"]
DEFAULT_PROMPT = ("Tóm tắt CỐT LÕI bài viết trong 5 đến 10 câu ngắn gọn bằng tiếng Việt, "
                  "nêu rõ số liệu nếu có.")
DEFAULT_MODEL = "gemini-2.5-flash-lite"

MAX_NEW_PER_RUN = 20        # tổng số bài gọi AI mỗi lần chạy (giới hạn quota)
MAX_STORE_PER_BLOCK = 100
MAX_CONTENT_CHARS = 4000
REQUEST_DELAY_SEC = 3

CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
VN_TZ = timezone(timedelta(hours=7))


def load_config():
    blocks, prompt, model = [], DEFAULT_PROMPT, ""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                c = json.load(f)
            blocks = c.get("blocks", [])
            prompt = c.get("prompt_instructions") or DEFAULT_PROMPT
            model = c.get("model") or ""
        except Exception:
            pass
    # Ưu tiên model trong config; nếu trống thì lấy biến môi trường; cuối cùng là mặc định.
    model = model or os.environ.get("GEMINI_MODEL", "") or DEFAULT_MODEL
    return blocks, prompt, model


def load_data():
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
    payload = {"updated_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
               "blocks": blocks_data}
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


def analyze(api_key, model, instructions, active_blocks, title, content, max_retries=3):
    """Một lần gọi AI: tóm tắt + chọn block + đánh giá tác động."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)

    block_lines = "\n".join(
        f"- {b['name']}: {', '.join(b.get('topics', []))}" for b in active_blocks)
    names = [b["name"] for b in active_blocks]

    prompt = f"""{instructions}

Tiêu đề: {title}
Nội dung bài: {content}

Dưới đây là các nhóm tin và từ khóa của chúng:
{block_lines}

Hãy trả về JSON gồm:
- "summary": bản tóm tắt theo yêu cầu trên.
- "block": tên nhóm phù hợp nhất, chép ĐÚNG MỘT tên trong: {names}
- "impact": tác động tới kinh tế/thị trường, chọn ĐÚNG MỘT trong: {IMPACTS}

Chỉ trả JSON: {{"summary": "...", "block": "...", "impact": "..."}}"""

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            data = json.loads(resp.text)
            impact = data.get("impact", "Trung lập")
            if impact not in IMPACTS:
                impact = "Trung lập"
            return {"ok": True, "summary": data.get("summary", "").strip(),
                    "block": data.get("block", ""), "impact": impact}
        except Exception as e:
            msg = str(e); low = msg.lower()
            if "resource_exhausted" in low or "429" in msg:
                return {"ok": False, "error": msg, "kind": "quota"}
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            return {"ok": False, "error": msg, "kind": "other"}


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("LỖI: chưa có GEMINI_API_KEY."); return

    force_all = os.environ.get("FORCE_ALL", "").lower() == "true"
    hour = datetime.now(VN_TZ).hour
    blocks, instructions, model = load_config()
    blocks_data = load_data()
    print(f"Dùng model: {model}")

    active = [b for b in blocks if force_all or hour in b.get("update_hours", [])]
    print(f"Giờ VN {hour}h. Chạy tất cả: {force_all}. "
          f"Số block tới giờ: {len(active)}")
    if not active:
        save_data(blocks_data)
        print("Không có block nào tới giờ."); return

    name_to_id = {b["name"]: b["id"] for b in active}

    # Tất cả id đã có (trên MỌI block) -> mỗi bài chỉ xử lý một lần duy nhất
    existing_ids = set()
    for bd in blocks_data.values():
        for a in bd.get("articles", []):
            existing_ids.add(a["id"])

    # Quét GỘP các nguồn của block đang tới giờ, loại trùng nguồn theo URL
    feeds = {}
    for b in active:
        for name, url in b.get("rss_feeds", {}).items():
            feeds.setdefault(url, name)

    candidates = []
    for url, source_name in feeds.items():
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
            matched = [b for b in active
                       if matches_keywords(title + " " + rss_sum, b.get("topics", []))]
            if not matched:
                continue   # không khớp từ khóa block nào -> bỏ, không tốn AI
            existing_ids.add(aid)
            candidates.append({"id": aid, "title": title or "(Không tiêu đề)",
                               "source": source_name, "link": link,
                               "published": entry.get("published", ""),
                               "rss_summary": rss_sum, "fallback_id": matched[0]["id"]})

    candidates = candidates[:MAX_NEW_PER_RUN]
    print(f"Có {len(candidates)} bài mới khớp từ khóa để xử lý.")

    added = 0
    for art in candidates:
        full_text = get_full_text(art["link"], fallback=art["rss_summary"])
        result = analyze(api_key, model, instructions, active, art["title"], full_text)
        if not result["ok"]:
            print(f"Dừng vì lỗi ({result.get('kind')}): {str(result.get('error'))[:120]}")
            break
        bid = name_to_id.get(result["block"], art["fallback_id"])
        bdata = blocks_data.setdefault(bid, {"articles": []})
        block_name = next((b["name"] for b in active if b["id"] == bid), bid)
        bdata.setdefault("articles", []).insert(0, {
            "id": art["id"], "title": art["title"], "source": art["source"],
            "link": art["link"], "published": art["published"],
            "summary": result["summary"], "topic": block_name,
            "impact": result["impact"],
            "created_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
        })
        bdata["articles"] = bdata["articles"][:MAX_STORE_PER_BLOCK]
        bdata["name"] = block_name
        bdata["updated_at"] = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M")
        added += 1
        print(f"  + [{block_name}] {art['title'][:50]}")
        time.sleep(REQUEST_DELAY_SEC)

    save_data(blocks_data)
    print(f"Xong. Thêm {added} tin.")


if __name__ == "__main__":
    main()
