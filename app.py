# -*- coding: utf-8 -*-
"""
DASHBOARD TIN TỨC KINH TẾ — nhiều block + trang Cấu hình (admin)
----------------------------------------------------------------
- Trang Tin tức: hiển thị từng block theo lưới, mỗi block là một chủ đề.
- Trang Cấu hình (mật khẩu): thêm/bớt block; mỗi block chỉnh tên, từ khóa,
  nguồn RSS, prompt, giờ cập nhật. Lưu sẽ ghi config.json lên GitHub.

Secrets cần cho phần Cấu hình:
  ADMIN_PASSWORD, GH_TOKEN, GH_REPO  (vd "tendangnhap/dashboard-kinh-te")
"""

import os
import json
import base64
import uuid

import requests
import streamlit as st

CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
IMPACTS = ["Tích cực", "Tiêu cực", "Trung lập"]
DEFAULT_PROMPT = ("Bạn là chuyên gia phân tích tin kinh tế. Hãy tóm tắt CỐT LÕI của bài "
                  "trong 5 đến 10 câu ngắn gọn bằng tiếng Việt, tập trung nêu rõ các con số, "
                  "số liệu cụ thể nếu bài có.")
GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.5-flash",
                 "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]
DEFAULT_MODEL = GEMINI_MODELS[0]
COLS_PER_ROW = 2


# ---------------- Đọc dữ liệu & cấu hình ----------------

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("blocks", {}), d.get("updated_at", "")
        except Exception:
            return {}, ""
    return {}, ""


def load_blocks():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                c = json.load(f)
            blocks = st.session_state.get("live_blocks", c.get("blocks", []))
            prompt = st.session_state.get("live_prompt", c.get("prompt_instructions", DEFAULT_PROMPT))
            model = st.session_state.get("live_model", c.get("model") or DEFAULT_MODEL)
            return blocks, prompt, model
        except Exception:
            pass
    return (st.session_state.get("live_blocks", []),
            st.session_state.get("live_prompt", DEFAULT_PROMPT),
            st.session_state.get("live_model", DEFAULT_MODEL))


def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def github_save(blocks, prompt, model):
    token, repo = get_secret("GH_TOKEN"), get_secret("GH_REPO")
    if not token or not repo:
        return False, "Chưa khai báo GH_TOKEN / GH_REPO trong Secrets."
    api = f"https://api.github.com/repos/{repo}/contents/{CONFIG_FILE}"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    content_str = json.dumps({"model": model, "prompt_instructions": prompt, "blocks": blocks},
                             ensure_ascii=False, indent=2)
    sha = None
    try:
        r = requests.get(api, headers=headers, timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        return False, f"Không kết nối được GitHub: {e}"
    body = {"message": "Cap nhat cau hinh block",
            "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    try:
        r2 = requests.put(api, headers=headers, json=body, timeout=20)
    except Exception as e:
        return False, f"Không gửi được lên GitHub: {e}"
    if r2.status_code in (200, 201):
        return True, "Đã lưu cấu hình lên GitHub."
    return False, f"GitHub trả lỗi {r2.status_code}: {r2.text[:200]}"


def impact_label(impact):
    if impact == "Tích cực":
        return ":green[● Tích cực]"
    if impact == "Tiêu cực":
        return ":red[● Tiêu cực]"
    return ":gray[● Trung lập]"


# ---------------- Giao diện ----------------

st.set_page_config(page_title="Dashboard Kinh tế", page_icon="📊", layout="wide")

blocks, global_prompt, global_model = load_blocks()
blocks_data, updated_at = load_data()

page = st.sidebar.radio("Trang", ["📊 Tin tức", "⚙️ Cấu hình"])

# ============================ TRANG TIN TỨC ============================
if page == "📊 Tin tức":
    st.title("📊 Dashboard tin tức kinh tế")
    with st.sidebar:
        if updated_at:
            st.caption(f"Cập nhật gần nhất: **{updated_at}** (giờ VN)")
        if st.button("🔄 Tải lại trang", use_container_width=True):
            st.rerun()

    if not blocks:
        st.info("Chưa có block nào. Vào trang **Cấu hình** để tạo block.")
    else:
        # Lọc nhanh theo tác động (áp dụng cho mọi block)
        impact_filter = st.selectbox("Lọc theo tác động", ["Tất cả"] + IMPACTS)

        # Nhóm block theo "hàng" đã cấu hình. Block chưa đặt hàng thì xếp xuống cuối.
        rows = {}
        for order, block in enumerate(blocks):
            r = block.get("row", 0) or (9999)   # chưa đặt -> dồn về nhóm cuối
            rows.setdefault(r, []).append((order, block))

        def render_block(block):
            bid = block.get("id")
            bdata = blocks_data.get(bid, {})
            arts = bdata.get("articles", [])
            if impact_filter != "Tất cả":
                arts = [a for a in arts
                        if (a.get("impact") or "Trung lập") == impact_filter]
            with st.container(border=True, height=460):
                st.markdown(f"#### {block.get('name', bid)}")
                upd = bdata.get("updated_at")
                st.caption(f"{len(arts)} tin" + (f" · cập nhật {upd}" if upd else ""))
                if not arts:
                    st.caption("_Chưa có tin._")
                for a in arts:
                    st.markdown(f"**[{a.get('title','')}]({a.get('link','')})**")
                    if a.get("ai", True):
                        badge = impact_label(a.get('impact') or 'Trung lập')
                    else:
                        badge = ":orange[● Tin nhanh (chưa qua AI)]"
                    st.markdown(f"{badge}  ·  📰 {a.get('source','')}")
                    if a.get("summary"):
                        st.caption(a["summary"])
                    st.divider()

        for r in sorted(rows.keys()):
            row_items = [b for _, b in sorted(rows[r], key=lambda x: x[0])]
            cols = st.columns(len(row_items))
            for col, block in zip(cols, row_items):
                with col:
                    render_block(block)

# ============================ TRANG CẤU HÌNH ============================
else:
    st.title("⚙️ Cấu hình các block")

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

    # Nạp bản nháp các block vào session (chỉ lần đầu)
    if "draft" not in st.session_state:
        st.session_state["draft"] = [dict(b) for b in blocks]

    st.success("Đã mở khóa. Chỉnh xong nhớ bấm **Lưu tất cả** ở cuối trang.")

    # Khởi tạo giá trị ban đầu (chỉ lần đầu) để widget không bị xung đột value/state
    if "model_select" not in st.session_state:
        st.session_state["model_select"] = global_model if global_model in GEMINI_MODELS else DEFAULT_MODEL
    if "global_prompt" not in st.session_state:
        st.session_state["global_prompt"] = global_prompt

    st.subheader("Model AI")
    st.selectbox("Chọn model", GEMINI_MODELS, key="model_select",
                 help="Nếu model báo hết lượt, chọn model khác ở đây rồi Lưu — "
                      "không cần sửa fetch.py.")

    st.subheader("Prompt chung cho AI")
    st.caption("Dùng chung cho mọi block (vì mỗi bài chỉ gọi AI một lần). Hệ thống tự "
               "thêm yêu cầu chọn block và đánh giá tác động, bạn chỉ cần mô tả cách tóm tắt.")
    if st.button("↩️ Khôi phục prompt mặc định"):
        st.session_state["global_prompt"] = DEFAULT_PROMPT
        st.rerun()
    st.text_area("Prompt", key="global_prompt", height=140)
    st.markdown("---")
    st.subheader("Các block")

    if st.button("➕ Thêm block mới"):
        st.session_state["draft"].append({
            "id": "blk_" + uuid.uuid4().hex[:6],
            "name": "Block mới", "topics": [],
            "rss_feeds": {"VnExpress - Kinh doanh": "https://vnexpress.net/rss/kinh-doanh.rss"},
            "update_hours": [7], "row": 1,
        })
        st.rerun()

    rss_edits = {}   # id -> dữ liệu bảng RSS đã sửa
    for blk in st.session_state["draft"]:
        bid = blk["id"]
        title = st.session_state.get(f"name_{bid}", blk.get("name", "(block)"))
        with st.expander(f"📦 {title}", expanded=False):
            st.text_input("Tên block", value=blk.get("name", ""), key=f"name_{bid}")

            st.text_area("Từ khóa của block (mỗi dòng một từ)",
                         value="\n".join(blk.get("topics", [])),
                         key=f"topics_{bid}", height=120,
                         help="Chỉ bài có chứa một trong các từ khóa này mới được xét vào block.")

            st.caption("Nguồn RSS (gõ vào ô, bấm dòng trống cuối để thêm):")
            rss_rows = [{"Tên nguồn": k, "Link RSS": v}
                        for k, v in blk.get("rss_feeds", {}).items()]
            if not rss_rows:
                rss_rows = [{"Tên nguồn": "", "Link RSS": ""}]
            rss_edits[bid] = st.data_editor(
                rss_rows, num_rows="dynamic", use_container_width=True,
                key=f"rss_{bid}",
                column_config={
                    "Tên nguồn": st.column_config.TextColumn("Tên nguồn", width="medium"),
                    "Link RSS": st.column_config.TextColumn("Link RSS", width="large"),
                })

            st.multiselect("Giờ cập nhật (giờ Việt Nam)", options=list(range(24)),
                           default=blk.get("update_hours", []),
                           format_func=lambda h: f"{h}h", key=f"hours_{bid}",
                           help="Block chỉ cập nhật vào những giờ này. Muốn AI cân nhắc "
                                "một bài giữa nhiều block, đặt chúng cùng giờ.")

            st.number_input("Hiển thị ở hàng số mấy", min_value=1, max_value=20, step=1,
                            value=int(blk.get("row", 1) or 1), key=f"row_{bid}",
                            help="Các block cùng số hàng sẽ nằm cạnh nhau trên một hàng. "
                                 "Ví dụ: GDP và Lạm phát cùng để hàng 1.")

            if st.button("🗑️ Xóa block này", key=f"del_{bid}"):
                st.session_state["draft"] = [b for b in st.session_state["draft"]
                                             if b["id"] != bid]
                st.rerun()

    st.markdown("---")
    if st.button("💾 Lưu tất cả", type="primary"):
        new_blocks = []
        for blk in st.session_state["draft"]:
            bid = blk["id"]
            name = (st.session_state.get(f"name_{bid}", "") or "").strip()
            topics = [t.strip() for t in
                      st.session_state.get(f"topics_{bid}", "").splitlines() if t.strip()]
            rss = {}
            for row in rss_edits.get(bid, []):
                n = (row.get("Tên nguồn") or "").strip()
                l = (row.get("Link RSS") or "").strip()
                if n and l:
                    rss[n] = l
            hours = sorted(st.session_state.get(f"hours_{bid}", []))
            row = int(st.session_state.get(f"row_{bid}", 1) or 1)
            if name and rss:
                new_blocks.append({"id": bid, "name": name, "topics": topics,
                                   "rss_feeds": rss, "update_hours": hours, "row": row})
        prompt = (st.session_state.get("global_prompt", "") or "").strip() or DEFAULT_PROMPT
        model = st.session_state.get("model_select", DEFAULT_MODEL)
        if not new_blocks:
            st.error("Cần ít nhất một block có tên và có nguồn RSS.")
        else:
            ok, msg = github_save(new_blocks, prompt, model)
            if ok:
                st.session_state["live_blocks"] = new_blocks
                st.session_state["live_prompt"] = prompt
                st.session_state["live_model"] = model
                st.session_state["draft"] = [dict(b) for b in new_blocks]
                st.success(msg + " Robot sẽ dùng cấu hình mới từ lần chạy kế tiếp.")
            else:
                st.error(msg)
