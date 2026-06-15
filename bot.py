# -*- coding: utf-8 -*-
"""
BOT TELEGRAM (chạy mỗi giờ trên GitHub Actions)
- Đọc các tin nhắn mới gửi tới bot (qua getUpdates)
- Mỗi tin chứa JSON nhận định (kết quả từ Gem) -> lưu vào data.json
- Nhắn lại xác nhận cho người dùng
- Ghi nhớ offset (tin đã đọc) trong telegram_state.json để không xử lý lại

Secrets cần có: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone, timedelta

import requests

DATA_FILE = "data.json"
SUGGEST_FILE = "suggestions.json"
STATE_FILE = "telegram_state.json"
CONFIG_FILE = "config.json"
VN_TZ = timezone(timedelta(hours=7))

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
API = f"https://api.telegram.org/bot{TOKEN}"

IMPACTS = ["Tích cực", "Trung lập", "Tiêu cực"]
REGIONS = ["Việt Nam", "Mỹ", "Châu Âu", "Trung Quốc", "Khác"]


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send(text):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(f"{API}/sendMessage", timeout=20,
                      json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True})
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")


def get_updates(offset):
    try:
        r = requests.get(f"{API}/getUpdates", timeout=40,
                         params={"offset": offset, "timeout": 0})
        if r.status_code == 200:
            return r.json().get("result", [])
        print(f"getUpdates lỗi {r.status_code}: {r.text[:150]}")
    except Exception as e:
        print(f"Lỗi getUpdates: {e}")
    return []


# ---- Xử lý nội dung (giống app: tách video/bài viết) ----

def extract_video_id(link):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([0-9A-Za-z_-]{11})", link or "")
    return m.group(1) if m else None


def post_id(url):
    u = (url or "").split("?")[0].rstrip("/")
    return "p" + hashlib.md5(u.encode("utf-8")).hexdigest()[:15]


def content_key(link):
    vid = extract_video_id(link)
    if vid:
        return vid, f"https://youtu.be/{vid}", True
    l = (link or "").strip()
    if l.startswith("http"):
        return post_id(l), l.split("?")[0].rstrip("/"), False
    return None, None, False


def ts_to_seconds(ts):
    parts = str(ts).split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        data = json.loads(t)
    except Exception:
        fa, fo = t.find("["), t.find("{")
        if fa == -1 and fo == -1:
            return None
        if fo == -1 or (fa != -1 and fa < fo):
            s = t[fa: t.rfind("]") + 1]
        else:
            s = t[fo: t.rfind("}") + 1]
        try:
            data = json.loads(s)
        except Exception:
            return None
    if isinstance(data, dict):
        for key in ("videos", "results", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data if isinstance(data, list) else None


def build_insights(parsed, topics):
    now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M")
    new_videos, new_insights = {}, []
    for item in parsed:
        link = item.get("video", "") or item.get("link", "") or item.get("url", "")
        key, url, is_video = content_key(link)
        if not key:
            continue
        channel = (item.get("channel") or "(không rõ kênh)").strip()
        title = (item.get("title") or "").strip()
        posted = (item.get("posted_at") or item.get("published") or "").strip()
        new_videos[key] = {"channel": channel, "title": title, "published": posted, "url": url}
        for ins in item.get("insights", []):
            if not ins.get("content"):
                continue
            topic = ins.get("topic", "")
            if topics and topic not in topics:
                topic = topics[-1]
            impact = ins.get("impact", "Trung lập")
            if impact not in IMPACTS:
                impact = "Trung lập"
            region = ins.get("region", "Việt Nam")
            if region not in REGIONS:
                region = "Việt Nam"
            ts = ins.get("timestamp", "") if is_video else ""
            raw = key + topic + ins.get("content", "")[:30]
            new_insights.append({
                "id": hashlib.md5(raw.encode("utf-8")).hexdigest(),
                "video_id": key, "channel": channel,
                "expert": (ins.get("expert", "") or channel).strip(),
                "topic": topic, "content": ins.get("content", "").strip(),
                "impact": impact, "region": region, "video_timestamp": ts,
                "video_url_at": url + (f"?t={ts_to_seconds(ts)}" if (ts and is_video) else ""),
                "video_title": title, "video_url": url,
                "posted_at": posted, "refers_to": ins.get("refers_to", "").strip(),
                "source": "telegram", "created_at": now_str})
    return new_insights, new_videos


def main():
    print(f"TELEGRAM_TOKEN có: {'CÓ' if TOKEN else 'KHÔNG'} | "
          f"TELEGRAM_CHAT_ID: {'CÓ (' + CHAT_ID + ')' if CHAT_ID else 'KHÔNG'}")
    if not TOKEN or not CHAT_ID:
        print("=> Thiếu secret TELEGRAM_TOKEN / TELEGRAM_CHAT_ID trong repo. Bỏ qua.")
        return

    # Kiểm tra bot sống và token đúng
    try:
        me = requests.get(f"{API}/getMe", timeout=20)
        print(f"getMe: HTTP {me.status_code} - {me.text[:120]}")
    except Exception as e:
        print(f"Lỗi gọi getMe (token sai?): {e}")
        return

    cfg = load_json(CONFIG_FILE, {})
    topics = [t["name"] if isinstance(t, dict) else t for t in cfg.get("topics", [])]

    state = load_json(STATE_FILE, {"offset": 0})
    offset = state.get("offset", 0)
    print(f"Offset đã lưu: {offset}")
    updates = get_updates(offset)
    print(f"Số tin nhận được: {len(updates)}")
    if not updates:
        print("Không có tin mới (đã đọc hết, hoặc chưa ai nhắn, hoặc tin >24h đã hết hạn).")
        return

    data = load_json(DATA_FILE, {"videos": {}, "insights": []})
    videos = data.get("videos", {})
    insights = data.get("insights", [])
    sugg = load_json(SUGGEST_FILE, {"items": {}})
    sugg_items = sugg.get("items", {})

    max_update_id = offset
    total_added, total_videos, changed = 0, 0, False

    for up in updates:
        max_update_id = max(max_update_id, up.get("update_id", 0) + 1)
        msg = up.get("message") or up.get("channel_post") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "") or ""
        print(f"  Tin từ chat {chat}: {text[:40]!r}")
        if chat != CHAT_ID:
            print(f"    -> Bỏ qua (chat {chat} khác CHAT_ID {CHAT_ID}).")
            continue
        if not text.strip():
            continue
        if text.strip() in ("/start", "/help"):
            send("Xin chào! Gửi cho tôi JSON nhận định (kết quả từ Gem) là tôi lưu vào dashboard. "
                 "Mỗi tin một bài/video.")
            continue

        parsed = parse_json(text)
        if not parsed:
            send("⚠️ Không đọc được JSON trong tin này. Hãy gửi đúng phần JSON từ Gem.")
            continue
        ni, nv = build_insights(parsed, topics)
        if not ni:
            send("⚠️ JSON hợp lệ nhưng không có nhận định nào (thiếu trường 'video' hoặc 'insights'?).")
            continue

        # Ghi đè theo video/bài
        new_keys = set(nv.keys())
        videos.update(nv)
        for k in new_keys:
            txt = sugg_items.get(k, {}).get("content", "")
            if txt:
                videos[k]["post_text"] = txt
        insights = [i for i in insights if i.get("video_id") not in new_keys] + ni
        # Dọn khỏi gợi ý
        for k in new_keys:
            sugg_items.pop(k, None)
        total_added += len(ni)
        total_videos += len(nv)
        changed = True
        titles = ", ".join(nv[k].get("title", "") or k for k in nv)[:120]
        send(f"✅ Đã lưu {len(ni)} nhận định cho: {titles}")

    state["offset"] = max_update_id
    save_json(STATE_FILE, state)

    if changed:
        data["videos"] = videos
        data["insights"] = insights
        data["updated_at"] = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M") + " (telegram)"
        save_json(DATA_FILE, data)
        sugg["items"] = sugg_items
        save_json(SUGGEST_FILE, sugg)
        print(f"Đã thêm {total_added} nhận định / {total_videos} nội dung từ Telegram.")
    else:
        print("Không có nội dung hợp lệ để lưu.")


if __name__ == "__main__":
    main()
