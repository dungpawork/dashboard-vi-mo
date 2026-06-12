# -*- coding: utf-8 -*-
"""
DASHBOARD NHẬN ĐỊNH CHUYÊN GIA (YouTube) — 3 trang:
  📊 Tin tức   : block theo chủ đề, gom theo chuyên gia, tải CSV
  ✍️ Nhập tay  : lấy video mới -> copy prompt+link cho Gemini -> dán kết quả -> lưu
  ⚙️ Cấu hình  : kênh YouTube, 7 chủ đề, model, prompt (có mật khẩu)

Secrets cần: ADMIN_PASSWORD, GH_TOKEN, GH_REPO
Thư viện: streamlit, requests, feedparser
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
DEFAULT_AUTO_PROMPT = ("Bạn là trợ lý phân tích kinh tế. Đọc bản ghi (có mốc thời gian) "
                       "của video và rút ra các NHẬN ĐỊNH kinh tế quan trọng, tóm tắt "
                       "mỗi nhận định 2-4 câu, nêu rõ số liệu nếu có.")
DEFAULT_MANUAL_TEMPLATE = (
    "Bạn là trợ lý phân tích kinh tế. Với MỖI link video dưới đây, hãy xem video và rút "
    "ra các nhận định kinh tế quan trọng.\n\nPhân loại mỗi nhận định vào ĐÚNG MỘT chủ đề: "
    "{topics}\n\nVới mỗi nhận định: expert (tên chuyên gia, không rõ thì ghi tên kênh), "
    "topic (trong danh sách trên), content (2-4 câu, nêu số liệu), timestamp (mm:ss), "
    "refers_to (thời điểm nhận định nói tới, vd \"Tháng 6/2026\", không rõ để \"\").\n\n"
    "CHỈ trả về JSON: [{\"video\":\"<link>\",\"insights\":[{\"expert\":\"...\",\"topic\":"
    "\"...\",\"content\":\"...\",\"timestamp\":\"mm:ss\",\"refers_to\":\"...\"}]}]\n\n"
    "Danh sách video:\n{links}")


# ==================== Đọc cấu hình & dữ liệu (file cục bộ) ====================

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
    """Trả về (dict_hoặc_None, sha_hoặc_None)."""
    repo = get_secret("GH_REPO")
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    try:
        r = requests.get(api, headers=_gh_headers(), timeout=20)
        if r.status_code == 200:
            j = r.json()
            content = base64.b64decode(j["content"]).decode("utf-8")
            return json.loads(content), j.get("sha")
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
            continue   # sha cũ, thử lại
        return False, f"GitHub lỗi {r2.status_code}: {r2.text[:150]}"
    return False, "Xung đột khi lưu, thử lại sau ít phút."


# ==================== Tiện ích YouTube / nhận định ====================

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


def resolve_channel_id(url):
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", url or "")
    if m:
        return m.group(1)
    try:
        r = requests.get((url or "").split("?")[0], timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
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
                        "published": e.get("published", ""),
                        "url": "https://youtu.be/" + vid})
    return out


def insights_to_csv(insights):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Chủ đề", "Chuyên gia", "Kênh", "Nội dung nhận định", "Mốc trong video",
                "Link tới mốc", "Thời điểm post video", "Thời điểm nhận định nói tới",
                "Nguồn", "Link video"])
    for a in insights:
        w.writerow([a.get("topic", ""), a.get("expert", ""), a.get("channel", ""),
                    a.get("content", ""), a.get("video_timestamp", ""),
                    a.get("video_url_at", ""), a.get("posted_at", ""),
                    a.get("refers_to", ""), a.get("source", ""), a.get("video_url", "")])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")   # BOM để Excel đọc tiếng Việt


# ==================== Giao diện ====================

st.set_page_config(page_title="Nhận định chuyên gia", page_icon="🎙️", layout="wide")
cfg = load_config()
videos, insights, updated_at = load_data()
TOPICS = topic_names(cfg)

page = st.sidebar.radio("Trang", ["📊 Tin tức", "✍️ Nhập tay", "⚙️ Cấu hình"])

# -------------------------------------------------- TRANG TIN TỨC
if page == "📊 Tin tức":
    st.title("🎙️ Nhận định chuyên gia theo chủ đề")
    with st.sidebar:
        if updated_at:
            st.caption(f"Cập nhật gần nhất: **{updated_at}**")
        st.caption(f"Tổng **{len(insights)}** nhận định")
        if insights:
            st.download_button("⬇️ Tải CSV (mở bằng Excel)",
                               data=insights_to_csv(insights),
                               file_name="nhan_dinh.csv", mime="text/csv",
                               use_container_width=True)
        if st.button("🔄 Tải lại trang", use_container_width=True):
            st.rerun()

    if not insights:
        st.info("Chưa có nhận định nào. Dùng trang **Nhập tay** hoặc chờ luồng tự động.")
    else:
        # nhóm chủ đề theo hàng
        rows = {}
        for order, t in enumerate(cfg["topics"]):
            name = t["name"] if isinstance(t, dict) else t
            r = (t.get("row", 1) if isinstance(t, dict) else 1) or 1
            rows.setdefault(r, []).append((order, name))

        def render_topic(name):
            items = [a for a in insights if a.get("topic") == name]
            with st.container(border=True, height=480):
                st.markdown(f"#### {name}")
                st.caption(f"{len(items)} nhận định")
                # gom theo chuyên gia
                by_expert = {}
                for a in items:
                    by_expert.setdefault(a.get("expert", "(không rõ)"), []).append(a)
                if not items:
                    st.caption("_Chưa có nhận định._")
                for expert, arr in by_expert.items():
                    st.markdown(f"**🧑‍💼 {expert}**")
                    for a in arr:
                        line = a.get("content", "")
                        st.write(line)
                        bits = []
                        if a.get("refers_to"):
                            bits.append(f"🗓️ nói về: {a['refers_to']}")
                        if a.get("video_timestamp"):
                            bits.append(f"[▶️ {a['video_timestamp']}]({a.get('video_url_at','')})")
                        if a.get("posted_at"):
                            bits.append(f"📅 đăng: {a['posted_at'][:10]}")
                        tag = "🤖" if a.get("source") == "tự động" else "✍️"
                        bits.append(tag)
                        st.caption("  ·  ".join(bits))
                    st.divider()

        for r in sorted(rows.keys()):
            row_items = [n for _, n in sorted(rows[r], key=lambda x: x[0])]
            cols = st.columns(len(row_items))
            for col, name in zip(cols, row_items):
                with col:
                    render_topic(name)

# -------------------------------------------------- TRANG NHẬP TAY
elif page == "✍️ Nhập tay":
    st.title("✍️ Nhập nhận định thủ công")
    st.caption("Quy trình: ① Lấy video mới → ② Copy prompt+link → ③ Dán vào Gemini → "
               "④ Dán kết quả vào đây và Lưu.")

    # ① Lấy video mới
    st.subheader("① Lấy video mới từ các kênh")
    if st.button("🔍 Quét video mới"):
        found = []
        with st.spinner("Đang quét các kênh..."):
            for ch in cfg["channels"]:
                for v in list_channel_videos(ch.get("url", ""), 5):
                    if v["id"] not in videos:
                        v["channel"] = ch.get("name", "")
                        found.append(v)
        st.session_state["found_videos"] = found
    found = st.session_state.get("found_videos", [])
    if found:
        st.write(f"Tìm thấy **{len(found)}** video mới chưa xử lý:")
        for v in found:
            st.write(f"- [{v['title'][:70]}]({v['url']}) — {v.get('channel','')}")
    st.caption("Nếu nút quét không ra video (do YouTube chặn máy chủ), dán link thủ công bên dưới:")
    manual_links = st.text_area("Dán thêm link video (mỗi dòng một link)", height=80)

    # ② Tạo khối prompt + link để copy
    st.subheader("② Copy khối này dán vào Gemini")
    links = [v["url"] for v in found]
    links += [l.strip() for l in manual_links.splitlines() if l.strip()]
    links = list(dict.fromkeys(links))   # bỏ trùng
    if links:
        block = (cfg["manual_prompt_template"]
                 .replace("{topics}", ", ".join(TOPICS))
                 .replace("{links}", "\n".join(links)))
        st.code(block, language="text")
    else:
        st.info("Chưa có link. Bấm Quét video mới hoặc dán link ở trên.")

    # ③ + ④ Dán kết quả Gemini và lưu
    st.subheader("③ Dán kết quả JSON từ Gemini vào đây")
    pasted = st.text_area("Kết quả từ Gemini", height=220,
                          placeholder='[{"video":"...","insights":[...]}]')
    if st.button("💾 Lưu nhận định", type="primary"):
        try:
            s = pasted[pasted.index("["): pasted.rindex("]") + 1]
            parsed = json.loads(s)
        except Exception:
            st.error("Không đọc được JSON. Hãy chắc bạn dán đúng phần Gemini trả về.")
            parsed = None
        if parsed is not None:
            vmeta = {v["id"]: v for v in found}
            new_videos, new_insights, skipped = {}, [], 0
            for item in parsed:
                vid = extract_video_id(item.get("video", ""))
                if not vid:
                    skipped += 1
                    continue
                meta = vmeta.get(vid, {"title": "", "published": "",
                                       "url": f"https://youtu.be/{vid}", "channel": "(thủ công)"})
                new_videos[vid] = {"channel": meta.get("channel", "(thủ công)"),
                                   "title": meta.get("title", ""),
                                   "published": meta.get("published", ""),
                                   "url": meta.get("url", f"https://youtu.be/{vid}")}
                for ins in item.get("insights", []):
                    if not ins.get("content"):
                        continue
                    topic = ins.get("topic", "")
                    if topic not in TOPICS:
                        topic = TOPICS[-1] if TOPICS else "Khác"
                    ts = ins.get("timestamp", "")
                    url_at = meta["url"] + (f"?t={ts_to_seconds(ts)}" if ts else "")
                    raw = vid + topic + ins.get("content", "")[:30]
                    new_insights.append({
                        "id": hashlib.md5(raw.encode("utf-8")).hexdigest(),
                        "video_id": vid, "channel": meta.get("channel", "(thủ công)"),
                        "expert": (ins.get("expert", "") or meta.get("channel", "")).strip(),
                        "topic": topic, "content": ins.get("content", "").strip(),
                        "video_timestamp": ts, "video_url_at": url_at,
                        "video_title": meta.get("title", ""), "video_url": meta["url"],
                        "posted_at": meta.get("published", ""),
                        "refers_to": ins.get("refers_to", "").strip(),
                        "source": "thủ công", "created_at": ""})
            if not new_insights:
                st.warning("Không tách được nhận định nào từ nội dung đã dán.")
            else:
                remote, _ = github_get_json(DATA_FILE)
                remote = remote or {"videos": {}, "insights": []}
                rv = remote.get("videos", {}); rv.update(new_videos)
                existing_ids = {i["id"] for i in remote.get("insights", [])}
                merged = [i for i in new_insights if i["id"] not in existing_ids] \
                    + remote.get("insights", [])
                payload = {"updated_at": "(vừa cập nhật tay)", "videos": rv, "insights": merged}
                ok, msg = github_put_json(DATA_FILE, payload, "Them nhan dinh thu cong")
                if ok:
                    st.success(f"Đã lưu {len(new_insights)} nhận định"
                               + (f" ({skipped} video bỏ qua do thiếu link)" if skipped else "")
                               + ". Mở trang Tin tức và Tải lại sau ~1 phút.")
                else:
                    st.error(msg)

# -------------------------------------------------- TRANG CẤU HÌNH
else:
    st.title("⚙️ Cấu hình")
    admin_pw = get_secret("ADMIN_PASSWORD")
    if not admin_pw:
        st.warning("Chưa đặt ADMIN_PASSWORD trong Secrets nên trang này đang khóa.")
        st.stop()
    if not st.session_state.get("is_admin"):
        pw = st.text_input("Mật khẩu quản trị", type="password")
        if st.button("Mở khóa"):
            if pw == admin_pw:
                st.session_state["is_admin"] = True
                st.rerun()
            else:
                st.error("Sai mật khẩu.")
        st.stop()

    st.success("Đã mở khóa. Chỉnh xong bấm **Lưu tất cả** ở cuối.")

    # Model + lịch
    if "model_select" not in st.session_state:
        st.session_state["model_select"] = cfg["model"] if cfg["model"] in GEMINI_MODELS else DEFAULT_MODEL
    st.subheader("Model AI (cho luồng tự động)")
    st.selectbox("Model", GEMINI_MODELS, key="model_select")
    st.subheader("Giờ chạy luồng tự động (giờ VN)")
    hours = st.multiselect("Giờ", list(range(24)), default=cfg["update_hours"],
                           format_func=lambda h: f"{h}h", key="hours_select")

    # Kênh YouTube
    st.subheader("Kênh YouTube (nguồn chuyên gia)")
    st.caption("Thêm/bớt kênh. Mỗi kênh gồm tên hiển thị và link kênh (dạng youtube.com/@...).")
    ch_rows = [{"Tên kênh": c.get("name", ""), "Link kênh": c.get("url", "")}
               for c in cfg["channels"]] or [{"Tên kênh": "", "Link kênh": ""}]
    ch_edit = st.data_editor(ch_rows, num_rows="dynamic", use_container_width=True,
                             key="ch_editor",
                             column_config={"Tên kênh": st.column_config.TextColumn(width="medium"),
                                            "Link kênh": st.column_config.TextColumn(width="large")})

    # Chủ đề
    st.subheader("Chủ đề (mỗi chủ đề là một block)")
    st.caption("Cột Hàng quyết định bố cục (các chủ đề cùng số hàng nằm cạnh nhau).")
    tp_rows = [{"Tên chủ đề": (t["name"] if isinstance(t, dict) else t),
                "Hàng": (t.get("row", 1) if isinstance(t, dict) else 1)} for t in cfg["topics"]] \
        or [{"Tên chủ đề": "", "Hàng": 1}]
    tp_edit = st.data_editor(tp_rows, num_rows="dynamic", use_container_width=True,
                             key="tp_editor",
                             column_config={"Tên chủ đề": st.column_config.TextColumn(width="large"),
                                            "Hàng": st.column_config.NumberColumn(min_value=1, max_value=20, step=1)})

    # Prompts
    st.subheader("Prompt luồng tự động")
    auto_prompt = st.text_area("Prompt (AI đọc transcript)", value=cfg["prompt_instructions"], height=120)
    st.subheader("Prompt mẫu cho Gemini (luồng nhập tay)")
    st.caption("Giữ nguyên hai chỗ {topics} và {links} — hệ thống tự điền chủ đề và link vào.")
    manual_tpl = st.text_area("Prompt mẫu", value=cfg["manual_prompt_template"], height=200)

    st.markdown("---")
    if st.button("💾 Lưu tất cả", type="primary"):
        channels = []
        for row in ch_edit:
            name = (row.get("Tên kênh") or "").strip()
            url = (row.get("Link kênh") or "").strip()
            if name and url:
                channels.append({"id": "ch_" + hashlib.md5(url.encode()).hexdigest()[:6],
                                 "name": name, "url": url})
        topics = []
        for row in tp_edit:
            nm = (row.get("Tên chủ đề") or "").strip()
            if nm:
                topics.append({"name": nm, "row": int(row.get("Hàng", 1) or 1)})
        if not channels or not topics:
            st.error("Cần ít nhất một kênh và một chủ đề.")
        else:
            new_cfg = {
                "model": st.session_state.get("model_select", DEFAULT_MODEL),
                "update_hours": sorted(st.session_state.get("hours_select", [])),
                "channels": channels, "topics": topics,
                "prompt_instructions": auto_prompt.strip() or DEFAULT_AUTO_PROMPT,
                "manual_prompt_template": manual_tpl.strip() or DEFAULT_MANUAL_TEMPLATE,
            }
            ok, msg = github_put_json(CONFIG_FILE, new_cfg, "Cap nhat cau hinh")
            st.success(msg + " Khởi động lại sau ~1 phút để áp dụng.") if ok else st.error(msg)
