# -*- coding: utf-8 -*-
"""
DASHBOARD NHẬN ĐỊNH CHUYÊN GIA (YouTube)
Trang xem (góc trên trái): 📊 Nhận định, 🧑‍💼 Chuyên gia
Công cụ (góc dưới trái):    ✍️ Nhập tay, ⚙️ Cấu hình
Secrets: ADMIN_PASSWORD, GH_TOKEN, GH_REPO ; Thư viện: streamlit, requests, feedparser
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

CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
DEFAULT_MODEL = "gemini-2.5-flash-lite"
GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.5-flash",
                 "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]
IMPACTS = ["Tích cực", "Trung lập", "Tiêu cực"]
DEFAULT_AUTO_PROMPT = ("Bạn là trợ lý phân tích kinh tế. Đọc bản ghi (có mốc thời gian) của "
                       "video và rút ra các NHẬN ĐỊNH kinh tế, tóm tắt 2-4 câu và đánh giá "
                       "tác động (Tích cực/Trung lập/Tiêu cực).")
DEFAULT_MANUAL_TEMPLATE = (
    "Bạn là trợ lý phân tích kinh tế. Với MỖI link video dưới đây, xem video và rút ra các "
    "nhận định kinh tế quan trọng.\n\nPhân loại mỗi nhận định vào ĐÚNG MỘT chủ đề: {topics}\n\n"
    "Mỗi video trả về: video, channel, posted_at (YYYY-MM-DD), insights[]. Mỗi nhận định: "
    "expert, topic, content (2-4 câu), impact (Tích cực/Trung lập/Tiêu cực), timestamp (mm:ss), "
    "refers_to (vd \"Quý 3/2026\").\n\nCHỈ trả về JSON:\n[{\"video\":\"<link>\",\"channel\":"
    "\"...\",\"posted_at\":\"...\",\"insights\":[{\"expert\":\"...\",\"topic\":\"...\","
    "\"content\":\"...\",\"impact\":\"...\",\"timestamp\":\"mm:ss\",\"refers_to\":\"...\"}]}]"
    "\n\nDanh sách video:\n{links}")


# ==================== Cấu hình & dữ liệu ====================

def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    return {
        "model": cfg.get("model", DEFAULT_MODEL),
        "channels": cfg.get("channels", []),
        "topics": cfg.get("topics", []),
        "experts": cfg.get("experts", []),
        "update_hours": cfg.get("update_hours", []),
        "prompt_instructions": cfg.get("prompt_instructions", DEFAULT_AUTO_PROMPT),
        "manual_prompt_template": cfg.get("manual_prompt_template", DEFAULT_MANUAL_TEMPLATE),
    }


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("videos", {}), d.get("insights", []), d.get("updated_at", "")
        except Exception:
            pass
    return {}, [], ""


def topic_names(cfg):
    return [t["name"] if isinstance(t, dict) else t for t in cfg["topics"]]


def build_topic_guide(cfg):
    """Danh sách chủ đề kèm từ khóa, để đưa vào prompt giúp AI phân loại."""
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


def parse_time_tags(text):
    """Bóc 'nói về' thành các tag: năm (2026), quý (Quý 3), tháng (Tháng 6)."""
    t = text or ""
    tags = []
    for y in re.findall(r"\b(20\d{2})\b", t):
        if y not in tags:
            tags.append(y)
    quarters = re.findall(r"[Qq]uý\s*([1-4])", t) + re.findall(r"\bQ([1-4])\b", t)
    for q in quarters:
        tag = f"Quý {q}"
        if tag not in tags:
            tags.append(tag)
    months = re.findall(r"[Tt]háng\s*(1[0-2]|[1-9])", t)
    if not quarters:   # tránh "Quý 3/2026" bị bắt nhầm thành Tháng 3
        months += re.findall(r"\b(1[0-2]|[1-9])/20\d{2}", t)
    for m in months:
        tag = f"Tháng {m}"
        if tag not in tags:
            tags.append(tag)
    return tags


def tag_sort_key(tag):
    if tag.isdigit():
        return (0, int(tag))
    if tag.startswith("Quý"):
        return (1, int(tag.split()[1]))
    if tag.startswith("Tháng"):
        return (2, int(tag.split()[1]))
    return (3, 0)


def chrono_key(refers_to):
    """Khóa sắp xếp timeline theo thời điểm nói tới (năm*100 + tháng ước lượng)."""
    t = refers_to or ""
    ym = re.search(r"\b(20\d{2})\b", t)
    year = int(ym.group(1)) if ym else 9999
    month = 0
    mm = re.search(r"[Tt]háng\s*(1[0-2]|[1-9])", t)
    qm = re.search(r"[Qq]uý\s*([1-4])", t) or re.search(r"\bQ([1-4])\b", t)
    if mm:
        month = int(mm.group(1))
    elif qm:
        month = int(qm.group(1)) * 3 - 2
    else:
        sm = re.search(r"\b(1[0-2]|[1-9])/20\d{2}", t)
        if sm:
            month = int(sm.group(1))
    return year * 100 + month


def impact_badge(v):
    return {"Tích cực": ":green[● Tích cực]", "Tiêu cực": ":red[● Tiêu cực]"}.get(
        v, ":gray[● Trung lập]")


def insights_to_csv(insights):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Chủ đề", "Chuyên gia", "Kênh", "Nội dung", "Đánh giá", "Nói về",
                "Tag thời gian", "Mốc video", "Link mốc", "Ngày đăng", "Nguồn", "Link video"])
    for a in insights:
        w.writerow([a.get("topic", ""), a.get("expert", ""), a.get("channel", ""),
                    a.get("content", ""), a.get("impact", ""), a.get("refers_to", ""),
                    "; ".join(parse_time_tags(a.get("refers_to", ""))),
                    a.get("video_timestamp", ""), a.get("video_url_at", ""),
                    a.get("posted_at", ""), a.get("source", ""), a.get("video_url", "")])
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
    new_videos, new_insights, skipped = {}, [], 0
    for item in parsed:
        vid = extract_video_id(item.get("video", "") or item.get("link", "") or item.get("url", ""))
        if not vid:
            skipped += 1
            continue
        url = f"https://youtu.be/{vid}"
        channel = (item.get("channel") or "(không rõ kênh)").strip()
        posted = (item.get("posted_at") or item.get("published") or "").strip()
        new_videos[vid] = {"channel": channel, "title": item.get("title", ""),
                           "published": posted, "url": url}
        for ins in item.get("insights", []):
            if not ins.get("content"):
                continue
            topic = ins.get("topic", "")
            if topic not in topics:
                topic = topics[-1] if topics else "Khác"
            impact = ins.get("impact", "Trung lập")
            if impact not in IMPACTS:
                impact = "Trung lập"
            ts = ins.get("timestamp", "")
            raw = vid + topic + ins.get("content", "")[:30]
            new_insights.append({
                "id": hashlib.md5(raw.encode("utf-8")).hexdigest(),
                "video_id": vid, "channel": channel,
                "expert": (ins.get("expert", "") or channel).strip(),
                "topic": topic, "content": ins.get("content", "").strip(),
                "impact": impact, "video_timestamp": ts,
                "video_url_at": url + (f"?t={ts_to_seconds(ts)}" if ts else ""),
                "video_title": item.get("title", ""), "video_url": url,
                "posted_at": posted, "refers_to": ins.get("refers_to", "").strip(),
                "source": "thủ công", "created_at": ""})
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


def try_unlock(key):
    pw_set = get_secret("ADMIN_PASSWORD")
    if not pw_set:
        st.caption("⚠️ Chưa đặt ADMIN_PASSWORD nên chưa sửa được.")
        return False
    if st.session_state.get("is_admin"):
        return True
    with st.expander("🔒 Nhập mật khẩu để sửa"):
        pw = st.text_input("Mật khẩu quản trị", type="password", key="pw_" + key)
        if st.button("Mở khóa", key="unlock_" + key):
            if pw == pw_set:
                st.session_state["is_admin"] = True
                st.rerun()
            else:
                st.error("Sai mật khẩu.")
    return False


# ==================== Giao diện ====================

st.set_page_config(page_title="Nhận định chuyên gia", page_icon="🎙️", layout="wide")
cfg = load_config()
videos, insights, updated_at = load_data()
TOPICS = topic_names(cfg)

NAV_TOP = ["📊 Nhận định", "🧑‍💼 Chuyên gia"]
NAV_BOTTOM = ["✍️ Nhập tay", "⚙️ Cấu hình"]
if "page" not in st.session_state:
    st.session_state["page"] = NAV_TOP[0]
cur = st.session_state["page"]
with st.sidebar:
    st.markdown("### Xem")
    for lbl in NAV_TOP:
        if st.button(lbl, use_container_width=True,
                     type="primary" if lbl == cur else "secondary", key="nv_" + lbl):
            st.session_state["page"] = lbl
            st.rerun()
    st.markdown("<div style='height:30vh'></div>", unsafe_allow_html=True)
    st.divider()
    st.caption("Công cụ")
    for lbl in NAV_BOTTOM:
        if st.button(lbl, use_container_width=True,
                     type="primary" if lbl == cur else "secondary", key="nv_" + lbl):
            st.session_state["page"] = lbl
            st.rerun()
page = st.session_state["page"]


def render_insight(a):
    st.write(a.get("content", ""))
    bits = [impact_badge(a.get("impact") or "Trung lập")]
    if a.get("refers_to"):
        bits.append(f"🗓️ {a['refers_to']}")
    if a.get("video_timestamp"):
        bits.append(f"[▶️ {a['video_timestamp']}]({a.get('video_url_at','')})")
    if a.get("posted_at"):
        bits.append(f"📅 {a['posted_at'][:10]}")
    bits.append("🤖" if a.get("source") == "tự động" else "✍️")
    st.caption("  ·  ".join(bits))


# -------------------------------------------------- 📊 NHẬN ĐỊNH
if page == "📊 Nhận định":
    st.title("📊 Nhận định theo chủ đề")
    with st.sidebar:
        if updated_at:
            st.caption(f"Cập nhật: **{updated_at}**")
        st.caption(f"Tổng **{len(insights)}** nhận định")
        if insights:
            st.download_button("⬇️ Tải CSV", data=insights_to_csv(insights),
                               file_name="nhan_dinh.csv", mime="text/csv",
                               use_container_width=True)
        if st.button("🔄 Tải lại", use_container_width=True):
            st.rerun()

    if not insights:
        st.info("Chưa có nhận định. Dùng trang **Nhập tay** hoặc chờ luồng tự động.")
    else:
        all_tags = sorted({tag for a in insights for tag in parse_time_tags(a.get("refers_to", ""))},
                          key=tag_sort_key)
        c1, c2, c3 = st.columns([1.2, 1, 1])
        time_filter = c1.selectbox("Lọc theo thời điểm nói tới", ["Tất cả"] + all_tags)
        impact_filter = c2.selectbox("Lọc theo đánh giá", ["Tất cả"] + IMPACTS)
        edit_mode = False
        with c3:
            st.write("")
            if try_unlock("nd"):
                edit_mode = st.toggle("✏️ Chế độ sửa")

        # ----- CHẾ ĐỘ SỬA -----
        if edit_mode:
            st.info("Sửa tên chuyên gia/kênh ngay trong bảng; xóa dòng để bỏ nhận định. "
                    "Tích 'Xóa cả block' để xóa toàn bộ một chủ đề. Xong bấm **Lưu thay đổi**.")
            editors, del_block, orig_ids = {}, {}, {}
            for name in TOPICS:
                items = [a for a in insights if a.get("topic") == name]
                orig_ids[name] = {a["id"] for a in items}
                with st.expander(f"📦 {name} ({len(items)})", expanded=False):
                    rows = [{"id": a["id"], "Tác giả": a.get("expert", ""),
                             "Kênh": a.get("channel", ""), "Nội dung": a.get("content", "")[:120]}
                            for a in items]
                    editors[name] = st.data_editor(
                        rows, num_rows="dynamic", use_container_width=True, key="ed_" + name,
                        column_config={"id": st.column_config.TextColumn("mã", disabled=True),
                                       "Nội dung": st.column_config.TextColumn(disabled=True, width="large")})
                    del_block[name] = st.checkbox("🗑️ Xóa toàn bộ nhận định trong block này",
                                                  key="db_" + name)
            if st.button("💾 Lưu thay đổi", type="primary"):
                edits, deleted, del_topics = {}, set(), set()
                for name in TOPICS:
                    if del_block.get(name):
                        del_topics.add(name)
                        continue
                    present = set()
                    for r in editors.get(name, []):
                        rid = r.get("id")
                        if not rid:
                            continue
                        present.add(rid)
                        edits[rid] = (str(r.get("Tác giả") or "").strip(),
                                      str(r.get("Kênh") or "").strip())
                    deleted |= (orig_ids[name] - present)
                remote, _ = github_get_json(DATA_FILE)
                remote = remote or {"videos": {}, "insights": []}
                out = []
                for a in remote.get("insights", []):
                    if a.get("topic") in del_topics or a["id"] in deleted:
                        continue
                    if a["id"] in edits:
                        e, c = edits[a["id"]]
                        if e:
                            a["expert"] = e
                        if c:
                            a["channel"] = c
                    out.append(a)
                payload = {"updated_at": "(vừa sửa tay)", "videos": remote.get("videos", {}),
                           "insights": out}
                ok, msg = github_put_json(DATA_FILE, payload, "Sua nhan dinh")
                st.success(msg + " Tải lại sau ~1 phút.") if ok else st.error(msg)

        # ----- CHẾ ĐỘ XEM -----
        else:
            def keep(a):
                if impact_filter != "Tất cả" and (a.get("impact") or "Trung lập") != impact_filter:
                    return False
                if time_filter != "Tất cả" and time_filter not in parse_time_tags(a.get("refers_to", "")):
                    return False
                return True
            shown = [a for a in insights if keep(a)]
            rows = {}
            for order, t in enumerate(cfg["topics"]):
                nm = t["name"] if isinstance(t, dict) else t
                r = (t.get("row", 1) if isinstance(t, dict) else 1) or 1
                rows.setdefault(r, []).append((order, nm))

            def render_topic(name):
                items = [a for a in shown if a.get("topic") == name]
                with st.container(border=True, height=480):
                    st.markdown(f"#### {name}")
                    st.caption(f"{len(items)} nhận định")
                    by_expert = {}
                    for a in items:
                        by_expert.setdefault(a.get("expert", "(không rõ)"), []).append(a)
                    if not items:
                        st.caption("_Chưa có nhận định._")
                    for expert, arr in by_expert.items():
                        st.markdown(f"**🧑‍💼 {expert}**")
                        for a in arr:
                            render_insight(a)
                        st.divider()

            for r in sorted(rows.keys()):
                row_items = [n for _, n in sorted(rows[r], key=lambda x: x[0])]
                cols = st.columns(len(row_items))
                for col, name in zip(cols, row_items):
                    with col:
                        render_topic(name)

# -------------------------------------------------- 🧑‍💼 CHUYÊN GIA
elif page == "🧑‍💼 Chuyên gia":
    st.title("🧑‍💼 Nhận định theo chuyên gia")
    if not insights:
        st.info("Chưa có nhận định.")
    else:
        # danh sách chuyên gia: ưu tiên cấu hình, nếu trống thì tự suy từ dữ liệu
        cfg_experts = cfg["experts"]
        if cfg_experts:
            expert_rows = {}
            for order, e in enumerate(cfg_experts):
                nm = e["name"] if isinstance(e, dict) else e
                r = (e.get("row", 1) if isinstance(e, dict) else 1) or 1
                expert_rows.setdefault(r, []).append((order, nm))
        else:
            names = list(dict.fromkeys(a.get("expert", "(không rõ)") for a in insights))
            expert_rows = {1: [(i, n) for i, n in enumerate(names)]}

        c1, c2 = st.columns(2)
        topic_filter = c1.selectbox("Lọc theo chủ đề", ["Tất cả"] + TOPICS)
        all_tags = sorted({tag for a in insights for tag in parse_time_tags(a.get("refers_to", ""))},
                          key=tag_sort_key)
        time_filter = c2.selectbox("Lọc theo thời điểm nói tới", ["Tất cả"] + all_tags)

        def render_expert(name):
            items = [a for a in insights if a.get("expert") == name]
            if topic_filter != "Tất cả":
                items = [a for a in items if a.get("topic") == topic_filter]
            if time_filter != "Tất cả":
                items = [a for a in items if time_filter in parse_time_tags(a.get("refers_to", ""))]
            with st.container(border=True, height=520):
                st.markdown(f"#### 🧑‍💼 {name}")
                st.caption(f"{len(items)} nhận định")
                # timeline: nhóm theo thời điểm nói tới, sắp theo trình tự
                groups = {}
                for a in items:
                    key = a.get("refers_to", "").strip() or "(không rõ thời điểm)"
                    groups.setdefault(key, []).append(a)
                ordered = sorted(groups.keys(), key=lambda k: chrono_key(k) if k != "(không rõ thời điểm)" else 9999999)
                if not items:
                    st.caption("_Chưa có nhận định._")
                for period in ordered:
                    st.markdown(f"**🗓️ {period}**")
                    for a in groups[period]:
                        st.write(f"[{a.get('topic','')}] {a.get('content','')}")
                        bits = [impact_badge(a.get("impact") or "Trung lập")]
                        if a.get("video_timestamp"):
                            bits.append(f"[▶️ {a['video_timestamp']}]({a.get('video_url_at','')})")
                        if a.get("posted_at"):
                            bits.append(f"📅 {a['posted_at'][:10]}")
                        st.caption("  ·  ".join(bits))
                    st.divider()

        for r in sorted(expert_rows.keys()):
            row_items = [n for _, n in sorted(expert_rows[r], key=lambda x: x[0])]
            cols = st.columns(len(row_items))
            for col, name in zip(cols, row_items):
                with col:
                    render_expert(name)

# -------------------------------------------------- ✍️ NHẬP TAY
elif page == "✍️ Nhập tay":
    st.title("✍️ Nhập nhận định thủ công")
    st.caption("① Dán link → ② Copy prompt cho Gemini → ③ Dán kết quả → ④ Xem trước & Lưu.")

    st.subheader("① Dán link video (mỗi dòng một link)")
    link_text = st.text_area("Link video", height=110,
                             placeholder="https://youtu.be/...\nhttps://www.youtube.com/watch?v=...")
    reprocess = st.checkbox("🔁 Làm lại cả video đã xử lý trước đó",
                            help="Mặc định hệ thống bỏ qua video đã làm. Tích ô này nếu bạn "
                                 "đã xóa nhận định của một video và muốn xử lý lại nó.")
    raw_links = [l.strip() for l in link_text.splitlines() if l.strip()]
    new_links, done_links, bad = [], [], 0
    for l in raw_links:
        vid = extract_video_id(l)
        if not vid:
            bad += 1
            continue
        if vid in videos and not reprocess:
            done_links.append(l)
        else:
            new_links.append(l)
    new_links = list(dict.fromkeys(new_links))
    if done_links:
        st.caption(f"↩️ Bỏ qua {len(done_links)} video đã xử lý "
                   "(tích ô '🔁 Làm lại' ở trên nếu muốn xử lý lại).")
    if bad:
        st.caption(f"⚠️ {bad} dòng không phải link YouTube.")

    st.subheader("② Copy khối này dán vào Gemini")
    if new_links:
        block = (cfg["manual_prompt_template"].replace("{topics}", build_topic_guide(cfg))
                 .replace("{links}", "\n".join(new_links)))
        st.caption(f"Gồm {len(new_links)} video mới — bấm biểu tượng copy ở góc khối:")
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
            st.dataframe([{"Chủ đề": a["topic"], "Chuyên gia": a["expert"], "Kênh": a["channel"],
                           "Đánh giá": a["impact"], "Nội dung": a["content"][:70],
                           "Mốc": a["video_timestamp"], "Nói về": a["refers_to"]} for a in ni],
                         use_container_width=True, hide_index=True)
            if st.button("💾 Lưu vào hệ thống", type="primary"):
                remote, _ = github_get_json(DATA_FILE)
                remote = remote or {"videos": {}, "insights": []}
                rv = remote.get("videos", {})
                rv.update(preview["videos"])
                existing = {i["id"] for i in remote.get("insights", [])}
                merged = [i for i in ni if i["id"] not in existing] + remote.get("insights", [])
                payload = {"updated_at": "(vừa cập nhật tay)", "videos": rv, "insights": merged}
                ok, msg = github_put_json(DATA_FILE, payload, "Them nhan dinh thu cong")
                if ok:
                    added = len([i for i in ni if i["id"] not in existing])
                    st.session_state.pop("preview", None)
                    st.success(f"Đã lưu {added} nhận định. Mở trang Nhận định và Tải lại sau ~1 phút.")
                else:
                    st.error(msg)
        else:
            st.warning("Không tách được nhận định nào — kiểm tra định dạng Gemini trả về.")

# -------------------------------------------------- ⚙️ CẤU HÌNH
else:
    st.title("⚙️ Cấu hình")
    if not try_unlock("cfg"):
        st.stop()
    st.success("Đã mở khóa. Chỉnh xong bấm **Lưu tất cả** ở cuối.")

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

    st.subheader("Chủ đề — trang Nhận định (Hàng = bố cục, Từ khóa = gợi ý cho AI)")
    st.caption("Từ khóa ngăn nhau bằng dấu phẩy; được đưa vào prompt để AI phân loại đúng chủ đề.")
    tp_rows = [{"Tên chủ đề": (t["name"] if isinstance(t, dict) else t),
                "Hàng": (t.get("row", 1) if isinstance(t, dict) else 1),
                "Từ khóa": ", ".join(t.get("keywords", [])) if isinstance(t, dict) else ""}
               for t in cfg["topics"]] or [{"Tên chủ đề": "", "Hàng": 1, "Từ khóa": ""}]
    tp_edit = st.data_editor(tp_rows, num_rows="dynamic", use_container_width=True, key="tp_editor",
                             column_config={"Tên chủ đề": st.column_config.TextColumn(width="medium"),
                                            "Hàng": st.column_config.NumberColumn(min_value=1, max_value=20, step=1),
                                            "Từ khóa": st.column_config.TextColumn(width="large")})

    st.subheader("Chuyên gia — trang Chuyên gia (cột Hàng = bố cục)")
    st.caption("Tên phải khớp tên chuyên gia trong dữ liệu. Để trống bảng này thì trang "
               "Chuyên gia tự liệt kê mọi chuyên gia tìm thấy.")
    ex_rows = [{"Tên chuyên gia": (e["name"] if isinstance(e, dict) else e),
                "Hàng": (e.get("row", 1) if isinstance(e, dict) else 1)} for e in cfg["experts"]] \
        or [{"Tên chuyên gia": "", "Hàng": 1}]
    ex_edit = st.data_editor(ex_rows, num_rows="dynamic", use_container_width=True, key="ex_editor",
                             column_config={"Tên chuyên gia": st.column_config.TextColumn(width="large"),
                                            "Hàng": st.column_config.NumberColumn(min_value=1, max_value=20, step=1)})

    st.subheader("Prompt luồng tự động")
    auto_prompt = st.text_area("Prompt (AI đọc transcript)", value=cfg["prompt_instructions"], height=110)
    st.subheader("Prompt mẫu cho Gemini (nhập tay)")
    st.caption("Giữ nguyên {topics} và {links} — hệ thống tự điền.")
    manual_tpl = st.text_area("Prompt mẫu", value=cfg["manual_prompt_template"], height=200)

    st.markdown("---")
    if st.button("💾 Lưu tất cả", type="primary"):
        channels = [{"id": "ch_" + hashlib.md5((r.get("Link kênh") or "").encode()).hexdigest()[:6],
                     "name": (r.get("Tên kênh") or "").strip(), "url": (r.get("Link kênh") or "").strip()}
                    for r in ch_edit if (r.get("Tên kênh") or "").strip() and (r.get("Link kênh") or "").strip()]
        topics = [{"name": (r.get("Tên chủ đề") or "").strip(), "row": int(r.get("Hàng", 1) or 1),
                   "keywords": [k.strip() for k in (r.get("Từ khóa") or "").split(",") if k.strip()]}
                  for r in tp_edit if (r.get("Tên chủ đề") or "").strip()]
        experts = [{"name": (r.get("Tên chuyên gia") or "").strip(), "row": int(r.get("Hàng", 1) or 1)}
                   for r in ex_edit if (r.get("Tên chuyên gia") or "").strip()]
        if not channels or not topics:
            st.error("Cần ít nhất một kênh và một chủ đề.")
        else:
            new_cfg = {
                "model": st.session_state.get("model_select", DEFAULT_MODEL),
                "update_hours": sorted(st.session_state.get("hours_select", [])),
                "channels": channels, "topics": topics, "experts": experts,
                "prompt_instructions": auto_prompt.strip() or DEFAULT_AUTO_PROMPT,
                "manual_prompt_template": manual_tpl.strip() or DEFAULT_MANUAL_TEMPLATE,
            }
            ok, msg = github_put_json(CONFIG_FILE, new_cfg, "Cap nhat cau hinh")
            st.success(msg + " Khởi động lại sau ~1 phút.") if ok else st.error(msg)
