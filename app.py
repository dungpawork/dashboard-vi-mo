# -*- coding: utf-8 -*-
"""
DASHBOARD TIN TỨC KINH TẾ — viewer + trang Cấu hình (admin)
-----------------------------------------------------------
- Hiển thị data.json (do robot fetch.py cập nhật).
- Trang "Cấu hình" có MẬT KHẨU: sửa nguồn RSS / TOPICS / prompt,
  rồi GHI config.json lên GitHub để robot dùng cho lần chạy sau.

Cần các Secrets (ở Streamlit Cloud) cho phần cấu hình:
  ADMIN_PASSWORD = "..."
  GH_TOKEN       = "ghp_..."          (GitHub token có quyền repo)
  GH_REPO        = "tendangnhap/dashboard-kinh-te"
"""

import os
import json
import base64

import requests
import streamlit as st

CONFIG_FILE = "config.json"
DATA_FILE = "data.json"
IMPACTS = ["Tích cực", "Tiêu cực", "Trung lập"]

DEFAULT_CONFIG = {
    "rss_feeds": {
        "VnExpress - Kinh doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
        "CafeF": "https://cafef.vn/trang-chu.rss",
    },
    "topics": ["GDP & Tăng trưởng", "Lạm phát", "Lãi suất", "Tỷ giá",
               "Chứng khoán", "Bất động sản", "Xuất nhập khẩu",
               "Doanh nghiệp", "Chính sách tiền tệ", "Khác"],
    "prompt_instructions": ("Bạn là chuyên gia phân tích tin kinh tế. Hãy tóm tắt "
                            "CỐT LÕI của bài trong 5 đến 10 câu ngắn gọn bằng tiếng "
                            "Việt, tập trung nêu rõ các con số nếu bài có."),
}


# ---------------- Đọc dữ liệu & cấu hình ----------------

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("articles", []), d.get("updated_at", "")
        except Exception:
            return [], ""
    return [], ""


def load_config():
    # Trong phiên làm việc, ưu tiên cấu hình vừa lưu (nếu có).
    if "live_config" in st.session_state:
        return st.session_state["live_config"]
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                c = json.load(f)
            return {
                "rss_feeds": c.get("rss_feeds", DEFAULT_CONFIG["rss_feeds"]),
                "topics": c.get("topics", DEFAULT_CONFIG["topics"]),
                "prompt_instructions": c.get("prompt_instructions",
                                             DEFAULT_CONFIG["prompt_instructions"]),
            }
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def github_save_config(config_dict):
    """Ghi config.json lên GitHub. Trả về (thành công, thông báo)."""
    token = get_secret("GH_TOKEN")
    repo = get_secret("GH_REPO")
    if not token or not repo:
        return False, "Chưa khai báo GH_TOKEN / GH_REPO trong Secrets."

    api = f"https://api.github.com/repos/{repo}/contents/{CONFIG_FILE}"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    content_str = json.dumps(config_dict, ensure_ascii=False, indent=2)

    sha = None
    try:
        r = requests.get(api, headers=headers, timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        return False, f"Không kết nối được GitHub: {e}"

    body = {
        "message": "Cap nhat cau hinh tu dashboard",
        "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
    }
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

cfg = load_config()
articles, updated_at = load_data()

page = st.sidebar.radio("Trang", ["📊 Tin tức", "⚙️ Cấu hình"])

if page == "📊 Tin tức":
    st.title("📊 Dashboard tin tức kinh tế")
    with st.sidebar:
        if updated_at:
            st.caption(f"Cập nhật lần cuối: **{updated_at}** (giờ VN)")
        st.caption(f"Đang lưu **{len(articles)}** tin")
        if st.button("🔄 Tải lại trang", use_container_width=True):
            st.rerun()

    if not articles:
        st.info("Chưa có dữ liệu. Chạy **Run workflow** trong tab Actions trên "
                "GitHub để lấy tin lần đầu, rồi tải lại trang.")
    else:
        topics = cfg["topics"]
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            topic_filter = st.selectbox("Lọc theo chủ đề", ["Tất cả"] + topics)
        with c2:
            impact_filter = st.selectbox("Lọc theo tác động", ["Tất cả"] + IMPACTS)
        with c3:
            keyword = st.text_input("Tìm theo từ khóa (trong tiêu đề)")

        shown = articles
        if topic_filter != "Tất cả":
            shown = [a for a in shown if a.get("topic") == topic_filter]
        if impact_filter != "Tất cả":
            shown = [a for a in shown if (a.get("impact") or "Trung lập") == impact_filter]
        if keyword:
            kw = keyword.lower()
            shown = [a for a in shown if kw in a.get("title", "").lower()]

        st.write(f"**{len(shown)}** tin")
        for a in shown:
            with st.container(border=True):
                st.markdown(f"### [{a.get('title','')}]({a.get('link','')})")
                meta = (f"{impact_label(a.get('impact') or 'Trung lập')}  ·  "
                        f"🏷️ **{a.get('topic','')}**  ·  📰 {a.get('source','')}")
                if a.get("published"):
                    meta += f"  ·  🕒 {a['published']}"
                st.markdown(meta)
                if a.get("summary"):
                    st.write(a["summary"])

else:  # ===================== TRANG CẤU HÌNH =====================
    st.title("⚙️ Cấu hình")

    admin_pw = get_secret("ADMIN_PASSWORD")
    if not admin_pw:
        st.warning("Chưa đặt ADMIN_PASSWORD trong Secrets nên trang này đang khóa. "
                   "Xem hướng dẫn để bật tính năng chỉnh sửa.")
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

    st.success("Đã mở khóa. Sửa xong nhớ bấm **Lưu cấu hình**.")

    # --- Nguồn RSS ---
    st.subheader("Nguồn RSS")
    st.caption("Thêm dòng mới hoặc xóa dòng tùy ý. Mỗi nguồn gồm tên và link RSS.")
    rss_rows = [{"Tên nguồn": k, "Link RSS": v} for k, v in cfg["rss_feeds"].items()]
    edited_rss = st.data_editor(rss_rows, num_rows="dynamic", use_container_width=True,
                                key="rss_editor")

    # --- TOPICS ---
    st.subheader("Danh sách chủ đề (TOPICS)")
    st.caption("Mỗi chủ đề một dòng. Nên giữ lại 'Khác' để chứa tin chưa phân loại.")
    topics_text = st.text_area("Chủ đề", value="\n".join(cfg["topics"]), height=220)

    # --- Prompt ---
    st.subheader("Hướng dẫn cho AI (prompt)")
    st.caption("Phần này điều khiển cách AI tóm tắt. Hệ thống sẽ tự thêm yêu cầu "
               "phân loại chủ đề, đánh giá tác động và trả về JSON, nên bạn chỉ cần "
               "tập trung mô tả cách tóm tắt mong muốn.")
    prompt_text = st.text_area("Prompt", value=cfg["prompt_instructions"], height=160)

    if st.button("💾 Lưu cấu hình", type="primary"):
        # Gom RSS từ bảng (bỏ dòng trống)
        new_rss = {}
        for row in edited_rss:
            name = (row.get("Tên nguồn") or "").strip()
            link = (row.get("Link RSS") or "").strip()
            if name and link:
                new_rss[name] = link
        # Gom topics
        new_topics = [t.strip() for t in topics_text.splitlines() if t.strip()]
        if "Khác" not in new_topics:
            new_topics.append("Khác")

        new_config = {
            "rss_feeds": new_rss,
            "topics": new_topics,
            "prompt_instructions": prompt_text.strip() or DEFAULT_CONFIG["prompt_instructions"],
        }

        if not new_rss:
            st.error("Cần ít nhất một nguồn RSS.")
        else:
            ok, msg = github_save_config(new_config)
            if ok:
                st.session_state["live_config"] = new_config
                st.success(msg + " Robot sẽ dùng cấu hình mới từ lần chạy kế tiếp.")
            else:
                st.error(msg)
