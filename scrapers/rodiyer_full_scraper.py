# -*- coding: utf-8 -*-
"""
rodiyer_full_scraper.py
全站爬取 www.rodiyer.idv.tw 所有含「解答」的文章，
解析題目與選項，寫入根目錄 questions.db。

Atom feed 分頁：
  https://www.rodiyer.idv.tw/feeds/posts/default?max-results=150&start-index=N

文章格式（HTML table，兩欄）：
  col0 = '問'       → col1 是題目文字
  col0 = '✓' (✓)  → col1 是正確答案選項
  col0 = ''（空白） → col1 是錯誤選項
  浮水印：style="color:White" 行 或 col1='www.rodiyer.com'
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
URLS_PATH = os.path.join(BASE_DIR, "rodiyer_urls.json")
PROGRESS_PATH = os.path.join(BASE_DIR, "rodiyer_progress.json")

FEED_BASE = "https://www.rodiyer.idv.tw/feeds/posts/default"
FEED_PAGE_SIZE = 150

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

WATERMARK_TEXTS = {"www.rodiyer.com", "www.rodiyer.idv.tw"}
CORRECT_MARK = "\u2713"  # ✓


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
    try:
        conn.execute("ALTER TABLE questions ADD COLUMN article_date TEXT")
    except sqlite3.OperationalError:
        pass  # 欄位已存在
    conn.commit()


def insert_questions(conn: sqlite3.Connection, rows: list) -> tuple:
    inserted = skipped = 0
    for row in rows:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO questions
                   (category, question, option_a, option_b, option_c, option_d,
                    answer, source_url, article_date)
                   VALUES (:category, :question, :option_a, :option_b, :option_c,
                           :option_d, :answer, :source_url, :article_date)""",
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
    透過 Atom feed 分頁取得 rodiyer 所有文章。
    篩選標題含「解答」的文章。
    優先讀取本地 rodiyer_urls.json。
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

        # 取總筆數
        total_elem = root.find("{http://a9.com/-/spec/opensearchrss/1.0/}totalResults")
        if total_elem is None:
            total_elem = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
        total = int(total_elem.text) if total_elem is not None else 0

        entries = root.findall("{http://www.w3.org/2005/Atom}entry")
        if not entries:
            break

        page_added = 0
        for entry in entries:
            title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
            title = title_elem.text.strip() if title_elem is not None else ""

            # 只收含「解答」的文章
            if "解答" not in title:
                continue

            url = ""
            for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
                if link.get("rel") == "alternate":
                    url = link.get("href", "")
                    break

            # 取發文日期
            published_elem = entry.find("{http://www.w3.org/2005/Atom}published")
            article_date = None
            if published_elem is not None and published_elem.text:
                article_date = published_elem.text.strip()[:10]  # YYYY-MM-DD

            if url:
                results.append(
                    {"title": title, "url": url, "article_date": article_date}
                )
                page_added += 1

        print(
            f"  本頁 {len(entries)} 篇，含解答 {page_added} 篇，累計 {len(results)} 篇"
        )

        if total > 0 and start_index + FEED_PAGE_SIZE > total:
            break
        if not entries:
            break

        start_index += FEED_PAGE_SIZE
        time.sleep(random.uniform(1.0, 2.0))

    with open(URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"已存 {len(results)} 個含解答 URL 至 {URLS_PATH}")
    return results


# ─────────────────────────── Category ───────────────────────────


def extract_category(title: str) -> str:
    """從標題提取課程名稱，去掉【解答】前綴與「第X回」後綴。"""
    title = re.sub(
        r"^[【〔\[（(]?(解答|答案|考題|題庫)[】〕\]）)]*\s*", "", title
    ).strip()
    title = re.sub(
        r"\s*(第[一二三四五六七八九十百\d]+[回題次期套組份]+)\s*$", "", title
    ).strip()
    return title or "未分類"


# ─────────────────────────── Parser ───────────────────────────


def parse_rodiyer_page(
    html: str, source_url: str, article_date: str | None = None
) -> list:
    """
    解析 rodiyer 課程頁面（兩欄 table 格式）。
    col0='問' → 題目；col0='✓' → 正確答案；col0='' → 錯誤選項
    """
    soup = BeautifulSoup(html, "html.parser")

    # 取 category 從頁面標題
    title_tag = (
        soup.find("h3", class_="post-title") or soup.find("h1") or soup.find("title")
    )
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    category = extract_category(title_text)

    # 定位文章容器
    article = (
        soup.find("div", id="PostBody")
        or soup.find("div", class_="post-body")
        or soup.find("div", class_="entry-content")
        or soup.find("div", class_="post-body entry-content")
        or soup
    )

    results = []
    tables = article.find_all("table")
    if not tables:
        print(f"  [警告] 找不到 table，URL：{source_url}")
        return results

    for table in tables:
        rows = table.find_all("tr")

        # 解析成 (col0, col1) list，過濾浮水印
        parsed = []
        for tr in rows:
            style = tr.get("style", "")
            if "color:White" in style or "color: White" in style:
                continue
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            col0 = cells[0].get_text(strip=True)
            col1 = cells[1].get_text(strip=True)
            if col1 in WATERMARK_TEXTS:
                continue
            parsed.append((col0, col1))

        # 組題目
        i = 0
        while i < len(parsed):
            col0, col1 = parsed[i]
            if col0 == "問":
                q_text = col1.strip()
                q_text = re.sub(r"^[\d０-９]+[.．、。）)\s]+", "", q_text).strip()
                if not q_text:
                    i += 1
                    continue

                options = []  # list of (is_correct: bool, text: str)
                j = i + 1
                while j < len(parsed) and parsed[j][0] != "問":
                    opt_col0, opt_text = parsed[j]
                    opt_text = opt_text.strip()
                    if not opt_text or opt_text in WATERMARK_TEXTS:
                        j += 1
                        continue
                    is_correct = opt_col0 == CORRECT_MARK
                    options.append((is_correct, opt_text))
                    j += 1

                correct_answers = [t for is_c, t in options if is_c]
                answer = "、".join(correct_answers) if correct_answers else None

                if q_text and answer:
                    all_opts = [t for _, t in options]
                    record = {
                        "category": category,
                        "question": q_text,
                        "option_a": all_opts[0] if len(all_opts) > 0 else None,
                        "option_b": all_opts[1] if len(all_opts) > 1 else None,
                        "option_c": all_opts[2] if len(all_opts) > 2 else None,
                        "option_d": all_opts[3] if len(all_opts) > 3 else None,
                        "answer": answer,
                        "source_url": source_url,
                        "article_date": article_date,
                    }
                    results.append(record)

                i = j
            else:
                i += 1

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
    print(f"DB 路徑：{DB_PATH}")
    print(f"總 URL：{len(url_list)}，已完成：{len(done_urls)}，待爬：{len(pending)}")

    before = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    print(f"寫入前題目數：{before}")

    if not pending:
        print("全部已爬取完畢！")
        conn.close()
        return

    inserted_total = 0
    skipped_total = 0
    save_interval = 50

    for idx, item in enumerate(pending):
        url = item["url"]
        title = item.get("title", url)
        article_date = item.get("article_date")

        print(f"[{idx + 1}/{len(pending)}] {title[:50]}")

        html = fetch(url, session)
        if not html:
            print("  跳過（無法取得頁面）")
            done_urls.add(url)
            continue

        qa_list = parse_rodiyer_page(html, url, article_date)
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
    after = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    conn.close()

    print(f"\n===== 完成 =====")
    print(f"新增：{inserted_total} 題，略過：{skipped_total} 題")
    print(f"寫入後題目總數：{after}（增加 {after - before}）")


if __name__ == "__main__":
    main()
