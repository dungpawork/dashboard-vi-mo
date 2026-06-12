# -*- coding: utf-8 -*-
"""
ROBOT GỢI Ý VIDEO (chạy trên GitHub Actions)
1) Quét RSS các kênh YouTube nguồn (trong config.json)
2) Với video MỚI (chưa xử lý, chưa gợi ý), nhờ Gemini phân loại TIÊU ĐỀ
   vào 1 trong các chủ đề — hoặc "Không phù hợp"
3) Ghi kết quả vào suggestions.json để trang Nhập tay hiển thị cho người chọn

Không lấy transcript, không tạo nhận định — phần đó người dùng làm qua app Gemini.
"""

import os
import re
import json
import time

import requests
import feedparser

CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
SUGGEST_FILE = "suggestions.json"
NOT_SUITABLE = "Không phù hợp"
MAX_PER_CHANNEL = 10


def now_vn():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=7)))


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def load_config():
    cfg = load_json(CONFIG_FILE, {})
    topics = [t["name"] if isinstance(t, dict) else t for t in cfg.get("topics", [])]
    kw = {}
    for t in cfg.get("topics", []):
        if isinstance(t, dict):
            kw[t["name"]] = t.get("keywords", [])
    return (cfg.get("channels", []), cfg.get("substacks", []), topics, kw,
            cfg.get("model", "gemini-2.5-flash-lite"), cfg.get("update_hours", []))


def post_id(url):
    import hashlib
    u = (url or "").split("?")[0].rstrip("/")
    return "p" + hashlib.md5(u.encode("utf-8")).hexdigest()[:15]


HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
           "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"}
COOKIES = {"CONSENT": "YES+1", "SOCS": "CAI"}


def resolve_channel_id(url):
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", url or "")
    if m:
        print("    Mã kênh có sẵn trong link.")
        return m.group(1)
    try:
        r = requests.get((url or "").split("?")[0], timeout=20,
                         headers=HEADERS, cookies=COOKIES)
        print(f"    Tải trang kênh: HTTP {r.status_code}, {len(r.text)} ký tự")
        # YouTube đổi cách ghi mã kênh theo thời gian — thử nhiều mẫu
        patterns = [
            r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"',
            r'"externalId":"(UC[0-9A-Za-z_-]{22})"',
            r'"browseId":"(UC[0-9A-Za-z_-]{22})"',
            r'"channelId":"(UC[0-9A-Za-z_-]{22})"',
            r'youtube\.com/channel/(UC[0-9A-Za-z_-]{22})',
            r'\b(UC[0-9A-Za-z_-]{22})\b',
        ]
        for p in patterns:
            m = re.search(p, r.text)
            if m:
                print(f"    Tìm thấy mã kênh: {m.group(1)}")
                return m.group(1)
        print("    KHÔNG thấy mã kênh trong trang dù trang tải được.")
    except Exception as e:
        print(f"    Lỗi tải trang kênh: {e}")
    return None


def substack_feed_url(url):
    """Tìm địa chỉ RSS của bản tin Substack từ link cấu hình."""
    u = (url or "").split("?")[0].rstrip("/")
    if u.endswith("/feed"):
        return u
    # Link hồ sơ tác giả (substack.com/@ten) -> dò bản tin trong trang
    if "substack.com/@" in u:
        try:
            r = requests.get(u, timeout=20, headers=HEADERS)
            print(f"    Tải trang hồ sơ: HTTP {r.status_code}, {len(r.text)} ký tự")
            doms = re.findall(r'https?://([a-zA-Z0-9-]+)\.substack\.com', r.text)
            doms = [d for d in doms if d not in ("www", "substack", "open", "api", "cdn", "support")]
            if doms:
                best = max(set(doms), key=doms.count)
                print(f"    Dò được bản tin: {best}.substack.com")
                return f"https://{best}.substack.com/feed"
            print("    KHÔNG dò được bản tin từ trang hồ sơ — hãy dùng link dạng ten.substack.com")
        except Exception as e:
            print(f"    Lỗi tải trang hồ sơ: {e}")
        return None
    return u + "/feed"


def list_substack_posts(url, limit=MAX_PER_CHANNEL):
    feed_url = substack_feed_url(url)
    if not feed_url:
        return []
    try:
        r = requests.get(feed_url, timeout=20, headers=HEADERS)
        print(f"    Tải RSS: HTTP {r.status_code}, {len(r.text)} ký tự")
        if r.status_code != 200:
            print("    => RSS bị từ chối.")
            return []
        feed = feedparser.parse(r.text)
    except Exception as e:
        print(f"    Lỗi tải RSS: {e}")
        return []
    out = []
    for e in feed.entries[:limit]:
        link = (e.get("link", "") or "").split("?")[0]
        if not link:
            continue
        pub = ""
        if e.get("published_parsed"):
            p = e["published_parsed"]
            pub = f"{p.tm_year:04d}-{p.tm_mon:02d}-{p.tm_mday:02d}"
        out.append({"id": post_id(link), "title": e.get("title", ""),
                    "published": pub, "url": link})
    return out


def list_channel_videos(url, limit=MAX_PER_CHANNEL):
    cid = resolve_channel_id(url)
    if not cid:
        print("    => Bỏ qua kênh (không có mã kênh). Mẹo: dùng link dạng "
              "youtube.com/channel/UC... trong Cấu hình để khỏi cần bước này.")
        return []
    feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid
    try:
        r = requests.get(feed_url, timeout=20, headers=HEADERS, cookies=COOKIES)
        print(f"    Tải RSS: HTTP {r.status_code}, {len(r.text)} ký tự")
        if r.status_code != 200:
            print("    => RSS bị từ chối (khả năng YouTube chặn máy chủ GitHub).")
            return []
        feed = feedparser.parse(r.text)
    except Exception as e:
        print(f"    Lỗi tải RSS: {e}")
        return []
    out = []
    for e in feed.entries[:limit]:
        vid = getattr(e, "yt_videoid", None) or e.get("id", "").split(":")[-1]
        if vid:
            out.append({"id": vid, "title": e.get("title", ""),
                        "published": (e.get("published", "") or "")[:10],
                        "url": "https://youtu.be/" + vid})
    return out


def build_classify_prompt(items, topics, kw):
    guide = []
    for t in topics:
        ks = kw.get(t, [])
        guide.append(f"- {t}" + (f" (từ khóa: {', '.join(ks)})" if ks else ""))
    lines = [f"{it['id']} | {it['title']}" for it in items]
    return (
        "Bạn là trợ lý phân loại video kinh tế. Dưới đây là danh sách video dạng `mã | tiêu đề`.\n"
        "Với MỖI video, chỉ dựa trên TIÊU ĐỀ, chọn ĐÚNG MỘT chủ đề phù hợp nhất trong danh sách:\n"
        + "\n".join(guide) + "\n"
        f"Nếu tiêu đề không liên quan các chủ đề trên (giải trí, quảng cáo, kỹ năng cá nhân, "
        f"chứng khoán riêng lẻ không vĩ mô...), trả \"{NOT_SUITABLE}\".\n\n"
        "CHỈ trả về JSON: {\"<mã>\": \"<tên chủ đề>\", ...}\n\nDanh sách:\n" + "\n".join(lines))


def classify(items, topics, kw, model):
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = build_classify_prompt(items, topics, kw)
    last_err = None
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            text = (resp.text or "").strip()
            text = re.sub(r"^```[a-zA-Z]*", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
            f = text.find("{")
            data = json.loads(text[f: text.rfind("}") + 1])
            return {str(k): str(v).strip() for k, v in data.items()}
        except Exception as e:
            last_err = e
            msg = str(e)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                print(f"    Hết hạn mức AI (lần {attempt+1}), chờ 30s...")
                time.sleep(30)
            else:
                print(f"    Lỗi AI (lần {attempt+1}): {msg[:150]}")
                time.sleep(5)
    raise RuntimeError(f"Gemini thất bại sau 3 lần: {last_err}")


def main():
    channels, substacks, topics, kw, model, update_hours = load_config()
    if (not channels and not substacks) or not topics:
        print("Thiếu nguồn hoặc chủ đề trong config.json — dừng.")
        return

    force = os.environ.get("FORCE_ALL", "") == "1"
    hour = now_vn().hour
    if update_hours and hour not in update_hours and not force:
        print(f"Giờ hiện tại {hour}h không nằm trong lịch {update_hours} — bỏ qua.")
        return

    data = load_json(DATA_FILE, {})
    known_videos = set(data.get("videos", {}).keys())
    sugg = load_json(SUGGEST_FILE, {})
    items = sugg.get("items", {})

    # Thu thập nội dung mới từ các nguồn
    fresh = []
    for ch in channels:
        name, url = ch.get("name", ""), ch.get("url", "")
        print(f"Kênh YouTube: {name}")
        vids = list_channel_videos(url)
        print(f"  RSS trả về {len(vids)} video")
        for v in vids:
            if v["id"] in known_videos or v["id"] in items:
                continue
            v["channel"] = name
            v["type"] = "video"
            fresh.append(v)
    for sb in substacks:
        name, url = sb.get("name", ""), sb.get("url", "")
        print(f"Substack: {name}")
        posts = list_substack_posts(url)
        print(f"  RSS trả về {len(posts)} bài")
        for p in posts:
            if p["id"] in known_videos or p["id"] in items:
                continue
            p["channel"] = name
            p["type"] = "post"
            fresh.append(p)
    print(f"Tổng nội dung mới cần phân loại: {len(fresh)}")

    if fresh:
        labels = classify(fresh, topics, kw, model)
        for v in fresh:
            topic = labels.get(v["id"], NOT_SUITABLE)
            if topic not in topics and topic != NOT_SUITABLE:
                topic = NOT_SUITABLE
            items[v["id"]] = {"title": v["title"], "channel": v["channel"],
                              "published": v["published"], "url": v["url"],
                              "type": v.get("type", "video"),
                              "topic": topic, "status": "gợi ý"}
            print(f"  + [{topic}] {v['title'][:60]}")

    # Dọn: video đã xử lý (đã có trong data.json) thì bỏ khỏi danh sách gợi ý
    before = len(items)
    items = {k: v for k, v in items.items() if k not in known_videos}
    if before != len(items):
        print(f"Dọn {before - len(items)} gợi ý đã xử lý.")

    sugg = {"updated_at": now_vn().strftime("%Y-%m-%d %H:%M"), "items": items}
    with open(SUGGEST_FILE, "w", encoding="utf-8") as f:
        json.dump(sugg, f, ensure_ascii=False, indent=2)
    print(f"Đã ghi {SUGGEST_FILE}: {len(items)} mục.")


if __name__ == "__main__":
    main()
