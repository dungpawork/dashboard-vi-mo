# -*- coding: utf-8 -*-
"""
DASHBOARD TIN TỨC KINH TẾ — bản hiển thị (viewer)
-------------------------------------------------
Chỉ ĐỌC và HIỂN THỊ file data.json (do robot fetch.py cập nhật theo lịch).
Không lấy tin, không cần API key ở đây nữa.

Chạy ở máy:  py -m streamlit run app.py
"""

import os
import json
import streamlit as st

TOPICS = [
    "GDP & Tăng trưởng", "Lạm phát", "Lãi suất", "Tỷ giá", "Chứng khoán",
    "Bất động sản", "Xuất nhập khẩu", "Doanh nghiệp", "Chính sách tiền tệ", "Khác",
]
IMPACTS = ["Tích cực", "Tiêu cực", "Trung lập"]

DATA_FILE = "data.json"


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("articles", []), d.get("updated_at", "")
        except Exception:
            return [], ""
    return [], ""


def impact_label(impact):
    if impact == "Tích cực":
        return ":green[● Tích cực]"
    if impact == "Tiêu cực":
        return ":red[● Tiêu cực]"
    return ":gray[● Trung lập]"


st.set_page_config(page_title="Dashboard Kinh tế", page_icon="📊", layout="wide")

st.title("📊 Dashboard tin tức kinh tế")

articles, updated_at = load_data()

with st.sidebar:
    st.header("ℹ️ Thông tin")
    if updated_at:
        st.caption(f"Cập nhật lần cuối: **{updated_at}** (giờ VN)")
    st.caption(f"Đang lưu **{len(articles)}** tin")
    st.markdown("---")
    if st.button("🔄 Tải lại trang", use_container_width=True):
        st.rerun()
    st.caption("Tin được robot tự cập nhật lúc 7h, 12h, 15h, 20h hằng ngày.")

if not articles:
    st.info("Chưa có dữ liệu. Hãy chạy lần cập nhật đầu tiên bằng nút "
            "**Run workflow** trong tab Actions trên GitHub, rồi tải lại trang.")
else:
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        topic_filter = st.selectbox("Lọc theo chủ đề", ["Tất cả"] + TOPICS)
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
            badge = impact_label(a.get("impact") or "Trung lập")
            meta = f"{badge}  ·  🏷️ **{a.get('topic','')}**  ·  📰 {a.get('source','')}"
            if a.get("published"):
                meta += f"  ·  🕒 {a['published']}"
            st.markdown(meta)
            if a.get("summary"):
                st.write(a["summary"])
