# -*- coding: utf-8 -*-
"""
DASHBOARD TIN TỨC KINH TẾ VIỆT NAM / THẾ GIỚI  (bản nâng cấp)
-------------------------------------------------------------
- Lấy tin từ RSS, đọc TOÀN VĂN bài báo
- AI (Gemini): tóm tắt 5-10 câu + phân loại chủ đề + đánh giá tác động
- Lưu vào SQLite (tự chống trùng)
- Dashboard trong trình duyệt

Cách chạy:  py -m streamlit run app.py
Lưu ý: lần đầu chạy bản này cần cài thêm thư viện đọc bài báo:
       py -m pip install trafilatura
"""

import time
import json
import sqlite3
import hashlib
from datetime import datetime

import feedparser
import streamlit as st

# =====================================================================
# 1) PHẦN BẠN CÓ THỂ TỰ CHỈNH SỬA DỄ DÀNG
# =====================================================================

RSS_FEEDS = {
    "VnExpress - Kinh doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
    "CafeF":                  "https://cafef.vn/trang-chu.rss",
    "VietnamBiz - Kinh tế":   "https://vietnambiz.vn/kinh-te.rss",
    "Tuổi Trẻ - Kinh doanh":  "https://tuoitre.vn/rss/kinh-doanh.rss",
    "Báo Đầu tư":             "https://baodautu.vn/rss/home.rss",
}

TOPICS = [
    "GDP & Tăng trưởng",
    "Lạm phát",
    "Lãi suất",
    "Tỷ giá",
    "Chứng khoán",
    "Bất động sản",
    "Xuất nhập khẩu",
    "Doanh nghiệp",
    "Chính sách tiền tệ",
    "Khác",
]

# Ba trạng thái đánh giá tác động.
IMPACTS = ["Tích cực", "Tiêu cực", "Trung lập"]

GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

# Vì giờ phải đọc toàn văn (tốn lượt hơn), để mức thấp hơn cho an toàn.
MAX_NEW_PER_RUN = 15

# Chờ (giây) giữa hai lần gọi AI để không vượt giới hạn lượt/phút.
REQUEST_DELAY_SEC = 4

# Cắt bớt nội dung bài quá dài để tiết kiệm lượt (số ký tự tối đa gửi cho AI).
MAX_CONTENT_CHARS = 4000

DB_FILE = "tin_kinh_te.db"

# =====================================================================
# 2) LƯU TRỮ (SQLite)
# =====================================================================

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            source      TEXT,
            link        TEXT,
            published   TEXT,
            raw_text    TEXT,
            summary     TEXT,
            topic       TEXT,
            impact      TEXT,
            created_at  TEXT
        )
    """)
    # Nếu là kho cũ chưa có cột "impact" thì tự thêm vào.
    existing = [row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()]
    if "impact" not in existing:
        conn.execute("ALTER TABLE articles ADD COLUMN impact TEXT")
    conn.commit()
    return conn


def make_id(link: str) -> str:
    return hashlib.md5(link.encode("utf-8")).hexdigest()


def article_exists(conn, art_id: str) -> bool:
    cur = conn.execute("SELECT 1 FROM articles WHERE id = ?", (art_id,))
    return cur.fetchone() is not None


def save_article(conn, art: dict):
    conn.execute(
        """INSERT OR IGNORE INTO articles
           (id, title, source, link, published, raw_text, summary, topic, impact, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (art["id"], art["title"], art["source"], art["link"], art["published"],
         art["raw_text"], art["summary"], art["topic"], art["impact"],
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def load_articles(conn):
    cur = conn.execute(
        "SELECT title, source, link, published, summary, topic, impact "
        "FROM articles ORDER BY created_at DESC"
    )
    cols = ["title", "source", "link", "published", "summary", "topic", "impact"]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def clear_all(conn):
    conn.execute("DELETE FROM articles")
    conn.commit()


# =====================================================================
# 3) ĐỌC TOÀN VĂN BÀI BÁO
# =====================================================================

def get_full_text(link: str, fallback: str = "") -> str:
    """Tải trang bài báo và bóc lấy nội dung chính. Nếu không được thì dùng
    đoạn tóm tắt RSS (fallback)."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(link)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False,
                                       include_tables=False)
            if text and len(text.strip()) > 120:
                return text.strip()[:MAX_CONTENT_CHARS]
    except Exception:
        pass
    return fallback


# =====================================================================
# 4) AI: tóm tắt + phân loại + đánh giá tác động
# =====================================================================

def analyze(api_key: str, model_name: str, title: str, content: str,
            max_retries: int = 3) -> dict:
    """Trả về:
       - thành công: {"ok": True, "summary": ..., "topic": ..., "impact": ...}
       - thất bại:   {"ok": False, "error": "...", "kind": "quota"/"other"}
    Lỗi tạm thời (503/quá tải) -> tự chờ rồi thử lại. Lỗi hết lượt -> dừng ngay.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    prompt = f"""Bạn là chuyên gia phân tích tin kinh tế. Đọc bài sau và trả về JSON.

Tiêu đề: {title}
Nội dung bài: {content}

Yêu cầu:
1. "summary": tóm tắt CỐT LÕI của bài trong 5 đến 10 câu ngắn gọn bằng tiếng Việt.
   Tập trung nêu rõ các con số, số liệu cụ thể nếu bài có.
2. "topic": chọn ĐÚNG MỘT chủ đề phù hợp nhất trong: {TOPICS}
3. "impact": đánh giá tác động của tin này tới nền kinh tế / thị trường,
   chọn ĐÚNG MỘT trong: {IMPACTS}

Chỉ trả về JSON đúng định dạng:
{{"summary": "...", "topic": "...", "impact": "..."}}"""

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            data = json.loads(resp.text)
            topic = data.get("topic", "Khác")
            if topic not in TOPICS:
                topic = "Khác"
            impact = data.get("impact", "Trung lập")
            if impact not in IMPACTS:
                impact = "Trung lập"
            return {"ok": True, "summary": data.get("summary", "").strip(),
                    "topic": topic, "impact": impact}
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if "resource_exhausted" in low or "429" in msg:
                return {"ok": False, "error": msg, "kind": "quota"}
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return {"ok": False, "error": msg, "kind": "other"}


# =====================================================================
# 5) THU THẬP: đọc RSS, lọc tin mới, đọc toàn văn, xử lý AI
# =====================================================================

def fetch_new_articles(conn, api_key: str, model_name: str, progress_callback=None):
    """Trả về (số_tin_đã_thêm, lỗi_nếu_có). Chỉ lưu tin xử lý thành công."""
    new_items = []
    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for entry in feed.entries:
            link = entry.get("link", "")
            if not link:
                continue
            art_id = make_id(link)
            if article_exists(conn, art_id):
                continue
            new_items.append({
                "id": art_id,
                "title": entry.get("title", "(Không có tiêu đề)"),
                "source": source_name,
                "link": link,
                "published": entry.get("published", ""),
                "rss_summary": entry.get("summary", "") or entry.get("description", ""),
            })

    new_items = new_items[:MAX_NEW_PER_RUN]

    count = 0
    total = len(new_items)
    for i, art in enumerate(new_items, start=1):
        if progress_callback:
            progress_callback(i, total, "Đang đọc bài: " + art["title"])
        full_text = get_full_text(art["link"], fallback=art["rss_summary"])
        result = analyze(api_key, model_name, art["title"], full_text)
        if not result["ok"]:
            return count, result
        art["raw_text"] = full_text
        art["summary"] = result["summary"]
        art["topic"] = result["topic"]
        art["impact"] = result["impact"]
        save_article(conn, art)
        count += 1
        if progress_callback:
            progress_callback(i, total, "Đã xử lý: " + art["title"])
        time.sleep(REQUEST_DELAY_SEC)
    return count, None


# =====================================================================
# 6) GIAO DIỆN DASHBOARD (Streamlit)
# =====================================================================

def impact_label(impact: str) -> str:
    """Trả về nhãn có màu cho Streamlit."""
    if impact == "Tích cực":
        return ":green[● Tích cực]"
    if impact == "Tiêu cực":
        return ":red[● Tiêu cực]"
    return ":gray[● Trung lập]"


st.set_page_config(page_title="Dashboard Kinh tế", page_icon="📊", layout="wide")

st.title("📊 Dashboard tin tức kinh tế")
st.caption("Tự lấy tin → AI tóm tắt, phân loại chủ đề & đánh giá tác động")

conn = get_db()

with st.sidebar:
    st.header("⚙️ Cấu hình")
    # Nếu đã cất key trong Secrets (khi chạy trên Streamlit Cloud) thì tự lấy,
    # khỏi cần dán tay. Nếu không có thì hiện ô nhập như khi chạy ở máy nhà.
    try:
        api_key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        api_key = ""
    if api_key:
        st.success("Đã dùng API Key bí mật có sẵn.")
    else:
        api_key = st.text_input("Gemini API Key", type="password",
                                help="Lấy miễn phí tại aistudio.google.com → Get API key")
    model_name = st.selectbox(
        "Model AI", GEMINI_MODELS, index=0,
        help="Nếu báo hết lượt, chọn model khác rồi bấm Cập nhật lại.")

    st.markdown("---")
    if st.button("🔄 Cập nhật tin mới", use_container_width=True, type="primary"):
        if not api_key:
            st.error("Hãy dán Gemini API Key trước khi cập nhật.")
        else:
            bar = st.progress(0.0, text="Đang đọc các nguồn tin...")
            def cb(i, total, msg):
                pct = i / total if total else 1.0
                bar.progress(pct, text=f"{i}/{total} · {msg[:50]}...")
            added, err = fetch_new_articles(conn, api_key, model_name, progress_callback=cb)
            bar.empty()
            if err:
                if err.get("kind") == "quota":
                    st.error(f"Model **{model_name}** đã hết lượt miễn phí. "
                             "Chọn MODEL KHÁC ở ô phía trên rồi bấm Cập nhật lại.")
                else:
                    st.error(f"Lỗi: {err.get('error')}")
                if added:
                    st.info(f"Đã kịp thêm {added} tin trước khi dừng.")
            elif added:
                st.success(f"Đã thêm {added} tin mới!")
            else:
                st.info("Không có tin mới (hoặc đã cập nhật hết).")

    st.markdown("---")
    if st.button("🗑️ Xóa toàn bộ dữ liệu đã lưu", use_container_width=True):
        clear_all(conn)
        st.success("Đã xóa sạch. Bấm Cập nhật để lấy lại tin.")
        st.rerun()

articles = load_articles(conn)

if not articles:
    st.info("Chưa có tin nào. Dán API Key ở thanh bên trái rồi bấm **Cập nhật tin mới**.")
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
        shown = [a for a in shown if a["topic"] == topic_filter]
    if impact_filter != "Tất cả":
        shown = [a for a in shown if (a["impact"] or "Trung lập") == impact_filter]
    if keyword:
        kw = keyword.lower()
        shown = [a for a in shown if kw in a["title"].lower()]

    st.write(f"**{len(shown)}** tin")

    for a in shown:
        with st.container(border=True):
            st.markdown(f"### [{a['title']}]({a['link']})")
            badge = impact_label(a["impact"] or "Trung lập")
            meta = f"{badge}  ·  🏷️ **{a['topic']}**  ·  📰 {a['source']}"
            if a["published"]:
                meta += f"  ·  🕒 {a['published']}"
            st.markdown(meta)
            if a["summary"]:
                st.write(a["summary"])
