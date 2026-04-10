# -*- coding: utf-8 -*-
"""
dedup.py
對 questions.db 按題目文字（question 欄位）去重。

去重規則：
  1. 相同 question 的多筆，保留 article_date 不為 NULL 的那筆。
  2. 若多筆都有 article_date，保留最新日期（date 最大）的那筆。
  3. 若都沒有 article_date，保留 id 最小（最早爬入）的那筆。
  4. 刪除其餘重複筆。

執行前會先顯示統計，確認後才執行刪除。
"""

import sys
import os
import sqlite3

if sys.stdout.encoding and sys.stdout.encoding.lower() in (
    "cp950",
    "cp932",
    "gbk",
    "gb2312",
):
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "questions.db")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ── 統計重複情況
    total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    dup_groups = conn.execute(
        "SELECT COUNT(*) FROM (SELECT question FROM questions GROUP BY question HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    dup_rows = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE question IN "
        "(SELECT question FROM questions GROUP BY question HAVING COUNT(*) > 1)"
    ).fetchone()[0]

    print(f"目前總題數：{total}")
    print(f"有重複的題目組數：{dup_groups}")
    print(f"涉及重複的行數：{dup_rows}（去重後可減少約 {dup_rows - dup_groups} 筆）")

    if dup_groups == 0:
        print("無重複，不需去重。")
        conn.close()
        return

    ans = input("\n是否執行去重？(y/N) ").strip().lower()
    if ans != "y":
        print("取消。")
        conn.close()
        return

    # ── 找出每個 question 要「保留」的 id
    # 策略：先選 article_date 不為 NULL 且最新的，若都是 NULL 則選 id 最小
    keep_ids = set()
    rows = conn.execute(
        """
        SELECT question, id, article_date
        FROM questions
        ORDER BY question, article_date DESC NULLS LAST, id ASC
        """
    ).fetchall()

    current_q = None
    for row in rows:
        q = row["question"]
        if q != current_q:
            current_q = q
            keep_ids.add(row["id"])

    print(f"\n保留 {len(keep_ids)} 筆，刪除 {total - len(keep_ids)} 筆...")

    # ── 批次刪除（分批避免 SQLite 參數上限）
    all_ids = [r["id"] for r in conn.execute("SELECT id FROM questions").fetchall()]
    delete_ids = [i for i in all_ids if i not in keep_ids]

    BATCH = 500
    deleted = 0
    for start in range(0, len(delete_ids), BATCH):
        batch = delete_ids[start : start + BATCH]
        placeholders = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM questions WHERE id IN ({placeholders})", batch)
        conn.commit()
        deleted += len(batch)
        print(f"  已刪除 {deleted}/{len(delete_ids)}...")

    # ── 重新統計
    new_total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    with_date = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE article_date IS NOT NULL"
    ).fetchone()[0]
    print(f"\n去重完成！")
    print(f"  去重前：{total} 題")
    print(f"  去重後：{new_total} 題（減少 {total - new_total} 筆）")
    print(f"  有 article_date：{with_date} 筆")

    conn.execute("VACUUM")
    conn.commit()
    conn.close()
    print("VACUUM 完成，DB 已壓縮。")


if __name__ == "__main__":
    main()
