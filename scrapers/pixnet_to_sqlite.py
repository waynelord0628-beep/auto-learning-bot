# -*- coding: utf-8 -*-
"""
pixnet_to_sqlite.py
爬取 roddayeye.pixnet.net 所有課程解答頁面，
解析題目與選項，寫入 SQLite 資料庫（questions.db）。

Schema:
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        question TEXT,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        answer TEXT,
        source_url TEXT
    )

解析邏輯（同 pixnet_scraper.py parse_page）：
  - col0 = 'Q'  → col2 是題目文字
  - col0 = 'v'  → col2 是正確答案選項
  - col0 = 其他 → col2 是錯誤選項
  - 過濾浮水印 r.o.d.d.a.y.e.y.e.
  - 多選題答案用「、」合併
  - option_a/b/c/d = 第 1~4 個選項（不含答案判斷，純粹依序排列）
  - answer = 正確答案的實際文字
"""

import json
import re
import time
import os
import sys
import random
import sqlite3
import requests
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
URLS_PATH = os.path.join(BASE_DIR, "pixnet_urls.json")
PROGRESS_PATH = os.path.join(BASE_DIR, "sqlite_progress.json")

INDEX_URL = "https://roddayeye.pixnet.net/blog/posts/15325785090"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


# ─────────────────────────── DB ───────────────────────────


def init_db(conn: sqlite3.Connection):
    """建立 questions 資料表（若不存在），並確保 article_date 欄位存在"""
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


def insert_questions(conn: sqlite3.Connection, rows: list) -> tuple[int, int]:
    """
    批次寫入，回傳 (inserted, skipped)
    rows: list of dict with keys: category, question, option_a..d, answer, source_url, article_date
    """
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


def fetch_page(url: str, session: requests.Session, retries=3) -> str | None:
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                return resp.text
            elif resp.status_code == 429:
                print(f"  [429] Rate limited, sleeping 60s...")
                time.sleep(60)
            else:
                print(f"  [HTTP {resp.status_code}] {url}")
                return None
        except Exception as e:
            print(f"  [Error] {e} (attempt {attempt + 1}/{retries})")
            time.sleep(5)
    return None


# ─────────────────────────── Date extraction ───────────────────────────


def extract_date_from_html(html: str) -> str | None:
    """
    從 pixnet 文章 HTML 取出發文日期，格式 YYYY-MM-DD。
    優先嘗試 JSON-LD / meta datePublished，
    其次嘗試 <span class="month"> / <span class="date"> / <span class="year">。
    """
    import json as _json

    soup = BeautifulSoup(html, "html.parser")

    # 1. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            date_str = data.get("datePublished") or data.get("dateCreated") or ""
            if date_str:
                m = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
                if m:
                    return m.group(1)
        except Exception:
            pass

    # 2. meta datePublished
    meta = soup.find("meta", attrs={"itemprop": "datePublished"})
    if meta and meta.get("content"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", meta["content"])
        if m:
            return m.group(1)

    # 3. <time> element
    time_tag = soup.find("time")
    if time_tag:
        dt = time_tag.get("datetime", "")
        m = re.match(r"(\d{4}-\d{2}-\d{2})", dt)
        if m:
            return m.group(1)

    # 4. <span class="month/date/year"> pattern
    month_span = soup.find("span", class_="month")
    date_span = soup.find("span", class_="date")
    year_span = soup.find("span", class_="year")
    if month_span and date_span and year_span:
        try:
            month = month_span.get_text(strip=True).zfill(2)
            day = date_span.get_text(strip=True).zfill(2)
            year = year_span.get_text(strip=True)
            return f"{year}-{month}-{day}"
        except Exception:
            pass

    return None


# ─────────────────────────── Index page ───────────────────────────


def fetch_index_urls(session: requests.Session) -> list[dict]:
    """
    從總整理頁抓所有文章連結。
    優先讀取本地 pixnet_urls.json；若不存在才從網路抓。
    回傳 [{"title": ..., "url": ...}, ...]
    """
    if os.path.exists(URLS_PATH):
        print(f"從本地 {URLS_PATH} 載入 URL 列表...")
        with open(URLS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  載入 {len(data)} 個 URL")
        return data

    print(f"從網路抓索引頁：{INDEX_URL}")
    results = []
    page = 1
    while True:
        url = f"{INDEX_URL}?page={page}" if page > 1 else INDEX_URL
        html = fetch_page(url, session)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        links = soup.select("a[href*='pixnet.net/blog/post/']")
        if not links:
            break
        found = 0
        seen = set(r["url"] for r in results)
        for a in links:
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if href and href not in seen:
                results.append({"title": text, "url": href})
                seen.add(href)
                found += 1
        print(f"  第 {page} 頁：找到 {found} 個連結（累計 {len(results)}）")
        # 檢查是否有下一頁
        next_btn = soup.select_one("a.next, a[rel='next']")
        if not next_btn:
            break
        page += 1
        time.sleep(random.uniform(1, 2))

    # 存到本地備用
    with open(URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"已存 {len(results)} 個 URL 至 {URLS_PATH}")
    return results


# ─────────────────────────── Parser ───────────────────────────


def parse_page(
    html: str, source_url: str, category: str, article_date: str | None = None
) -> list[dict]:
    """
    解析 pixnet 課程頁面，回傳可直接寫入 DB 的 dict list。
    每題包含：category, question, option_a..d, answer, source_url, article_date
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    article = soup.find("div", class_="article-content-inner")
    if not article:
        article = soup.find("div", class_="article-content")
    if not article:
        return results

    tables = article.find_all("table")
    for table in tables:
        rows = table.find_all("tr")

        parsed_rows = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            col0 = cells[0].get_text(strip=True)
            col2 = cells[2].get_text(strip=True)
            parsed_rows.append((col0, col2))

        i = 0
        while i < len(parsed_rows):
            col0, col2 = parsed_rows[i]
            if col0 == "Q":
                q_text = col2.strip()
                # 去掉題號前綴（如 "1." "１." "（1）"）
                q_text = re.sub(r"^[\d０-９]+[.．、。）)\s]+", "", q_text).strip()
                if not q_text:
                    i += 1
                    continue

                # 收集選項行
                options = []  # list of (col0, col2)
                j = i + 1
                while j < len(parsed_rows) and parsed_rows[j][0] != "Q":
                    opt_col0, opt_col2 = parsed_rows[j]
                    opt_text = opt_col2.strip()
                    # 過濾浮水印與空白行
                    if "r.o.d.d.a.y.e.y.e." in opt_text or not opt_text:
                        j += 1
                        continue
                    options.append((opt_col0, opt_text))
                    j += 1

                # 正確答案（col0='v'）
                correct_answers = [t for c0, t in options if c0 == "v"]
                answer = "、".join(correct_answers) if correct_answers else None

                if q_text and answer:
                    # 依序填入 option_a/b/c/d
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


def extract_category(title: str) -> str:
    """從文章標題提取 category（去掉《解答》《答案》後綴等）"""
    # 去掉常見的後綴
    title = re.sub(
        r"[《〈【\[（(]?(解答|答案|考題|題庫|解析|測驗)[》〉】\]）)]*\s*$", "", title
    ).strip()
    # 去掉 「第X回」「第X題」前綴/後綴
    title = re.sub(
        r"\s*(第[一二三四五六七八九十百\d]+[回題次期套組份]+)\s*$", "", title
    ).strip()
    return title or "未分類"


# ─────────────────────────── Main ───────────────────────────


def main():
    session = requests.Session()

    # 1. 取得 URL 列表
    url_list = fetch_index_urls(session)
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
    save_interval = 100

    for idx, item in enumerate(pending):
        url = item["url"]
        title = item.get("title", url)
        category = extract_category(title)

        print(f"[{idx + 1}/{len(pending)}] {title[:30]} → {category}")

        html = fetch_page(url, session)
        if not html:
            print(f"  跳過（無法取得頁面）")
            done_urls.add(url)
            continue

        article_date = extract_date_from_html(html)
        qa_list = parse_page(html, url, category, article_date)
        if not qa_list:
            print(f"  無解析結果（頁面格式不符或無題目）")
        else:
            ins, skp = insert_questions(conn, qa_list)
            inserted_total += ins
            skipped_total += skp
            print(f"  解析 {len(qa_list)} 題，新增 {ins}，略過(重複) {skp}")

        done_urls.add(url)

        # 定期存進度
        if (idx + 1) % save_interval == 0:
            save_progress(done_urls)
            count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            print(f"  [定期存檔] questions.db 共 {count} 題")

        time.sleep(random.uniform(0.5, 1.5))

    # 最終存檔
    save_progress(done_urls)
    count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    conn.close()

    print(f"\n完成！共新增 {inserted_total} 題，略過 {skipped_total} 題")
    print(f"questions.db 現共 {count} 題")


if __name__ == "__main__":
    main()
