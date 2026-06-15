# -*- coding: utf-8 -*-
"""
DASHBOARD NHẬN ĐỊNH CHUYÊN GIA (YouTube)
Xem (góc trên trái):   📊 Nhận định, 🧑‍💼 Chuyên gia
Công cụ (góc dưới, cần mật khẩu): ✍️ Nhập tay, 🗂️ Quản lý nguồn, 👤 Quản lý chuyên gia, ⚙️ Cấu hình
Secrets: ADMIN_PASSWORD, GH_TOKEN, GH_REPO ; Thư viện: streamlit, requests, feedparser, Pillow
"""

import os
import re
import io
import csv
import json
import html
import base64
import hashlib

import requests
import feedparser
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timezone, timedelta

VN_TZ = timezone(timedelta(hours=7))
CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
EXPERTS_FILE = "experts.json"
SUGGEST_FILE = "suggestions.json"
DEFAULT_MODEL = "gemini-2.5-flash-lite"
GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.5-flash",
                 "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]
IMPACTS = ["Tích cực", "Trung lập", "Tiêu cực"]
REGIONS = ["Việt Nam", "Mỹ", "Châu Âu", "Trung Quốc", "Khác"]

THEMES = {
    "light": {"bg": "#faf8f4", "card": "#ffffff", "border": "#ece6db", "text": "#2b2b2b",
              "muted": "#9a9286", "heading": "#16243a", "hfont": "'Lora',Georgia,serif",
              "sidebar": "#f3efe8", "neu": "#cbd5e1", "pos": "#15803d", "neg": "#dc2626",
              "accent": "#16243a", "shadow": "0 4px 14px rgba(40,30,10,.05)", "radius": "12px"},
    "dark": {"bg": "#0e141b", "card": "#131b24", "border": "#202c3a", "text": "#cdd6e3",
             "muted": "#7d8aa0", "heading": "#ffffff", "hfont": "'Inter',sans-serif",
             "sidebar": "#0b1118", "neu": "#3a4759", "pos": "#34d399", "neg": "#f87171",
             "accent": "#2b6cb0", "shadow": "none", "radius": "10px"},
}


def TH():
    try:
        return THEMES[st.session_state.get("ui_theme", "light")]
    except Exception:
        return THEMES["light"]
DEFAULT_AUTO_PROMPT = ("Bạn là trợ lý phân tích kinh tế. Đọc bản ghi (có mốc thời gian) của video "
                       "và rút ra các NHẬN ĐỊNH kinh tế, tóm tắt 2-4 câu, đánh giá tác động "
                       "(Tích cực/Trung lập/Tiêu cực) và khu vực (Việt Nam/Mỹ/Châu Âu/Trung Quốc/Khác). "
                       "Ưu tiên nhận định DỰ BÁO TƯƠNG LAI và SO SÁNH mục tiêu/kế hoạch; không có mới lấy hiện trạng.")
DEFAULT_MANUAL_TEMPLATE = (
    "Bạn là trợ lý phân tích kinh tế. Với MỖI link video, xem video và rút ra các nhận định kinh tế "
    "quan trọng. Ưu tiên nhận định DỰ BÁO TƯƠNG LAI và SO SÁNH mục tiêu/kế hoạch; không có mới lấy hiện trạng.\n\n"
    "Phân loại mỗi nhận định vào ĐÚNG MỘT chủ đề trong danh sách sau:\n{topics}\n\n"
    "Mỗi video trả về: video, channel, title (tiêu đề video), posted_at (YYYY-MM-DD), insights[]. Mỗi nhận định: expert, "
    "region (Việt Nam/Mỹ/Châu Âu/Trung Quốc/Khác), topic (chép ĐÚNG TÊN ở trên), content (2-4 câu), "
    "impact (Tích cực/Trung lập/Tiêu cực), timestamp (mm:ss), refers_to (vd \"Quý 3/2026\").\n\n"
    "CHỈ trả về JSON:\n[{\"video\":\"<link>\",\"channel\":\"...\",\"title\":\"...\",\"posted_at\":\"...\",\"insights\":"
    "[{\"expert\":\"...\",\"region\":\"...\",\"topic\":\"...\",\"content\":\"...\",\"impact\":\"...\","
    "\"timestamp\":\"mm:ss\",\"refers_to\":\"...\"}]}]\n\nDanh sách video:\n{links}")


# ==================== Cấu hình & dữ liệu ====================

def _read_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def load_config():
    cfg = _read_json(CONFIG_FILE, {})
    return {
        "model": cfg.get("model", DEFAULT_MODEL),
        "channels": cfg.get("channels", []),
        "substacks": cfg.get("substacks", []),
        "topics": cfg.get("topics", []),
        "update_hours": cfg.get("update_hours", []),
        "prompt_instructions": cfg.get("prompt_instructions", DEFAULT_AUTO_PROMPT),
        "manual_prompt_template": cfg.get("manual_prompt_template", DEFAULT_MANUAL_TEMPLATE),
    }


def load_data():
    d = _read_json(DATA_FILE, {})
    return d.get("videos", {}), d.get("insights", []), d.get("updated_at", "")


def load_experts():
    return _read_json(EXPERTS_FILE, {})


def topic_names(cfg):
    return [t["name"] if isinstance(t, dict) else t for t in cfg["topics"]]


def build_topic_guide(cfg):
    lines = []
    for t in cfg["topics"]:
        if isinstance(t, dict):
            kw = t.get("keywords", [])
            lines.append(f"- {t['name']}" + (f" (từ khóa: {', '.join(kw)})" if kw else ""))
        else:
            lines.append(f"- {t}")
    return "\n".join(lines)


# ==================== GitHub ====================

def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def _gh_headers():
    return {"Authorization": f"Bearer {get_secret('GH_TOKEN')}",
            "Accept": "application/vnd.github+json"}


def github_get_json(path):
    repo = get_secret("GH_REPO")
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}",
                         headers=_gh_headers(), timeout=20)
        if r.status_code == 200:
            j = r.json()
            return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j.get("sha")
    except Exception:
        pass
    return None, None


def github_put_json(path, data, message):
    token, repo = get_secret("GH_TOKEN"), get_secret("GH_REPO")
    if not token or not repo:
        return False, "Chưa khai báo GH_TOKEN / GH_REPO trong Secrets."
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    content_str = json.dumps(data, ensure_ascii=False, indent=2)
    for _ in range(2):
        sha = None
        try:
            r = requests.get(api, headers=_gh_headers(), timeout=20)
            if r.status_code == 200:
                sha = r.json().get("sha")
        except Exception as e:
            return False, f"Không kết nối GitHub: {e}"
        body = {"message": message,
                "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        try:
            r2 = requests.put(api, headers=_gh_headers(), json=body, timeout=20)
        except Exception as e:
            return False, f"Không gửi được lên GitHub: {e}"
        if r2.status_code in (200, 201):
            return True, "Đã lưu lên GitHub."
        if r2.status_code == 409:
            continue
        return False, f"GitHub lỗi {r2.status_code}: {r2.text[:150]}"
    return False, "Xung đột khi lưu, thử lại sau."


def commit_json(path, payload, message):
    """Ghi lên GitHub; nếu OK thì ghi luôn bản local để phiên hiện tại thấy ngay."""
    ok, msg = github_put_json(path, payload, message)
    if ok:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return ok, msg


def flash_and_rerun(msg):
    st.session_state["flash"] = msg
    st.rerun()


# ==================== Tiện ích ====================

def extract_video_id(link):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([0-9A-Za-z_-]{11})", link or "")
    return m.group(1) if m else None


def post_id(url):
    u = (url or "").split("?")[0].rstrip("/")
    return "p" + hashlib.md5(u.encode("utf-8")).hexdigest()[:15]


def content_key(link):
    """Trả về (key, url_chuẩn, là_video). Hỗ trợ video YouTube và bài viết."""
    vid = extract_video_id(link)
    if vid:
        return vid, f"https://youtu.be/{vid}", True
    l = (link or "").strip()
    if l.startswith("http"):
        return post_id(l), l.split("?")[0].rstrip("/"), False
    return None, None, False


def is_youtube_id(key):
    return bool(re.fullmatch(r"[0-9A-Za-z_-]{11}", key or ""))


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


def yt_iframe(vid, start=0, height=200):
    return (f'<iframe width="100%" height="{height}" src="https://www.youtube.com/embed/{vid}'
            f'?start={start}" frameborder="0" allow="encrypted-media; fullscreen" allowfullscreen></iframe>')


def parse_time_tags(text):
    t = text or ""
    tags = []
    for y in re.findall(r"\b(20\d{2})\b", t):
        if y not in tags:
            tags.append(y)
    quarters = re.findall(r"[Qq]uý\s*([1-4])", t) + re.findall(r"\bQ([1-4])\b", t)
    for q in quarters:
        if f"Quý {q}" not in tags:
            tags.append(f"Quý {q}")
    months = re.findall(r"[Tt]háng\s*(1[0-2]|[1-9])", t)
    if not quarters:
        months += re.findall(r"\b(1[0-2]|[1-9])/20\d{2}", t)
    for m in months:
        if f"Tháng {m}" not in tags:
            tags.append(f"Tháng {m}")
    return tags


def month_index(y, m):
    return y * 12 + (m - 1)


def posted_index(posted_at):
    m = re.search(r"(20\d\d)[-/](\d{1,2})", posted_at or "")
    return month_index(int(m.group(1)), int(m.group(2))) if m else None


def refers_range(text):
    t = text or ""
    ym = re.search(r"\b(20\d{2})\b", t)
    if not ym:
        return None
    y = int(ym.group(1))
    has_q = bool(re.search(r"[Qq]uý", t) or re.search(r"\bQ[1-4]\b", t))
    mm = re.search(r"[Tt]háng\s*(1[0-2]|[1-9])", t)
    if not has_q and not mm:
        mm = re.search(r"\b(1[0-2]|[1-9])/20\d{2}", t)
    if mm and not has_q:
        mo = int(mm.group(1))
        return (month_index(y, mo), month_index(y, mo))
    qm = re.search(r"[Qq]uý\s*([1-4])", t) or re.search(r"\bQ([1-4])\b", t)
    if qm:
        q = int(qm.group(1))
        return (month_index(y, q * 3 - 2), month_index(y, q * 3))
    return (month_index(y, 1), month_index(y, 12))


def impact_badge(v):
    return {"Tích cực": ":green[● Tích cực]", "Tiêu cực": ":red[● Tiêu cực]"}.get(
        v, ":gray[● Trung lập]")


REGION_FLAG = {"Việt Nam": "🇻🇳", "Mỹ": "🇺🇸", "Châu Âu": "🇪🇺", "Trung Quốc": "🇨🇳", "Khác": "🌐"}
REGION_CODE = {"Việt Nam": "vn", "Mỹ": "us", "Châu Âu": "eu", "Trung Quốc": "cn"}


def region_flag_html(region):
    """Trả về ảnh cờ + tên khu vực (ảnh hiện được cả trên Windows, khác emoji cờ)."""
    code = REGION_CODE.get(region)
    if code:
        return (f"<img src='https://flagcdn.com/20x15/{code}.png' width='18' "
                f"style='vertical-align:middle;border-radius:2px;margin-right:3px'>{region}")
    return f"🌐 {region}"


def impact_dot_html(v):
    t = TH()
    color = {"Tích cực": t["pos"], "Tiêu cực": t["neg"]}.get(v, t["muted"])
    return f"<span style='color:{color}'>● {v}</span>"


def impact_counts(items):
    n = len(items)
    if n == 0:
        return 0, 0, 0
    pos = sum(1 for a in items if (a.get("impact") or "Trung lập") == "Tích cực")
    neg = sum(1 for a in items if (a.get("impact") or "Trung lập") == "Tiêu cực")
    pp, pn = round(pos * 100 / n), round(neg * 100 / n)
    return pp, 100 - pp - pn, pn


def impact_summary_html(items):
    t = TH()
    if not items:
        return f"<div style='text-align:right;color:{t['muted']};font-size:12px;margin-top:8px'>—</div>"
    pp, pu, pn = impact_counts(items)
    return (
        "<div style='margin-top:6px'>"
        f"<div style='display:flex;height:8px;border-radius:4px;overflow:hidden'>"
        f"<div style='width:{pp}%;background:{t['pos']}'></div>"
        f"<div style='width:{pu}%;background:{t['neu']}'></div>"
        f"<div style='width:{pn}%;background:{t['neg']}'></div></div>"
        f"<div style='text-align:right;font-size:11px;color:{t['muted']};margin-top:2px'>"
        f"🟢 {pp}% · ⚪ {pu}% · 🔴 {pn}%</div></div>")


def render_topic_summary(shown):
    """Dải 'Tổng hợp Nhận định Chuyên gia' dạng thanh phân kỳ cho 7 chủ đề."""
    t = TH()
    rows = []
    for name in TOPICS:
        items = [a for a in shown if a.get("topic") == name]
        pp, _, pn = impact_counts(items)
        rows.append(
            "<div style='display:grid;grid-template-columns:200px 44px 1fr 44px;gap:10px;"
            "align-items:center;padding:8px 0'>"
            f"<div style='font-size:15px;color:{t['text']}'>{name}</div>"
            f"<div style='font-family:monospace;font-size:13px;font-weight:600;color:{t['neg']};text-align:right'>{pn}%</div>"
            "<div style='display:flex;align-items:center;height:16px;position:relative'>"
            f"<div style='position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:{t['border']}'></div>"
            f"<div style='flex:1;display:flex;justify-content:flex-end'><div style='height:12px;border-radius:3px;width:{pn}%;background:{t['neg']}'></div></div>"
            f"<div style='flex:1'><div style='height:12px;border-radius:3px;width:{pp}%;background:{t['pos']}'></div></div>"
            "</div>"
            f"<div style='font-family:monospace;font-size:13px;font-weight:600;color:{t['pos']}'>{pp}%</div>"
            "</div>")
    return (
        f"<div style='background:{t['card']};border:1px solid {t['border']};border-radius:{t['radius']};"
        f"padding:18px 22px;box-shadow:{t['shadow']};margin-bottom:20px'>"
        f"<div style='font-family:{t['hfont']};font-size:21px;font-weight:600;color:{t['heading']};"
        "margin-bottom:8px'>Tổng hợp Nhận định Chuyên gia</div>"
        f"<div style='font-size:13px;color:{t['muted']};margin-bottom:10px'>"
        f"<span style='color:{t['neg']}'>◀ tiêu cực&nbsp;%</span> &nbsp;·&nbsp; "
        f"<span style='color:{t['pos']}'>tích cực&nbsp;% ▶</span></div>"
        + "".join(rows) + "</div>")


def avatar_img(name, size=36):
    p = EXPERTS_PROFILE.get(name, {})
    av = p.get("avatar")
    if av:
        return (f"<img src='{av}' width='{size}' height='{size}' "
                f"style='border-radius:50%;object-fit:cover;vertical-align:middle'>")
    init = (name.strip()[:1] or "?").upper()
    return (f"<span style='display:inline-flex;width:{size}px;height:{size}px;border-radius:50%;"
            f"background:#dee2e6;color:#495057;align-items:center;justify-content:center;"
            f"font-weight:600;vertical-align:middle'>{init}</span>")


def expert_title(name):
    return EXPERTS_PROFILE.get(name, {}).get("title", "")


def insights_to_csv(insights):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Chủ đề", "Khu vực", "Chuyên gia", "Kênh", "Nội dung", "Đánh giá", "Nói về",
                "Tag thời gian", "Mốc video", "Link mốc", "Ngày đăng", "Nguồn", "Link video"])
    for a in insights:
        w.writerow([a.get("topic", ""), a.get("region", ""), a.get("expert", ""), a.get("channel", ""),
                    a.get("content", ""), a.get("impact", ""), a.get("refers_to", ""),
                    "; ".join(parse_time_tags(a.get("refers_to", ""))), a.get("video_timestamp", ""),
                    a.get("video_url_at", ""), a.get("posted_at", ""), a.get("source", ""),
                    a.get("video_url", "")])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def parse_gemini_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        data = json.loads(t)
    except Exception:
        fa, fo = t.find("["), t.find("{")
        if fa == -1 and fo == -1:
            return None
        s = t[fa: t.rfind("]") + 1] if (fo == -1 or (fa != -1 and fa < fo)) else t[fo: t.rfind("}") + 1]
        data = json.loads(s)
    if isinstance(data, dict):
        for key in ("videos", "results", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data if isinstance(data, list) else None


def build_manual_insights(parsed, topics):
    now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M")
    new_videos, new_insights, skipped = {}, [], 0
    for item in parsed:
        link = item.get("video", "") or item.get("link", "") or item.get("url", "")
        key, url, is_video = content_key(link)
        if not key:
            skipped += 1
            continue
        channel = (item.get("channel") or "(không rõ kênh)").strip()
        title = (item.get("title") or "").strip()
        posted = (item.get("posted_at") or item.get("published") or "").strip()
        new_videos[key] = {"channel": channel, "title": title, "published": posted, "url": url}
        for ins in item.get("insights", []):
            if not ins.get("content"):
                continue
            topic = ins.get("topic", "")
            if topic not in topics:
                topic = topics[-1] if topics else "Khác"
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
                "source": "thủ công", "created_at": now_str})
    return new_insights, new_videos, skipped


def resolve_channel_id(url):
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", url or "")
    if m:
        return m.group(1)
    try:
        r = requests.get((url or "").split("?")[0], timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r'"channelId":"(UC[0-9A-Za-z_-]{22})"', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def list_channel_videos(url, limit=5):
    cid = resolve_channel_id(url)
    if not cid:
        return []
    feed = feedparser.parse("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid)
    out = []
    for e in feed.entries[:limit]:
        vid = getattr(e, "yt_videoid", None) or e.get("id", "").split(":")[-1]
        if vid:
            out.append({"id": vid, "title": e.get("title", ""),
                        "published": e.get("published", ""), "url": "https://youtu.be/" + vid})
    return out


def make_keep(posted_choice, refers_choice, region_filter, impact_filter="Tất cả"):
    now = datetime.now(VN_TZ)
    cur = month_index(now.year, now.month)
    post_start = {"3 tháng gần nhất": cur - 3, "6 tháng gần nhất": cur - 6}.get(posted_choice)
    ref_end = {"3 tháng tiếp theo": cur + 3, "6 tháng tiếp theo": cur + 6}.get(refers_choice)

    def keep(a):
        if impact_filter != "Tất cả" and (a.get("impact") or "Trung lập") != impact_filter:
            return False
        if region_filter != "Tất cả" and (a.get("region") or "Việt Nam") != region_filter:
            return False
        if post_start is not None:
            pi = posted_index(a.get("posted_at", ""))
            if pi is not None and pi < post_start:
                return False
        if ref_end is not None:
            rr = refers_range(a.get("refers_to", ""))
            if rr is not None:
                s, e = rr
                if not (e >= cur and s <= ref_end):
                    return False
        return True
    return keep


def sort_newest(items):
    return sorted(items, key=lambda a: posted_index(a.get("posted_at", "")) or -1, reverse=True)


def try_unlock(key):
    pw_set = get_secret("ADMIN_PASSWORD")
    if not pw_set:
        st.warning("Chưa đặt ADMIN_PASSWORD trong Secrets nên không vào được khu Công cụ.")
        return False
    if st.session_state.get("is_admin"):
        return True
    pw = st.text_input("Mật khẩu quản trị", type="password", key="pw_" + key)
    if st.button("Mở khóa", key="unlock_" + key):
        if pw == pw_set:
            st.session_state["is_admin"] = True
            st.rerun()
        else:
            st.error("Sai mật khẩu.")
    return False


# ==================== Khởi tạo ====================

st.set_page_config(page_title="Nhận định chuyên gia", page_icon="🎙️", layout="wide")
cfg = load_config()
videos, insights, updated_at = load_data()
EXPERTS_PROFILE = load_experts()
TOPICS = topic_names(cfg)

HAS_DIALOG = hasattr(st, "dialog")
HAS_POPOVER = hasattr(st, "popover")
if HAS_DIALOG:
    @st.dialog("Xem nhanh video")
    def _play_dialog(vid, sec, title):
        if title:
            st.caption(title)
        components.html(
            f'<iframe width="100%" height="400" src="https://www.youtube.com/embed/{vid}'
            f'?start={sec}&autoplay=1" frameborder="0" '
            f'allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>',
            height=420)


def render_insight(a, ctx="", show_byline=True):
    t = TH()
    if show_byline:
        expert = a.get("expert", "(không rõ)")
        ttl = expert_title(expert)
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:9px;margin-bottom:5px'>"
            f"{avatar_img(expert, 34)}"
            f"<span><span style='font-weight:600;font-size:15.5px;color:{t['heading']}'>{expert}</span>"
            + (f" <span style='color:{t['muted']};font-size:13px'>· {ttl}</span>" if ttl else "")
            + "</span></div>", unsafe_allow_html=True)
    st.markdown(f"<div style='color:{t['text']};font-size:15.5px;line-height:1.65'>{a.get('content','')}</div>",
                unsafe_allow_html=True)
    impact = a.get("impact") or "Trung lập"
    region = a.get("region") or "Việt Nam"
    parts = [impact_dot_html(impact), region_flag_html(region)]
    if a.get("refers_to"):
        parts.append(f"🗓️ {a['refers_to']}")
    if a.get("posted_at"):
        parts.append(f"📅 {a['posted_at'][:10]}")
    link = a.get("video_url", "")
    is_post = (not a.get("video_timestamp")) and link and not is_youtube_id(a.get("video_id", ""))
    meta = f"<span style='font-size:14px;color:{t['muted']}'>" + "  ·  ".join(parts) + "</span>"

    ts = a.get("video_timestamp")
    vid, sec = a.get("video_id", ""), ts_to_seconds(ts)
    if ts:
        left, right = st.columns([5, 1.4])
        left.markdown(meta, unsafe_allow_html=True)
        with right:
            if HAS_POPOVER and vid:
                with st.popover(f"▶️ {ts}"):
                    components.html(yt_iframe(vid, sec, 200), height=210)
            elif HAS_DIALOG and vid:
                if st.button(f"▶️ {ts}", key=f"pl_{ctx}_{a['id']}"):
                    _play_dialog(vid, sec, a.get("video_title", ""))
            else:
                st.markdown(f"[▶️ {ts}]({a.get('video_url_at','')})")
    elif is_post:
        left, right = st.columns([5, 1.4])
        left.markdown(meta, unsafe_allow_html=True)
        with right:
            if HAS_POPOVER:
                with st.popover("📰 Đọc bài"):
                    st.markdown(f"**{a.get('video_title','') or 'Bài viết'}**  ·  "
                                f"[↗ Mở ở tab mới]({link})")
                    ptext = videos.get(a.get("video_id", ""), {}).get("post_text", "")
                    if ptext:
                        st.markdown(
                            f"<div style='width:740px;max-width:86vw;max-height:600px;overflow:auto;"
                            f"white-space:pre-wrap;font-size:15px;line-height:1.7;color:{t['text']}'>"
                            f"{html.escape(ptext)}</div>", unsafe_allow_html=True)
                    else:
                        components.html(
                            f"<div style='width:740px;max-width:86vw'>"
                            f"<iframe src='{link}' style='width:100%;height:580px;border:0'></iframe>"
                            f"<div style='font-size:12px;color:#888;margin-top:6px'>Nếu khung trống, "
                            f"trang gốc không cho nhúng — bấm '↗ Mở ở tab mới' phía trên.</div></div>",
                            height=620)
            else:
                st.markdown(f"[📰 Mở bài]({link})")
    else:
        st.markdown(meta, unsafe_allow_html=True)


def inject_theme_css():
    t = TH()
    accent = t["heading"] if st.session_state.get("ui_theme", "light") != "dark" else "#34d399"
    st.markdown(f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Lora:wght@500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');
    html, body, .stApp {{ font-size: 17px; }}
    .stApp {{ background:{t['bg']}; color:{t['text']}; font-family:'Inter',system-ui,sans-serif; }}
    h1 {{ font-size: 2rem !important; }}
    h1,h2,h3,h4 {{ color:{t['heading']} !important; font-family:{t['hfont']}; }}
    p, li, .stMarkdown {{ font-size: 1rem; }}
    section[data-testid="stSidebar"] {{ background:{t['sidebar']}; border-right:1px solid {t['border']}; }}
    [data-testid="stVerticalBlockBorderWrapper"] {{
        background:{t['card']}; border:1px solid {t['border']} !important;
        border-radius:{t['radius']}; box-shadow:{t['shadow']}; }}
    [data-testid="stExpander"] {{ background:{t['card']}; border:1px solid {t['border']} !important;
        border-radius:{t['radius']}; }}
    [data-testid="stPopoverBody"], div[data-baseweb="popover"] [data-testid="stPopoverBody"] {{
        max-width: 88vw !important; }}
    /* Nút */
    .stButton > button, .stDownloadButton > button, [data-testid="stPopoverButton"] {{
        border-radius: 12px !important; border:1px solid {t['border']} !important;
        background:{t['card']} !important; color:{t['text']} !important;
        font-weight:600 !important; font-size:0.95rem !important;
        padding:0.5rem 1rem !important; transition: all .15s ease;
        box-shadow:{t['shadow']}; }}
    .stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stPopoverButton"]:hover {{
        border-color:{accent} !important; color:{accent} !important;
        transform: translateY(-1px); }}
    .stButton > button[kind="primary"] {{
        background:{accent} !important; color:{('#0e141b' if st.session_state.get('ui_theme')== 'dark' else '#ffffff')} !important;
        border-color:{accent} !important; }}
    /* Ô nhập, ô chọn */
    [data-baseweb="select"] > div, .stTextInput input, .stTextArea textarea {{
        background:{t['card']} !important; color:{t['text']} !important;
        border-radius: 10px !important; border-color:{t['border']} !important;
        font-size:0.95rem !important; }}
    label, .stSelectbox label, .stTextInput label {{ color:{t['muted']} !important; font-size:0.9rem !important; }}
    [data-testid="stCaptionContainer"], .stCaption {{ color:{t['muted']} !important; font-size:0.88rem !important; }}
    hr {{ border-color:{t['border']}; }}
    #MainMenu, footer {{ visibility:hidden; }}
    </style>""", unsafe_allow_html=True)


# ==================== Điều hướng ====================

NAV_TOP = ["📊 Nhận định", "🧑‍💼 Chuyên gia"]
NAV_TOOLS = ["✏️ UPDATE NHẬN ĐỊNH", "🗂️ Quản lý nguồn", "👤 Quản lý chuyên gia", "⚙️ Cấu hình"]
UNLOCK_PAGE = "🔒 Mở khóa Công cụ"
if "page" not in st.session_state:
    st.session_state["page"] = NAV_TOP[0]
cur_page = st.session_state["page"]
inject_theme_css()
with st.sidebar:
    st.markdown("### Xem")
    for lbl in NAV_TOP:
        if st.button(lbl, use_container_width=True,
                     type="primary" if lbl == cur_page else "secondary", key="nv_" + lbl):
            st.session_state["page"] = lbl
            st.rerun()
    if updated_at:
        st.caption(f"Cập nhật: **{updated_at}**")
    st.caption(f"Tổng **{len(insights)}** nhận định")
    st.markdown("<div style='height:14vh'></div>", unsafe_allow_html=True)
    st.divider()
    # Gạt sáng/tối ngay trên khu Công cụ
    _dark_now = st.session_state.get("ui_theme", "light") == "dark"
    _toggle = getattr(st, "toggle", st.checkbox)
    _dark_new = _toggle("🌙 Chế độ Tối", value=_dark_now, key="theme_toggle")
    if _dark_new != _dark_now:
        st.session_state["ui_theme"] = "dark" if _dark_new else "light"
        st.rerun()
    # Khu Công cụ: ẩn khi chưa mở khóa
    if st.session_state.get("is_admin"):
        st.caption("🔓 Công cụ")
        for lbl in NAV_TOOLS:
            if st.button(lbl, use_container_width=True,
                         type="primary" if lbl == cur_page else "secondary", key="nv_" + lbl):
                st.session_state["page"] = lbl
                st.rerun()
        if insights:
            st.download_button("⬇️ Tải CSV", data=insights_to_csv(insights),
                               file_name="nhan_dinh.csv", mime="text/csv", use_container_width=True)
    else:
        if st.button("🔒 Công cụ", use_container_width=True,
                     type="primary" if cur_page == UNLOCK_PAGE else "secondary", key="nv_unlock"):
            st.session_state["page"] = UNLOCK_PAGE
            st.rerun()
page = st.session_state["page"]

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

# Trang mở khóa khu Công cụ
if page == UNLOCK_PAGE:
    st.title("🔒 Khu Công cụ")
    st.caption("Nhập mật khẩu để hiện các công cụ: Nhập tay, Quản lý nguồn, Quản lý chuyên gia, "
               "Cấu hình, Tải CSV.")
    if st.session_state.get("is_admin"):
        st.success("Đã mở khóa. Các công cụ hiện ở thanh bên trái.")
    else:
        try_unlock("tools")
    st.stop()

# Chặn truy cập thẳng trang công cụ khi chưa mở khóa
if page in NAV_TOOLS and not st.session_state.get("is_admin"):
    st.session_state["page"] = UNLOCK_PAGE
    st.rerun()


# ==================== 📊 NHẬN ĐỊNH ====================

if page == "📊 Nhận định":
    st.title("📊 Nhận định theo chủ đề")

    if not insights:
        st.info("Chưa có nhận định. Dùng **Nhập tay** hoặc chờ luồng tự động.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        posted_choice = c1.selectbox("Ngày đăng bài", ["3 tháng gần nhất", "6 tháng gần nhất", "Tất cả"])
        refers_choice = c2.selectbox("Thời điểm nhận định", ["3 tháng tiếp theo", "6 tháng tiếp theo", "Tất cả"],
                                     index=2)
        region_filter = c3.selectbox("Khu vực", ["Tất cả"] + REGIONS, index=1)
        impact_filter = c4.selectbox("Đánh giá", ["Tất cả"] + IMPACTS)
        keep = make_keep(posted_choice, refers_choice, region_filter, impact_filter)
        shown = [a for a in insights if keep(a)]

        st.markdown(render_topic_summary(shown), unsafe_allow_html=True)

        rows = {}
        for order, t in enumerate(cfg["topics"]):
            nm = t["name"] if isinstance(t, dict) else t
            r = (t.get("row", 1) if isinstance(t, dict) else 1) or 1
            rows.setdefault(r, []).append((order, nm))

        def render_topic(name):
            items = sort_newest([a for a in shown if a.get("topic") == name])
            with st.container(border=True, height=480):
                tcol, scol = st.columns([3, 2])
                tcol.markdown(f"#### {name}")
                scol.markdown(impact_summary_html(items), unsafe_allow_html=True)
                st.caption(f"{len(items)} nhận định")
                if not items:
                    st.caption("_Chưa có nhận định._")
                for a in items:
                    render_insight(a, ctx="nd", show_byline=True)
                    st.divider()

        for r in sorted(rows.keys()):
            row_items = [n for _, n in sorted(rows[r], key=lambda x: x[0])]
            cols = st.columns(len(row_items))
            for col, name in zip(cols, row_items):
                with col:
                    render_topic(name)


# ==================== 🧑‍💼 CHUYÊN GIA ====================

elif page == "🧑‍💼 Chuyên gia":
    st.title("🧑‍💼 Nhận định theo chuyên gia")
    if not insights:
        st.info("Chưa có nhận định.")
    else:
        c1, c2, c3 = st.columns(3)
        posted_choice = c1.selectbox("Ngày đăng bài", ["3 tháng gần nhất", "6 tháng gần nhất", "Tất cả"])
        refers_choice = c2.selectbox("Thời điểm nhận định", ["3 tháng tiếp theo", "6 tháng tiếp theo", "Tất cả"],
                                     index=2, key="cg_refers")
        region_filter = c3.selectbox("Khu vực", ["Tất cả"] + REGIONS, index=1)
        keep = make_keep(posted_choice, refers_choice, region_filter)
        shown = [a for a in insights if keep(a)]

        # mỗi chuyên gia một dòng ngang; sắp theo số nhận định giảm dần
        experts = {}
        for a in shown:
            experts.setdefault(a.get("expert", "(không rõ)"), []).append(a)
        for name in sorted(experts, key=lambda k: -len(experts[k])):
            items = sort_newest(experts[name])
            with st.container(border=True):
                left, right = st.columns([1, 2])
                with left:
                    ttl = expert_title(name)
                    st.markdown(f"{avatar_img(name, 56)}", unsafe_allow_html=True)
                    st.markdown(f"### {name}")
                    if ttl:
                        st.caption(ttl)
                    st.markdown("**Góc nhìn chuyên gia**")
                    st.markdown(impact_summary_html(items), unsafe_allow_html=True)
                    st.caption(f"{len(items)} nhận định")
                with right:
                    subs = st.columns(2)
                    for i, topic in enumerate(TOPICS):
                        titems = [a for a in items if a.get("topic") == topic]
                        with subs[i % 2]:
                            st.markdown(f"**{topic}**")
                            if not titems:
                                st.caption("—")
                            for a in titems:
                                render_insight(a, ctx="cg", show_byline=False)


# ==================== ✍️ NHẬP TAY ====================

elif page == "✏️ UPDATE NHẬN ĐỊNH":
    st.title("✍️ Nhập nhận định thủ công")
    st.caption("① Chọn video gợi ý hoặc dán link → ② Copy prompt cho Gemini → ③ Dán kết quả → ④ Xem trước & Lưu.")

    # ---- Thiết lập Gem (một lần) ----
    def gem_instructions():
        tpl = cfg["manual_prompt_template"].replace("{topics}", build_topic_guide(cfg))
        anchor = "Danh sách video:\n{links}"
        note = ("Người dùng sẽ gửi MỘT link video YouTube trong mỗi tin nhắn. "
                "Hãy xử lý đúng link đó theo các yêu cầu trên và CHỈ trả về JSON.")
        if anchor in tpl:
            return tpl.replace(anchor, note)
        return tpl.replace("{links}", "(link video sẽ được gửi trong tin nhắn)") + "\n\n" + note

    with st.expander("🧞 Tối ưu: dùng Gemini Gem — thiết lập MỘT lần, sau đó mỗi video chỉ cần dán link"):
        st.markdown("""
**Cách thiết lập (làm một lần duy nhất):**
1. Mở [gemini.google.com](https://gemini.google.com) → menu trái chọn **Gems** (Trình quản lý Gem) → **Tạo Gem mới**
2. Đặt tên, ví dụ: `Bóc nhận định vĩ mô`
3. Dán toàn bộ khối dưới đây vào ô **Hướng dẫn (Instructions)** → **Lưu**

**Từ đó về sau, với mỗi video:** mở Gem này → dán **mỗi cái link video** → nhận JSON → dán về bước ③ bên dưới. Không cần copy prompt dài nữa.

⚠️ Lưu ý: nếu sau này bạn đổi danh sách chủ đề/từ khóa trong Cấu hình, hãy mở lại mục này copy khối mới và cập nhật lại Instructions của Gem.
""")
        st.code(gem_instructions(), language="text")

    # ---- ① a) Video mới do robot gợi ý ----
    st.subheader("① Video mới từ kênh nguồn (AI gợi ý chủ đề)")
    sugg = _read_json(SUGGEST_FILE, {})
    sugg_items = sugg.get("items", {})
    pending = {k: v for k, v in sugg_items.items()
               if k not in videos and v.get("status") != "bỏ qua"
               and v.get("topic") != "Không phù hợp"}
    unsuitable = {k: v for k, v in sugg_items.items()
                  if k not in videos and v.get("status") != "bỏ qua"
                  and v.get("topic") == "Không phù hợp"}
    picked_urls = []
    if sugg.get("updated_at"):
        st.caption(f"Robot quét lần cuối: **{sugg['updated_at']}**")
    if not pending and not unsuitable:
        st.info("Chưa có video gợi ý nào. Robot quét theo lịch trong Cấu hình "
                "(hoặc vào GitHub → Actions → Run workflow để quét ngay).")
    else:
        def one_video_prompt(url):
            return (cfg["manual_prompt_template"].replace("{topics}", build_topic_guide(cfg))
                    .replace("{links}", url))

        def sugg_row(vid, v, key_prefix):
            c = st.columns([0.4, 3.8, 1.3, 2.0, 0.6, 1.1])
            sel = c[0].checkbox(" ", key=f"{key_prefix}_{vid}", label_visibility="collapsed")
            icon = "📰" if v.get("type") == "post" else "📺"
            c[1].markdown(f"{icon} **{v.get('title','')}**  \n"
                          f"<span style='font-size:13px;color:{TH()['muted']}'>"
                          f"{v.get('channel','')} · 📅 {v.get('published','')}</span>",
                          unsafe_allow_html=True)
            c[2].markdown(f"<span style='font-size:13px'>🏷️ {v.get('topic','')}</span>",
                          unsafe_allow_html=True)
            url = v.get("url") or (f"https://youtu.be/{vid}" if is_youtube_id(vid) else "")
            c[3].code(url, language="text")
            with c[4]:
                if HAS_POPOVER and is_youtube_id(vid):
                    with st.popover("▶"):
                        components.html(yt_iframe(vid, 0, 210), height=220)
                elif v.get("type") == "post":
                    st.markdown(f"[📰]({url})", help="Mở bài viết")
            with c[5]:
                if HAS_POPOVER and sel:
                    with st.popover("📋 Prompt"):
                        st.caption("Chưa dùng Gem? Copy PROMPT đầy đủ này dán vào Gemini thường:")
                        st.code(one_video_prompt(url), language="text")
                else:
                    st.button("📋 Prompt", disabled=True, key=f"pb_{key_prefix}_{vid}",
                              help="Tích chọn video trước để lấy prompt đầy đủ (nếu không dùng Gem)")
            return sel

        for vid, v in sorted(pending.items(), key=lambda kv: kv[1].get("published", ""), reverse=True):
            if sugg_row(vid, v, "sg"):
                picked_urls.append(v.get("url") or f"https://youtu.be/{vid}")
        if unsuitable:
            with st.expander(f"🙈 {len(unsuitable)} video AI cho là KHÔNG phù hợp (mở nếu muốn vẫn làm)"):
                for vid, v in sorted(unsuitable.items(), key=lambda kv: kv[1].get("published", ""), reverse=True):
                    if sugg_row(vid, v, "su"):
                        picked_urls.append(v.get("url") or f"https://youtu.be/{vid}")
        def _mark_skip(vids_to_skip, label):
            remote, _ = github_get_json(SUGGEST_FILE)
            remote = remote or {"items": {}}
            ritems = remote.get("items", {})
            marked = 0
            for vid in vids_to_skip:
                if vid in ritems:
                    ritems[vid]["status"] = "bỏ qua"
                    marked += 1
            if not marked:
                st.warning(label)
                return
            remote["items"] = ritems
            ok, msg = commit_json(SUGGEST_FILE, remote, "Bo qua video goi y")
            if ok:
                flash_and_rerun(f"Đã bỏ qua {marked} video.")
            else:
                st.error(msg)

        bc1, bc2 = st.columns(2)
        if bc1.button("🙈 Bỏ qua video ĐÃ tích", use_container_width=True):
            ticked = [vid for vid in list(pending) + list(unsuitable)
                      if st.session_state.get(f"sg_{vid}") or st.session_state.get(f"su_{vid}")]
            _mark_skip(ticked, "Chưa tích video nào.")
        if bc2.button("🧹 Bỏ qua tất cả video CHƯA tích", use_container_width=True,
                      help="Giữ lại các video đã tích để làm; phần còn lại ẩn hết khỏi danh sách"):
            unticked = [vid for vid in list(pending) + list(unsuitable)
                        if not (st.session_state.get(f"sg_{vid}") or st.session_state.get(f"su_{vid}"))]
            _mark_skip(unticked, "Không còn video nào chưa tích.")

    # ---- ① b) Dán link bổ sung ----
    st.subheader("①b. Hoặc dán link (video YouTube / bài viết, mỗi dòng một link)")
    link_text = st.text_area("Link", height=90,
                             placeholder="https://youtu.be/...\nhttps://ten.substack.com/p/...")
    reprocess = st.checkbox("🔁 Làm lại cả nội dung đã xử lý trước đó")
    raw_links = picked_urls + [l.strip() for l in link_text.splitlines() if l.strip()]
    new_links, done_links, bad = [], [], 0
    for l in raw_links:
        key, _, _ = content_key(l)
        if not key:
            bad += 1
            continue
        (done_links if (key in videos and not reprocess) else new_links).append(l)
    new_links = list(dict.fromkeys(new_links))
    if done_links:
        st.caption(f"↩️ Bỏ qua {len(done_links)} nội dung đã xử lý (tích '🔁 Làm lại' nếu muốn).")
    if bad:
        st.caption(f"⚠️ {bad} dòng không phải đường link hợp lệ.")

    st.subheader("② Copy khối này dán vào Gemini")
    if new_links:
        if len(new_links) > 1:
            st.warning(f"Bạn đang gộp {len(new_links)} video vào một prompt — chất lượng tóm tắt "
                       "thường giảm mạnh. Nên dùng nút **📋 Prompt** ở từng dòng để làm từng video.")
        block = (cfg["manual_prompt_template"].replace("{topics}", build_topic_guide(cfg))
                 .replace("{links}", "\n".join(new_links)))
        st.caption(f"Gồm {len(new_links)} video — bấm biểu tượng copy ở góc khối:")
        st.code(block, language="text")
    else:
        st.info("Tích chọn video gợi ý hoặc dán ít nhất một link mới ở bước ① để tạo khối prompt.")

    st.subheader("③ Dán kết quả từ Gemini")
    pasted = st.text_area("Kết quả Gemini (JSON)", height=200)
    if st.button("👁️ Xem trước"):
        try:
            parsed = parse_gemini_json(pasted)
        except Exception as e:
            parsed = None
            st.error(f"Không đọc được JSON: {e}")
        if parsed is None:
            st.error("Không tìm thấy JSON hợp lệ.")
        else:
            ni, nv, sk = build_manual_insights(parsed, TOPICS)
            st.session_state["preview"] = {"insights": ni, "videos": nv, "skipped": sk}

    preview = st.session_state.get("preview")
    if preview:
        ni = preview["insights"]
        st.write(f"**Xem trước: {len(ni)} nhận định** / {len(preview['videos'])} video"
                 + (f" · {preview['skipped']} bỏ qua" if preview["skipped"] else ""))
        if ni:
            st.dataframe([{"Chủ đề": a["topic"], "Khu vực": a["region"], "Chuyên gia": a["expert"],
                           "Đánh giá": a["impact"], "Nội dung": a["content"][:70],
                           "Mốc": a["video_timestamp"], "Nói về": a["refers_to"]} for a in ni],
                         use_container_width=True, hide_index=True)
            if st.button("💾 Lưu vào hệ thống", type="primary"):
                remote, _ = github_get_json(DATA_FILE)
                remote = remote or {"videos": {}, "insights": []}
                rv = remote.get("videos", {})
                rv.update(preview["videos"])
                # Lưu kèm nội dung bài (nếu có trong gợi ý) để đọc trong popup
                for k in preview["videos"]:
                    txt = sugg_items.get(k, {}).get("content", "")
                    if txt:
                        rv[k]["post_text"] = txt
                # Ghi đè: bỏ nhận định cũ của các video vừa nạp, rồi thêm bản mới
                new_vids = set(preview["videos"].keys())
                kept = [i for i in remote.get("insights", []) if i.get("video_id") not in new_vids]
                merged = ni + kept
                payload = {"updated_at": "(vừa cập nhật tay)", "videos": rv, "insights": merged}
                ok, msg = commit_json(DATA_FILE, payload, "Them nhan dinh thu cong")
                if ok:
                    # Dọn các video vừa xử lý khỏi danh sách gợi ý (nếu có)
                    rs, _ = github_get_json(SUGGEST_FILE)
                    if rs and any(v in rs.get("items", {}) for v in new_vids):
                        rs["items"] = {k: v for k, v in rs.get("items", {}).items()
                                       if k not in new_vids}
                        commit_json(SUGGEST_FILE, rs, "Don goi y da xu ly")
                    st.session_state.pop("preview", None)
                    flash_and_rerun(f"Đã lưu {len(ni)} nhận định cho {len(new_vids)} video.")
                else:
                    st.error(msg)


# ==================== 🗂️ QUẢN LÝ NGUỒN ====================

elif page == "🗂️ Quản lý nguồn":
    st.title("🗂️ Quản lý nguồn (video)")
    st.caption("Mỗi video là một nguồn, có thể chứa nhiều nhận định. Sửa/xóa nguồn sẽ ảnh hưởng "
               "toàn bộ nhận định bên trong.")
    by_video = {}
    for a in insights:
        by_video.setdefault(a.get("video_id", ""), []).append(a)
    if not by_video:
        st.info("Chưa có nguồn nào.")
    else:
        def vurl_of(vid, a0):
            return (videos.get(vid, {}).get("url") or a0[0].get("video_url")
                    or f"https://youtu.be/{vid}")

        # gom theo kênh rồi theo ngày đăng mới nhất
        def chan_of(vid, a0):
            return videos.get(vid, {}).get("channel") or a0[0].get("channel", "")

        def date_of(vid, a0):
            return (videos.get(vid, {}).get("published", "") or a0[0].get("posted_at", ""))[:10]

        order = sorted(by_video.items(), key=lambda kv: (chan_of(*kv), date_of(*kv)), reverse=False)

        # Tìm kiếm theo kênh / tiêu đề / mã video / chuyên gia / khu vực
        q = st.text_input("🔎 Tìm theo kênh, tiêu đề, chuyên gia hoặc khu vực",
                          placeholder="vd: VIF, lạm phát, Long Phan, Mỹ...").strip().lower()
        if q:
            def match(vid, a0):
                title = (videos.get(vid, {}).get("title", "") or a0[0].get("video_title", "")).lower()
                if q in chan_of(vid, a0).lower() or q in title or q in vid.lower():
                    return True
                return any(q in (a.get("expert", "") or "").lower()
                           or q in (a.get("region", "") or "").lower() for a in a0)
            order = [(vid, a0) for vid, a0 in order if match(vid, a0)]
            st.caption(f"Tìm thấy {len(order)} nguồn khớp.")

        # Phân trang
        PAGE_SIZE = 15
        total = len(order)
        npages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        pc1, pc2 = st.columns([1, 3])
        pageno = int(pc1.number_input("Trang", min_value=1, max_value=npages, value=1, step=1,
                                      key="src_page"))
        start = (pageno - 1) * PAGE_SIZE
        page_order = order[start:start + PAGE_SIZE]
        pc2.caption(f"Hiển thị {start + 1}–{min(start + PAGE_SIZE, total)} / tổng {total} nguồn "
                    f"· {npages} trang. Nhớ **Lưu thay đổi** trước khi chuyển trang.")

        st.subheader("Nguồn (bấm để xem & sửa)")
        editors, ch_edit, ti_edit, del_src, orig = {}, {}, {}, {}, {}
        for vid, child in page_order:
            meta = videos.get(vid, {})
            title = meta.get("title", "") or (child[0].get("video_title", ""))
            chan = chan_of(vid, child)
            vurl = vurl_of(vid, child)
            d = date_of(vid, child)
            orig[vid] = {a["id"] for a in child}
            label = f"🎬 {chan} · {title[:45] or ('youtu.be/' + vid)} · 📅 {d} · {len(child)} NĐ"
            with st.expander(label):
                lc, rc = st.columns([4, 1])
                with lc:
                    st.caption("Link gốc (bấm icon để copy):")
                    st.code(vurl, language="text")
                with rc:
                    if HAS_POPOVER and is_youtube_id(vid):
                        with st.popover("▶ Xem nhanh"):
                            components.html(yt_iframe(vid, 0, 210), height=220)
                    elif not is_youtube_id(vid):
                        st.markdown(f"[📰 Mở]({vurl})")
                ti_edit[vid] = st.text_input("Tiêu đề", value=title, key="ti_" + vid)
                ch_edit[vid] = st.text_input("Tên kênh (áp cho mọi nhận định của nguồn)",
                                             value=chan, key="chan_" + vid)
                editors[vid] = st.data_editor(
                    [{"id": a["id"], "Tác giả": a.get("expert", ""), "Khu vực": a.get("region", "Việt Nam"),
                      "Chủ đề": a.get("topic", ""), "Đánh giá": a.get("impact", "Trung lập"),
                      "Nội dung": a.get("content", ""), "Nói về": a.get("refers_to", ""),
                      "Mốc": a.get("video_timestamp", "")} for a in child],
                    num_rows="dynamic", use_container_width=True, key="ed_" + vid,
                    column_config={"id": st.column_config.TextColumn("mã", disabled=True, width="small"),
                                   "Tác giả": st.column_config.TextColumn(width="medium"),
                                   "Khu vực": st.column_config.SelectboxColumn(options=REGIONS),
                                   "Chủ đề": st.column_config.SelectboxColumn(options=TOPICS),
                                   "Đánh giá": st.column_config.SelectboxColumn(options=IMPACTS),
                                   "Nội dung": st.column_config.TextColumn(width="large"),
                                   "Nói về": st.column_config.TextColumn(width="small"),
                                   "Mốc": st.column_config.TextColumn(width="small")})
                del_src[vid] = st.checkbox("🗑️ Xóa toàn bộ nguồn này", key="ds_" + vid)

        if st.button("💾 Lưu thay đổi", type="primary"):
            edits, deleted, del_videos, title_edit = {}, set(), set(), {}
            for vid, child in page_order:
                if del_src.get(vid):
                    del_videos.add(vid)
                    continue
                title_edit[vid] = str(ti_edit.get(vid) or "").strip()
                present = set()
                for r in editors.get(vid, []):
                    rid = r.get("id")
                    if not rid:
                        continue
                    present.add(rid)
                    edits[rid] = {"expert": str(r.get("Tác giả") or "").strip(),
                                  "region": r.get("Khu vực") or "Việt Nam",
                                  "topic": r.get("Chủ đề") or "",
                                  "impact": r.get("Đánh giá") or "Trung lập",
                                  "content": str(r.get("Nội dung") or "").strip(),
                                  "refers_to": str(r.get("Nói về") or "").strip(),
                                  "timestamp": str(r.get("Mốc") or "").strip(),
                                  "channel": str(ch_edit.get(vid) or "").strip(),
                                  "title": title_edit[vid]}
                deleted |= (orig[vid] - present)
            remote, _ = github_get_json(DATA_FILE)
            remote = remote or {"videos": {}, "insights": []}
            rvideos = {k: v for k, v in remote.get("videos", {}).items() if k not in del_videos}
            for vid, t in title_edit.items():
                if vid in rvideos:
                    rvideos[vid]["title"] = t
            out = []
            for a in remote.get("insights", []):
                if a.get("video_id") in del_videos or a["id"] in deleted:
                    continue
                if a["id"] in edits:
                    e = edits[a["id"]]
                    if e["expert"]:
                        a["expert"] = e["expert"]
                    if e["region"] in REGIONS:
                        a["region"] = e["region"]
                    if e["topic"] in TOPICS:
                        a["topic"] = e["topic"]
                    if e["impact"] in IMPACTS:
                        a["impact"] = e["impact"]
                    if e["content"]:
                        a["content"] = e["content"]
                    a["refers_to"] = e["refers_to"]
                    ts = e["timestamp"]
                    a["video_timestamp"] = ts
                    base = a.get("video_url") or f"https://youtu.be/{a.get('video_id','')}"
                    a["video_url_at"] = base + (f"?t={ts_to_seconds(ts)}" if ts else "")
                    a["video_title"] = e["title"]
                    if e["channel"]:
                        a["channel"] = e["channel"]
                        if a.get("video_id") in rvideos:
                            rvideos[a["video_id"]]["channel"] = e["channel"]
                out.append(a)
            payload = {"updated_at": "(vừa sửa nguồn)", "videos": rvideos, "insights": out}
            ok, msg = commit_json(DATA_FILE, payload, "Quan ly nguon")
            if ok:
                flash_and_rerun(msg + " Đã cập nhật nguồn.")
            else:
                st.error(msg)


# ==================== 👤 QUẢN LÝ CHUYÊN GIA ====================

elif page == "👤 Quản lý chuyên gia":
    st.title("👤 Quản lý chuyên gia")
    st.caption("Cập nhật chức vụ và ảnh đại diện cho từng chuyên gia.")
    all_names = sorted({a.get("expert", "") for a in insights if a.get("expert")})
    if not all_names:
        st.info("Chưa có chuyên gia nào trong dữ liệu.")
    else:
        qe = st.text_input("🔎 Tìm theo tên hoặc chức vụ").strip().lower()
        names = [n for n in all_names if (not qe) or qe in n.lower() or qe in expert_title(n).lower()]
        if not names:
            st.info("Không tìm thấy chuyên gia khớp.")
        else:
            sel = st.selectbox("Chọn chuyên gia", names)
            prof = EXPERTS_PROFILE.get(sel, {})
            col1, col2 = st.columns([1, 2])
            with col1:
                st.markdown("Ảnh hiện tại:")
                st.markdown(avatar_img(sel, 96), unsafe_allow_html=True)
            with col2:
                title = st.text_input("Chức vụ", value=prof.get("title", ""))
                up = st.file_uploader("Ảnh đại diện (PNG/JPG)", type=["png", "jpg", "jpeg"])
            if st.button("💾 Lưu hồ sơ", type="primary"):
                avatar = prof.get("avatar", "")
                if up is not None:
                    try:
                        from PIL import Image
                        im = Image.open(io.BytesIO(up.read())).convert("RGB")
                        im.thumbnail((96, 96))
                        out = io.BytesIO()
                        im.save(out, format="JPEG", quality=80)
                        avatar = "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
                    except Exception as e:
                        st.error(f"Không xử lý được ảnh: {e}")
                remote, _ = github_get_json(EXPERTS_FILE)
                remote = remote or {}
                remote[sel] = {"title": title.strip(), "avatar": avatar}
                ok, msg = commit_json(EXPERTS_FILE, remote, "Cap nhat ho so chuyen gia")
                if ok:
                    flash_and_rerun(msg + " Đã cập nhật hồ sơ.")
                else:
                    st.error(msg)


# ==================== ⚙️ CẤU HÌNH ====================

else:
    st.title("⚙️ Cấu hình")
    if "model_select" not in st.session_state:
        st.session_state["model_select"] = cfg["model"] if cfg["model"] in GEMINI_MODELS else DEFAULT_MODEL
    st.subheader("Model AI (luồng tự động)")
    st.selectbox("Model", GEMINI_MODELS, key="model_select")
    st.subheader("Giờ chạy luồng tự động (giờ VN)")
    st.multiselect("Giờ", list(range(24)), default=cfg["update_hours"],
                   format_func=lambda h: f"{h}h", key="hours_select")

    st.subheader("Kênh YouTube (nguồn)")
    ch_rows = [{"Tên kênh": c.get("name", ""), "Link kênh": c.get("url", "")}
               for c in cfg["channels"]] or [{"Tên kênh": "", "Link kênh": ""}]
    ch_edit = st.data_editor(ch_rows, num_rows="dynamic", use_container_width=True, key="ch_editor",
                             column_config={"Tên kênh": st.column_config.TextColumn(width="medium"),
                                            "Link kênh": st.column_config.TextColumn(width="large")})

    st.subheader("Bản tin Substack (nguồn)")
    st.caption("Dùng link bản tin dạng `ten.substack.com` (link hồ sơ `substack.com/@ten` robot sẽ tự dò "
               "nhưng kém chắc chắn hơn).")
    sb_rows = [{"Tên bản tin": s.get("name", ""), "Link bản tin": s.get("url", "")}
               for s in cfg.get("substacks", [])] or [{"Tên bản tin": "", "Link bản tin": ""}]
    sb_edit = st.data_editor(sb_rows, num_rows="dynamic", use_container_width=True, key="sb_editor",
                             column_config={"Tên bản tin": st.column_config.TextColumn(width="medium"),
                                            "Link bản tin": st.column_config.TextColumn(width="large")})

    st.subheader("Chủ đề (Hàng = bố cục, Từ khóa = gợi ý cho AI)")
    st.caption("Từ khóa ngăn nhau bằng dấu phẩy.")
    tp_rows = [{"Tên chủ đề": (t["name"] if isinstance(t, dict) else t),
                "Hàng": (t.get("row", 1) if isinstance(t, dict) else 1),
                "Từ khóa": ", ".join(t.get("keywords", [])) if isinstance(t, dict) else ""}
               for t in cfg["topics"]] or [{"Tên chủ đề": "", "Hàng": 1, "Từ khóa": ""}]
    tp_edit = st.data_editor(tp_rows, num_rows="dynamic", use_container_width=True, key="tp_editor",
                             column_config={"Tên chủ đề": st.column_config.TextColumn(width="medium"),
                                            "Hàng": st.column_config.NumberColumn(min_value=1, max_value=20, step=1),
                                            "Từ khóa": st.column_config.TextColumn(width="large")})

    st.subheader("Prompt luồng tự động")
    auto_prompt = st.text_area("Prompt (AI đọc transcript)", value=cfg["prompt_instructions"], height=110)
    st.subheader("Prompt mẫu cho Gemini (nhập tay)")
    st.caption("Giữ nguyên {topics} và {links}.")
    manual_tpl = st.text_area("Prompt mẫu", value=cfg["manual_prompt_template"], height=200)

    st.markdown("---")
    if st.button("💾 Lưu tất cả", type="primary"):
        channels = [{"id": "ch_" + hashlib.md5((r.get("Link kênh") or "").encode()).hexdigest()[:6],
                     "name": (r.get("Tên kênh") or "").strip(), "url": (r.get("Link kênh") or "").strip()}
                    for r in ch_edit if (r.get("Tên kênh") or "").strip() and (r.get("Link kênh") or "").strip()]
        substacks = [{"name": (r.get("Tên bản tin") or "").strip(), "url": (r.get("Link bản tin") or "").strip()}
                     for r in sb_edit
                     if (r.get("Tên bản tin") or "").strip() and (r.get("Link bản tin") or "").strip()]
        topics = [{"name": (r.get("Tên chủ đề") or "").strip(), "row": int(r.get("Hàng", 1) or 1),
                   "keywords": [k.strip() for k in (r.get("Từ khóa") or "").split(",") if k.strip()]}
                  for r in tp_edit if (r.get("Tên chủ đề") or "").strip()]
        if (not channels and not substacks) or not topics:
            st.error("Cần ít nhất một nguồn (kênh YouTube hoặc Substack) và một chủ đề.")
        else:
            new_cfg = {"model": st.session_state.get("model_select", DEFAULT_MODEL),
                       "update_hours": sorted(st.session_state.get("hours_select", [])),
                       "channels": channels, "substacks": substacks, "topics": topics,
                       "prompt_instructions": auto_prompt.strip() or DEFAULT_AUTO_PROMPT,
                       "manual_prompt_template": manual_tpl.strip() or DEFAULT_MANUAL_TEMPLATE}
            ok, msg = commit_json(CONFIG_FILE, new_cfg, "Cap nhat cau hinh")
            if ok:
                flash_and_rerun(msg + " Đã áp dụng cấu hình.")
            else:
                st.error(msg)
