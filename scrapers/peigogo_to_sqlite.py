# -*- coding: utf-8 -*-
"""
peigogo_to_sqlite.py
爬取 www.peigogo.com E等公務員系列所有文章，
解析題目與選項，寫入 SQLite 資料庫（questions.db）。

HTML 結構（Blogger 平台）：
  - 容器：div.post-body-inner
  - 題目：div 內文字以「問：」開頭
  - 正確答案：文字以 v 或 V（小寫/大寫）開頭，後接 <span style="white-space: pre;">TAB</span>
  - 錯誤選項：文字以 &nbsp; 群組（無 v 前綴）
  - category：從標題去掉「[解答]@...」後綴

Atom feed 分頁：
  https://www.peigogo.com/feeds/posts/default/-/E等公務員?max-results=150&start-index=1
"""

import json
import re
import time
import os
import sys
import random
import sqlite3
import requests
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup

# Fix Windows console encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() in (
    "cp950",
    "cp932",
    "gbk",
    "gb2312",
):
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)  # autoLearning-- 根目錄
DB_PATH = os.path.join(ROOT_DIR, "questions.db")
PROGRESS_PATH = os.path.join(BASE_DIR, "peigogo_progress.json")
URLS_PATH = os.path.join(BASE_DIR, "peigogo_urls.json")

FEED_BASE = "https://www.peigogo.com/feeds/posts/default/-/E%E7%AD%89%E5%85%AC%E5%8B%99%E5%93%A1"
FEED_PAGE_SIZE = 150

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

NS = {"atom": "http://www.w3.org/2005/Atom"}


# ─────────────────────────── DB ───────────────────────────


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            question TEXT,
            option_a TEXT,
            option_b TEXT,
            option_c TEXT,
            option_d TEXT,
            answer TEXT,
            source_url TEXT,
            article_date TEXT,
            UNIQUE(question, source_url)
        )
    """)
    # 若舊版 DB 沒有 article_date 欄位，自動新增
    try:
        conn.execute("ALTER TABLE questions ADD COLUMN article_date TEXT")
    except sqlite3.OperationalError:
        pass  # 欄位已存在
    conn.commit()


def insert_questions(conn: sqlite3.Connection, rows: list) -> tuple:
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO questions
                    (category, question, option_a, option_b, option_c, option_d, answer, source_url, article_date)
                VALUES
                    (:category, :question, :option_a, :option_b, :option_c, :option_d, :answer, :source_url, :article_date)
                """,
                row,
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
            else:
                # 若已存在但 article_date 為 NULL，補入日期
                if row.get("article_date"):
                    conn.execute(
                        """UPDATE questions SET article_date = :article_date
                           WHERE question = :question AND source_url = :source_url
                           AND article_date IS NULL""",
                        row,
                    )
                skipped += 1
        except Exception as e:
            print(f"  [DB Error] {e}")
            skipped += 1
    conn.commit()
    return inserted, skipped


# ─────────────────────────── Progress ───────────────────────────


def load_progress() -> set:
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_progress(done_urls: set):
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(list(done_urls), f, ensure_ascii=False)


# ─────────────────────────── HTTP ───────────────────────────


def fetch(url: str, session: requests.Session, retries=4) -> str | None:
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                return resp.text
            elif resp.status_code == 429:
                wait = 90 + random.randint(0, 30)
                print(f"  [429] Rate limited, sleep {wait}s...")
                time.sleep(wait)
            else:
                print(f"  [HTTP {resp.status_code}] {url}")
                return None
        except Exception as e:
            print(f"  [Error] {e} (attempt {attempt + 1}/{retries})")
            time.sleep(10 * (attempt + 1))
    return None


# ─────────────────────────── Feed (URL 列表) ───────────────────────────


def fetch_all_urls(session: requests.Session) -> list:
    """
    透過 Atom feed 分頁取得所有 E等公務員文章 URL 與標題。
    優先讀取本地 peigogo_urls.json。
    """
    if os.path.exists(URLS_PATH):
        print(f"從本地 {URLS_PATH} 載入 URL 列表...")
        with open(URLS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  載入 {len(data)} 個 URL")
        return data

    print("從 Atom feed 抓文章列表...")
    results = []
    start_index = 1

    while True:
        feed_url = f"{FEED_BASE}?max-results={FEED_PAGE_SIZE}&start-index={start_index}"
        print(f"  Feed page start-index={start_index}...")
        xml_text = fetch(feed_url, session)
        if not xml_text:
            print("  無法取得 feed，停止")
            break

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            print(f"  XML 解析失敗：{e}")
            break

        total_elem = root.find("{http://a9.com/-/spec/opensearchrss/1.0/}totalResults")
        if total_elem is None:
            total_elem = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
        total = int(total_elem.text) if total_elem is not None else 0

        entries = root.findall("{http://www.w3.org/2005/Atom}entry")
        if not entries:
            break

        for entry in entries:
            title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
            title = title_elem.text.strip() if title_elem is not None else ""

            url = ""
            for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
                if link.get("rel") == "alternate":
                    url = link.get("href", "")
                    break

            # 取發文日期（格式：2024-03-15T00:00:00+08:00，只取前 10 字元）
            published_elem = entry.find("{http://www.w3.org/2005/Atom}published")
            article_date = None
            if published_elem is not None and published_elem.text:
                article_date = published_elem.text.strip()[:10]  # YYYY-MM-DD

            if url:
                results.append(
                    {"title": title, "url": url, "article_date": article_date}
                )

        print(f"  取得 {len(entries)} 篇，累計 {len(results)} / {total}")

        # 只以 total 為終止條件，不以單頁筆數（Blogger 偶爾回傳不足一頁但還有後頁）
        if total > 0 and len(results) >= total:
            break
        if not entries:
            break

        start_index += FEED_PAGE_SIZE
        time.sleep(random.uniform(1.0, 2.0))

    with open(URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"已存 {len(results)} 個 URL 至 {URLS_PATH}")
    return results


# ─────────────────────────── Category ───────────────────────────


def extract_category(title: str) -> str:
    """
    從標題提取課程名稱。
    格式：「課程名稱」[解答]@e等公務園+e學中心
    去掉「[解答]@...」之後的部分；去掉「」書名號。
    """
    # 去掉 [解答]@... 以後的全部
    title = re.sub(r"\s*[\[【〔]解答[\]】〕].*$", "", title).strip()
    # 去掉書名號
    title = title.strip("「」『』【】〔〕《》〈〉")
    return title or "未分類"


# ─────────────────────────── Parser ───────────────────────────


def _div_text(div) -> str:
    """
    取得 div 的純文字。
    使用 get_text(separator='') 保留內部空白。
    """
    return div.get_text(separator="")


def parse_post(
    html: str, source_url: str, category: str, article_date: str | None = None
) -> list:
    """
    解析單篇 PeiGoGo 文章，回傳可寫入 DB 的 dict list。

    正確答案識別：div 文字以 v 或 V（不分大小寫）開頭，
                  後接 \\t（tab，由 <span style="white-space: pre;">	</span> 產生）
    錯誤選項識別：div 文字以多個 &nbsp; / 空白開頭（無 v 前綴）
    題目識別：     div 文字以「問：」開頭
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("div", class_="post-body-inner")
    if not body:
        return []

    results = []
    current_q = None
    options = []  # list of (is_correct: bool, text: str)

    def flush_question():
        nonlocal current_q, options
        if current_q and options:
            correct = [t for ok, t in options if ok]
            if not correct:
                current_q = None
                options = []
                return
            answer = "、".join(correct)
            all_opts = [t for _, t in options]
            record = {
                "category": category,
                "question": current_q,
                "option_a": all_opts[0] if len(all_opts) > 0 else None,
                "option_b": all_opts[1] if len(all_opts) > 1 else None,
                "option_c": all_opts[2] if len(all_opts) > 2 else None,
                "option_d": all_opts[3] if len(all_opts) > 3 else None,
                "answer": answer,
                "source_url": source_url,
                "article_date": article_date,
            }
            results.append(record)
        current_q = None
        options = []

    # 只取第一層子 div（不遞迴），避免父 div 包含完整題目重複計算
    # 每個第一層子 div 代表一行（題目或選項）
    top_divs = body.find_all("div", recursive=False)
    # 如果第一層無 div，降級為全部 div（某些文章結構不同）
    if not top_divs:
        top_divs = body.find_all("div")

    # 展開巢狀結構：取每個 top_div 的直接子 div（實際行）
    # 若 top_div 的文字含「問：」且同時含選項（合包 div），需展開其子 div
    line_divs = []
    for tdiv in top_divs:
        children = tdiv.find_all("div", recursive=False)
        if children:
            line_divs.extend(children)
        else:
            line_divs.append(tdiv)

    SKIP_PREFIXES = ("相信", "善用", "資料", "【", "❤", "以下", "參考", "歡迎", "版權")

    for div in line_divs:
        raw = _div_text(div)
        text = raw.strip()
        if not text:
            continue

        # 題目
        if text.startswith("問："):
            flush_question()
            q_text = text[2:].strip()
            q_text = re.sub(r"^[\d０-９]+[.．、。）)\s]+", "", q_text).strip()
            if q_text:
                current_q = q_text
            continue

        if current_q is None:
            continue

        # 正確答案：v 或 V 開頭，後接空格（tab span 被 get_text 轉成空格）
        # 格式：「v 選項文字」 或 「V 選項文字」
        m_correct = re.match(r"^[vV][\s\u00a0]+(.+)$", text)
        if m_correct:
            opt_text = m_correct.group(1).strip()
            if opt_text:
                options.append((True, opt_text))
            continue

        # 錯誤選項：去掉 NBSP/空白前綴後即為選項文字
        # 排除說明性文字行
        if any(text.startswith(p) for p in SKIP_PREFIXES):
            continue
        # 去掉前綴 NBSP/空白
        cleaned = re.sub(r"^[\s\u00a0]+", "", text)
        if cleaned and len(cleaned) >= 2 and not cleaned.startswith("問："):
            options.append((False, cleaned))

    # 最後一題
    flush_question()
    return results


# ─────────────────────────── Main ───────────────────────────


def main():
    session = requests.Session()

    # 1. 取得 URL 列表
    url_list = fetch_all_urls(session)
    if not url_list:
        print("URL 列表為空，無法繼續")
        sys.exit(1)

    # 2. 開啟資料庫
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # 3. 載入進度
    done_urls = load_progress()
    pending = [item for item in url_list if item["url"] not in done_urls]
    print(f"總 URL：{len(url_list)}，已完成：{len(done_urls)}，待爬：{len(pending)}")

    if not pending:
        print("全部已爬取完畢！")
        count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        print(f"questions.db 現有 {count} 題")
        conn.close()
        return

    inserted_total = 0
    skipped_total = 0
    save_interval = 50  # 每 50 篇存一次進度

    for idx, item in enumerate(pending):
        url = item["url"]
        title = item.get("title", url)
        category = extract_category(title)
        article_date = item.get("article_date")

        print(f"[{idx + 1}/{len(pending)}] {title[:40]}")

        html = fetch(url, session)
        if not html:
            print("  跳過（無法取得頁面）")
            done_urls.add(url)
            continue

        qa_list = parse_post(html, url, category, article_date)
        if not qa_list:
            print("  無解析結果（頁面格式不符或無題目）")
        else:
            ins, skp = insert_questions(conn, qa_list)
            inserted_total += ins
            skipped_total += skp
            print(f"  解析 {len(qa_list)} 題，新增 {ins}，略過(重複) {skp}")

        done_urls.add(url)

        if (idx + 1) % save_interval == 0:
            save_progress(done_urls)
            count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            print(f"  [定期存檔] questions.db 共 {count} 題")

        time.sleep(random.uniform(0.8, 2.0))

    # 最終存檔
    save_progress(done_urls)
    count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    conn.close()

    print(f"\n完成！共新增 {inserted_total} 題，略過 {skipped_total} 題")
    print(f"questions.db 現共 {count} 題")


if __name__ == "__main__":
    main()
