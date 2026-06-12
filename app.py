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
DEFAULT_MODEL = "gemini-2.5-flash-lite"
GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.5-flash",
                 "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]
IMPACTS = ["Tích cực", "Trung lập", "Tiêu cực"]
REGIONS = ["Việt Nam", "Mỹ", "Châu Âu", "Trung Quốc", "Khác"]
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
    color = {"Tích cực": "#2f9e44", "Tiêu cực": "#e03131"}.get(v, "#868e96")
    return f"<span style='color:{color}'>● {v}</span>"


def impact_summary_html(items):
    n = len(items)
    if n == 0:
        return "<div style='text-align:right;color:#bbb;font-size:12px;margin-top:8px'>—</div>"
    pos = sum(1 for a in items if (a.get("impact") or "Trung lập") == "Tích cực")
    neg = sum(1 for a in items if (a.get("impact") or "Trung lập") == "Tiêu cực")
    pp, pn = round(pos * 100 / n), round(neg * 100 / n)
    pu = 100 - pp - pn
    return (
        "<div style='margin-top:6px'>"
        "<div style='display:flex;height:8px;border-radius:4px;overflow:hidden;border:1px solid #eee'>"
        f"<div style='width:{pp}%;background:#2f9e44'></div>"
        f"<div style='width:{pu}%;background:#ced4da'></div>"
        f"<div style='width:{pn}%;background:#e03131'></div></div>"
        f"<div style='text-align:right;font-size:11px;color:#555;margin-top:2px'>"
        f"🟢 {pp}% · ⚪ {pu}% · 🔴 {pn}%</div></div>")


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
        vid = extract_video_id(item.get("video", "") or item.get("link", "") or item.get("url", ""))
        if not vid:
            skipped += 1
            continue
        url = f"https://youtu.be/{vid}"
        channel = (item.get("channel") or "(không rõ kênh)").strip()
        title = (item.get("title") or "").strip()
        posted = (item.get("posted_at") or item.get("published") or "").strip()
        new_videos[vid] = {"channel": channel, "title": title, "published": posted, "url": url}
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
            ts = ins.get("timestamp", "")
            raw = vid + topic + ins.get("content", "")[:30]
            new_insights.append({
                "id": hashlib.md5(raw.encode("utf-8")).hexdigest(),
                "video_id": vid, "channel": channel,
                "expert": (ins.get("expert", "") or channel).strip(),
                "topic": topic, "content": ins.get("content", "").strip(),
                "impact": impact, "region": region, "video_timestamp": ts,
                "video_url_at": url + (f"?t={ts_to_seconds(ts)}" if ts else ""),
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


def render_insight(a, ctx=""):
    st.write(a.get("content", ""))
    impact = a.get("impact") or "Trung lập"
    region = a.get("region") or "Việt Nam"
    parts = [impact_dot_html(impact), region_flag_html(region)]
    if a.get("refers_to"):
        parts.append(f"🗓️ {a['refers_to']}")
    if a.get("posted_at"):
        parts.append(f"📅 {a['posted_at'][:10]}")
    parts.append("🤖" if a.get("source") == "tự động" else "✍️")
    meta = "<span style='font-size:13px;color:#666'>" + "  ·  ".join(parts) + "</span>"

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
    else:
        st.markdown(meta, unsafe_allow_html=True)


# ==================== Điều hướng ====================

NAV_TOP = ["📊 Nhận định", "🧑‍💼 Chuyên gia"]
NAV_TOOLS = ["✍️ Nhập tay", "🗂️ Quản lý nguồn", "👤 Quản lý chuyên gia", "⚙️ Cấu hình"]
if "page" not in st.session_state:
    st.session_state["page"] = NAV_TOP[0]
cur_page = st.session_state["page"]
with st.sidebar:
    st.markdown("### Xem")
    for lbl in NAV_TOP:
        if st.button(lbl, use_container_width=True,
                     type="primary" if lbl == cur_page else "secondary", key="nv_" + lbl):
            st.session_state["page"] = lbl
            st.rerun()
    st.markdown("<div style='height:22vh'></div>", unsafe_allow_html=True)
    st.divider()
    st.caption("🔒 Công cụ")
    for lbl in NAV_TOOLS:
        if st.button(lbl, use_container_width=True,
                     type="primary" if lbl == cur_page else "secondary", key="nv_" + lbl):
            st.session_state["page"] = lbl
            st.rerun()
page = st.session_state["page"]

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

# Cổng mật khẩu cho toàn bộ khu Công cụ
if page in NAV_TOOLS and not st.session_state.get("is_admin"):
    st.title("🔒 Khu Công cụ")
    st.caption("Nhập mật khẩu để dùng Nhập tay, Quản lý nguồn, Quản lý chuyên gia, Cấu hình.")
    try_unlock("tools")
    st.stop()


# ==================== 📊 NHẬN ĐỊNH ====================

if page == "📊 Nhận định":
    st.title("📊 Nhận định theo chủ đề")
    with st.sidebar:
        if updated_at:
            st.caption(f"Cập nhật: **{updated_at}**")
        st.caption(f"Tổng **{len(insights)}** nhận định")
        if insights:
            st.download_button("⬇️ Tải CSV", data=insights_to_csv(insights),
                               file_name="nhan_dinh.csv", mime="text/csv", use_container_width=True)
        if st.button("🔄 Tải lại", use_container_width=True):
            st.rerun()

    if not insights:
        st.info("Chưa có nhận định. Dùng **Nhập tay** hoặc chờ luồng tự động.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        posted_choice = c1.selectbox("Ngày đăng bài", ["3 tháng gần nhất", "6 tháng gần nhất", "Tất cả"])
        refers_choice = c2.selectbox("Thời điểm nói tới", ["3 tháng tiếp theo", "6 tháng tiếp theo", "Tất cả"])
        region_filter = c3.selectbox("Khu vực", ["Tất cả"] + REGIONS, index=1)
        impact_filter = c4.selectbox("Đánh giá", ["Tất cả"] + IMPACTS)
        keep = make_keep(posted_choice, refers_choice, region_filter, impact_filter)
        shown = [a for a in insights if keep(a)]

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
                by_expert = {}
                for a in items:
                    by_expert.setdefault(a.get("expert", "(không rõ)"), []).append(a)
                if not items:
                    st.caption("_Chưa có nhận định._")
                for expert, arr in by_expert.items():
                    ttl = expert_title(expert)
                    st.markdown(f"{avatar_img(expert, 28)} **{expert}**"
                                + (f" · <span style='color:#888;font-size:12px'>{ttl}</span>" if ttl else ""),
                                unsafe_allow_html=True)
                    for a in arr:
                        render_insight(a, ctx="nd")
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
        refers_choice = c2.selectbox("Thời điểm nói tới", ["3 tháng tiếp theo", "6 tháng tiếp theo", "Tất cả"])
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
                                render_insight(a, ctx="cg")


# ==================== ✍️ NHẬP TAY ====================

elif page == "✍️ Nhập tay":
    st.title("✍️ Nhập nhận định thủ công")
    st.caption("① Dán link → ② Copy prompt cho Gemini → ③ Dán kết quả → ④ Xem trước & Lưu.")

    st.subheader("① Dán link video (mỗi dòng một link)")
    link_text = st.text_area("Link video", height=110,
                             placeholder="https://youtu.be/...\nhttps://www.youtube.com/watch?v=...")
    reprocess = st.checkbox("🔁 Làm lại cả video đã xử lý trước đó")
    raw_links = [l.strip() for l in link_text.splitlines() if l.strip()]
    new_links, done_links, bad = [], [], 0
    for l in raw_links:
        vid = extract_video_id(l)
        if not vid:
            bad += 1
            continue
        (done_links if (vid in videos and not reprocess) else new_links).append(l)
    new_links = list(dict.fromkeys(new_links))
    if done_links:
        st.caption(f"↩️ Bỏ qua {len(done_links)} video đã xử lý (tích '🔁 Làm lại' nếu muốn).")
    if bad:
        st.caption(f"⚠️ {bad} dòng không phải link YouTube.")

    st.subheader("② Copy khối này dán vào Gemini")
    if new_links:
        block = (cfg["manual_prompt_template"].replace("{topics}", build_topic_guide(cfg))
                 .replace("{links}", "\n".join(new_links)))
        st.caption(f"Gồm {len(new_links)} video — bấm biểu tượng copy ở góc khối:")
        st.code(block, language="text")
    else:
        st.info("Dán ít nhất một link mới ở bước ① để tạo khối prompt.")

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
                # Ghi đè: bỏ nhận định cũ của các video vừa nạp, rồi thêm bản mới
                new_vids = set(preview["videos"].keys())
                kept = [i for i in remote.get("insights", []) if i.get("video_id") not in new_vids]
                merged = ni + kept
                payload = {"updated_at": "(vừa cập nhật tay)", "videos": rv, "insights": merged}
                ok, msg = commit_json(DATA_FILE, payload, "Them nhan dinh thu cong")
                if ok:
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

        # Tìm kiếm theo tiêu đề / kênh / mã video
        q = st.text_input("🔎 Tìm theo kênh hoặc tiêu đề video", placeholder="vd: VIF, lạm phát...").strip().lower()
        if q:
            def match(vid, a0):
                title = (videos.get(vid, {}).get("title", "") or a0[0].get("video_title", "")).lower()
                return q in chan_of(vid, a0).lower() or q in title or q in vid.lower()
            order = [(vid, a0) for vid, a0 in order if match(vid, a0)]
            st.caption(f"Tìm thấy {len(order)} nguồn khớp.")

        # Cập nhật nhanh nguồn cũ: lấy link các nguồn (theo bộ lọc) để chạy lại qua Gemini
        with st.expander("⚡ Cập nhật nhanh dữ liệu nguồn cũ (chạy lại qua Gemini)"):
            st.caption("Copy khối dưới đây dán vào Gemini, rồi mang kết quả sang trang **Nhập tay** "
                       "(tích '🔁 Làm lại') để ghi đè dữ liệu mới cho các nguồn này.")
            only_missing = st.checkbox("Chỉ lấy nguồn còn thiếu tiêu đề", value=True)
            upd = [(vid, a0) for vid, a0 in order
                   if (not only_missing) or not (videos.get(vid, {}).get("title", "") or a0[0].get("video_title", ""))]
            links = [vurl_of(vid, a0) for vid, a0 in upd]
            if links:
                block = (cfg["manual_prompt_template"].replace("{topics}", build_topic_guide(cfg))
                         .replace("{links}", "\n".join(links)))
                st.caption(f"{len(links)} nguồn:")
                st.code(block, language="text")
            else:
                st.info("Không có nguồn nào cần cập nhật theo điều kiện trên.")

        st.subheader("Thống kê nguồn")
        h = st.columns([2, 3, 1.3, 0.8, 1])
        for col, t in zip(h, ["Kênh", "Tiêu đề", "Ngày đăng", "Số NĐ", "Xem"]):
            col.caption(t)
        for vid, a0 in order:
            row = st.columns([2, 3, 1.3, 0.8, 1])
            row[0].write(chan_of(vid, a0))
            row[1].write(videos.get(vid, {}).get("title", "") or a0[0].get("video_title", "")
                         or f"youtu.be/{vid}")
            row[2].write(date_of(vid, a0))
            row[3].write(len(a0))
            with row[4]:
                if HAS_POPOVER:
                    with st.popover("▶ Xem"):
                        components.html(yt_iframe(vid, 0, 210), height=220)
                else:
                    st.markdown(f"[▶ Mở]({vurl_of(vid, a0)})")

        st.subheader("Sửa / xóa từng nguồn")
        editors, ch_edit, ti_edit, del_src, orig = {}, {}, {}, {}, {}
        for vid, child in order:
            meta = videos.get(vid, {})
            title = meta.get("title", "") or (child[0].get("video_title", ""))
            chan = chan_of(vid, child)
            vurl = vurl_of(vid, child)
            orig[vid] = {a["id"] for a in child}
            label = f"🎬 {chan} · youtu.be/{vid}" + (f" — {title[:40]}" if title else "") + f" ({len(child)})"
            with st.expander(label):
                lc, rc = st.columns([4, 1])
                with lc:
                    st.caption("Link video gốc (bấm icon để copy):")
                    st.code(vurl, language="text")
                with rc:
                    if HAS_POPOVER:
                        with st.popover("▶ Xem nhanh"):
                            components.html(yt_iframe(vid, 0, 210), height=220)
                ti_edit[vid] = st.text_input("Tiêu đề video", value=title, key="ti_" + vid)
                ch_edit[vid] = st.text_input("Tên kênh (áp cho mọi nhận định của nguồn)",
                                             value=chan, key="chan_" + vid)
                editors[vid] = st.data_editor(
                    [{"id": a["id"], "Tác giả": a.get("expert", ""), "Khu vực": a.get("region", "Việt Nam"),
                      "Chủ đề": a.get("topic", ""), "Nội dung": a.get("content", "")[:120]} for a in child],
                    num_rows="dynamic", use_container_width=True, key="ed_" + vid,
                    column_config={"id": st.column_config.TextColumn("mã", disabled=True),
                                   "Khu vực": st.column_config.SelectboxColumn(options=REGIONS),
                                   "Chủ đề": st.column_config.SelectboxColumn(options=TOPICS),
                                   "Nội dung": st.column_config.TextColumn(disabled=True, width="large")})
                del_src[vid] = st.checkbox("🗑️ Xóa toàn bộ nguồn này", key="ds_" + vid)

        if st.button("💾 Lưu thay đổi", type="primary"):
            edits, deleted, del_videos, title_edit = {}, set(), set(), {}
            for vid, child in order:
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
    names = sorted({a.get("expert", "") for a in insights if a.get("expert")})
    if not names:
        st.info("Chưa có chuyên gia nào trong dữ liệu.")
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
        topics = [{"name": (r.get("Tên chủ đề") or "").strip(), "row": int(r.get("Hàng", 1) or 1),
                   "keywords": [k.strip() for k in (r.get("Từ khóa") or "").split(",") if k.strip()]}
                  for r in tp_edit if (r.get("Tên chủ đề") or "").strip()]
        if not channels or not topics:
            st.error("Cần ít nhất một kênh và một chủ đề.")
        else:
            new_cfg = {"model": st.session_state.get("model_select", DEFAULT_MODEL),
                       "update_hours": sorted(st.session_state.get("hours_select", [])),
                       "channels": channels, "topics": topics,
                       "prompt_instructions": auto_prompt.strip() or DEFAULT_AUTO_PROMPT,
                       "manual_prompt_template": manual_tpl.strip() or DEFAULT_MANUAL_TEMPLATE}
            ok, msg = commit_json(CONFIG_FILE, new_cfg, "Cap nhat cau hinh")
            if ok:
                flash_and_rerun(msg + " Đã áp dụng cấu hình.")
            else:
                st.error(msg)
