# -*- coding: utf-8 -*-
"""
fetch.py — LUỒNG TỰ ĐỘNG (GitHub Actions).
Với mỗi kênh YouTube cấu hình sẵn: liệt kê video mới -> lấy transcript ->
AI bóc thành các nhận định theo chủ đề -> lưu vào data.json.

Biến môi trường: GEMINI_API_KEY (bắt buộc), FORCE_ALL ("true" = chạy bất kể giờ).
Thư viện: feedparser, google-genai, youtube-transcript-api, requests
"""

import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone, timedelta

import requests
import feedparser

DEFAULT_MODEL = "gemini-2.5-flash-lite"
DEFAULT_PROMPT = ("Bạn là trợ lý phân tích kinh tế. Đọc bản ghi (có mốc thời gian) của "
                  "video và rút ra các NHẬN ĐỊNH kinh tế quan trọng, tóm tắt mỗi nhận "
                  "định 2-4 câu, nêu rõ số liệu nếu có.")

MAX_VIDEOS_PER_CHANNEL = 3     # mỗi kênh xử lý tối đa bao nhiêu video mới mỗi lần
MAX_TRANSCRIPT_CHARS = 12000   # cắt transcript để giới hạn token
MAX_STORE_INSIGHTS = 1000
REQUEST_DELAY_SEC = 3

CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
VN_TZ = timezone(timedelta(hours=7))


# ---------------- Cấu hình & dữ liệu ----------------

def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    channels = cfg.get("channels", [])
    topics_raw = cfg.get("topics", [])
    model = cfg.get("model") or os.environ.get("GEMINI_MODEL", "") or DEFAULT_MODEL
    prompt = cfg.get("prompt_instructions") or DEFAULT_PROMPT
    update_hours = cfg.get("update_hours", [])
    return channels, topics_raw, model, prompt, update_hours


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("videos", {}), d.get("insights", [])
        except Exception:
            pass
    return {}, []


def save_data(videos, insights):
    payload = {"updated_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
               "videos": videos, "insights": insights[:MAX_STORE_INSIGHTS]}
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def names_of(topics_raw):
    return [t["name"] if isinstance(t, dict) else t for t in topics_raw]


def build_topic_guide(topics_raw):
    lines = []
    for t in topics_raw:
        if isinstance(t, dict):
            kw = t.get("keywords", [])
            lines.append(f"- {t['name']}" + (f" (từ khóa: {', '.join(kw)})" if kw else ""))
        else:
            lines.append(f"- {t}")
    return "\n".join(lines)


# ---------------- YouTube: kênh -> video ----------------

def resolve_channel_id(url):
    """Tìm channel_id (UC...) từ link kênh."""
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", url)
    if m:
        return m.group(1)
    try:
        r = requests.get(url.split("?")[0], timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r'"channelId":"(UC[0-9A-Za-z_-]{22})"', r.text)
        if m:
            return m.group(1)
        m = re.search(r'/channel/(UC[0-9A-Za-z_-]{22})', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def list_channel_videos(url, limit):
    """Trả về list video gần đây: {id, title, published, url}."""
    cid = resolve_channel_id(url)
    if not cid:
        return []
    feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid
    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        return []
    out = []
    for e in feed.entries[:limit]:
        vid = getattr(e, "yt_videoid", None) or (e.get("id", "").split(":")[-1])
        if not vid:
            continue
        out.append({"id": vid, "title": e.get("title", ""),
                    "published": e.get("published", ""),
                    "url": "https://youtu.be/" + vid})
    return out


def get_transcript_text(video_id):
    """Trả về transcript có mốc thời gian, hoặc None nếu không có."""
    langs = ["vi", "en"]
    segs = None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=langs)
        segs = [(getattr(s, "start", 0), getattr(s, "text", "")) for s in fetched]
    except Exception:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            data = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            segs = [(d.get("start", 0), d.get("text", "")) for d in data]
        except Exception:
            return None
    if not segs:
        return None
    lines = []
    for start, text in segs:
        m, s = int(start // 60), int(start % 60)
        lines.append(f"[{m}:{s:02d}] {text}")
    return "\n".join(lines)[:MAX_TRANSCRIPT_CHARS]


# ---------------- AI: transcript -> nhận định ----------------

def analyze(api_key, model, instructions, topic_guide, title, transcript, max_retries=3):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)

    prompt = f"""{instructions}

Tiêu đề video: {title}
Bản ghi (mỗi dòng có mốc thời gian [phút:giây]):
{transcript}

Hãy bóc các nhận định. Mỗi nhận định gồm:
- expert: tên chuyên gia phát biểu; nếu không rõ tên thì ghi "(chủ kênh)".
- topic: chọn ĐÚNG MỘT chủ đề trong danh sách sau (chép đúng TÊN, phần trước dấu ngoặc):
{topic_guide}
- content: tóm tắt nhận định 2-4 câu, nêu rõ số liệu nếu có.
- impact: đánh giá tác động tới kinh tế/thị trường, chọn ĐÚNG MỘT: "Tích cực", "Trung lập", "Tiêu cực".
- timestamp: mốc thời gian dạng mm:ss nơi nói nhận định đó.
- refers_to: thời điểm/giai đoạn nhận định nói tới (vd "Quý 3/2026"); không rõ để "".

Chỉ trả về JSON: {{"insights": [{{"expert":"...","topic":"...","content":"...","impact":"...","timestamp":"mm:ss","refers_to":"..."}}]}}"""

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            data = json.loads(resp.text)
            return {"ok": True, "insights": data.get("insights", [])}
        except Exception as e:
            msg = str(e); low = msg.lower()
            if "resource_exhausted" in low or "429" in msg:
                return {"ok": False, "kind": "quota", "error": msg}
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            return {"ok": False, "kind": "other", "error": msg}


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


def build_insight(video, channel_name, ins, topics):
    topic = ins.get("topic", "")
    if topic not in topics:
        topic = topics[-1] if topics else "Khác"
    impact = ins.get("impact", "Trung lập")
    if impact not in ("Tích cực", "Trung lập", "Tiêu cực"):
        impact = "Trung lập"
    ts = ins.get("timestamp", "")
    url_at = video["url"] + (f"?t={ts_to_seconds(ts)}" if ts else "")
    raw = video["id"] + topic + ins.get("content", "")[:30]
    return {
        "id": hashlib.md5(raw.encode("utf-8")).hexdigest(),
        "video_id": video["id"], "channel": channel_name,
        "expert": ins.get("expert", "").strip() or channel_name,
        "topic": topic, "content": ins.get("content", "").strip(),
        "impact": impact,
        "video_timestamp": ts, "video_url_at": url_at,
        "video_title": video.get("title", ""), "video_url": video["url"],
        "posted_at": video.get("published", ""),
        "refers_to": ins.get("refers_to", "").strip(),
        "source": "tự động",
        "created_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"),
    }


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("LỖI: chưa có GEMINI_API_KEY."); return

    force_all = os.environ.get("FORCE_ALL", "").lower() == "true"
    hour = datetime.now(VN_TZ).hour
    channels, topics_raw, model, instructions, update_hours = load_config()
    topic_names = names_of(topics_raw)
    topic_guide = build_topic_guide(topics_raw)

    if not (force_all or hour in update_hours):
        print(f"Giờ VN {hour}h không nằm trong lịch {update_hours}. Bỏ qua.")
        return

    videos, insights = load_data()
    print(f"Model: {model} | {len(channels)} kênh | đã có {len(insights)} nhận định")

    total_new = 0
    stop = False
    for ch in channels:
        if stop:
            break
        ch_name = ch.get("name", ch.get("id"))
        vids = list_channel_videos(ch.get("url", ""), MAX_VIDEOS_PER_CHANNEL)
        print(f"== {ch_name}: thấy {len(vids)} video gần đây")
        for v in vids:
            if v["id"] in videos:
                continue   # đã xử lý
            transcript = get_transcript_text(v["id"])
            if not transcript:
                print(f"  (bỏ qua, không có transcript) {v['title'][:50]}")
                videos[v["id"]] = {"channel": ch_name, "title": v["title"],
                                   "published": v["published"], "url": v["url"],
                                   "no_transcript": True}
                continue
            result = analyze(api_key, model, instructions, topic_guide, v["title"], transcript)
            if not result["ok"]:
                print(f"  Dừng vì lỗi ({result.get('kind')}): {str(result.get('error'))[:100]}")
                stop = True
                break
            n = 0
            for ins in result["insights"]:
                if not ins.get("content"):
                    continue
                insights.insert(0, build_insight(v, ch_name, ins, topic_names))
                n += 1
            videos[v["id"]] = {"channel": ch_name, "title": v["title"],
                               "published": v["published"], "url": v["url"]}
            total_new += n
            print(f"  + {n} nhận định từ: {v['title'][:50]}")
            time.sleep(REQUEST_DELAY_SEC)

    save_data(videos, insights)
    print(f"Xong. Thêm {total_new} nhận định mới.")


if __name__ == "__main__":
    main()
