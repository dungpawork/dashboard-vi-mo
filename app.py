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
DEFAULT_PROMPT = "Tóm tắt CỐT LÕI bài viết trong 5 đến 10 câu, nêu rõ số liệu nếu có."
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
    if "live_blocks" in st.session_state:
        return st.session_state["live_blocks"]
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("blocks", [])
        except Exception:
            pass
    return []


def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def github_save(blocks):
    token, repo = get_secret("GH_TOKEN"), get_secret("GH_REPO")
    if not token or not repo:
        return False, "Chưa khai báo GH_TOKEN / GH_REPO trong Secrets."
    api = f"https://api.github.com/repos/{repo}/contents/{CONFIG_FILE}"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    content_str = json.dumps({"blocks": blocks}, ensure_ascii=False, indent=2)
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

blocks = load_blocks()
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

        for i in range(0, len(blocks), COLS_PER_ROW):
            row_blocks = blocks[i:i + COLS_PER_ROW]
            cols = st.columns(len(row_blocks))
            for col, block in zip(cols, row_blocks):
                with col:
                    bid = block.get("id")
                    bdata = blocks_data.get(bid, {})
                    arts = bdata.get("articles", [])
                    if impact_filter != "Tất cả":
                        arts = [a for a in arts
                                if (a.get("impact") or "Trung lập") == impact_filter]
                    with st.container(border=True, height=460):
                        st.markdown(f"#### {block.get('name', bid)}")
                        upd = bdata.get("updated_at")
                        st.caption(f"{len(arts)} tin"
                                   + (f" · cập nhật {upd}" if upd else ""))
                        if not arts:
                            st.caption("_Chưa có tin._")
                        for a in arts:
                            st.markdown(f"**[{a.get('title','')}]({a.get('link','')})**")
                            st.markdown(
                                f"{impact_label(a.get('impact') or 'Trung lập')}  ·  "
                                f"📰 {a.get('source','')}")
                            if a.get("summary"):
                                st.caption(a["summary"])
                            st.divider()

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

    if st.button("➕ Thêm block mới"):
        st.session_state["draft"].append({
            "id": "blk_" + uuid.uuid4().hex[:6],
            "name": "Block mới", "topics": [], "rss_feeds": {},
            "prompt_instructions": DEFAULT_PROMPT, "update_hours": [7],
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
                         help="Chỉ bài có chứa một trong các từ khóa này mới vào block.")

            st.caption("Nguồn RSS (thêm dòng mới hoặc xóa dòng):")
            rss_rows = [{"Tên nguồn": k, "Link RSS": v}
                        for k, v in blk.get("rss_feeds", {}).items()]
            rss_edits[bid] = st.data_editor(rss_rows, num_rows="dynamic",
                                            use_container_width=True, key=f"rss_{bid}")

            st.text_area("Prompt cho AI", value=blk.get("prompt_instructions", DEFAULT_PROMPT),
                         key=f"prompt_{bid}", height=120)

            st.multiselect("Giờ cập nhật (giờ Việt Nam)", options=list(range(24)),
                           default=blk.get("update_hours", []),
                           format_func=lambda h: f"{h}h", key=f"hours_{bid}",
                           help="Block chỉ cập nhật vào những giờ này, giúp trải đều quota.")

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
            prompt = (st.session_state.get(f"prompt_{bid}", "") or "").strip() or DEFAULT_PROMPT
            hours = sorted(st.session_state.get(f"hours_{bid}", []))
            if name and rss:
                new_blocks.append({"id": bid, "name": name, "topics": topics,
                                   "rss_feeds": rss, "prompt_instructions": prompt,
                                   "update_hours": hours})
        if not new_blocks:
            st.error("Cần ít nhất một block có tên và có nguồn RSS.")
        else:
            ok, msg = github_save(new_blocks)
            if ok:
                st.session_state["live_blocks"] = new_blocks
                st.session_state["draft"] = [dict(b) for b in new_blocks]
                st.success(msg + " Robot sẽ dùng cấu hình mới từ lần chạy kế tiếp.")
            else:
                st.error(msg)
