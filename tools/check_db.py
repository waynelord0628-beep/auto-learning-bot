# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect("questions.db")
cur = conn.cursor()

keywords = ["精神疾病", "精神醫療", "精神", "疾病防治"]
for kw in keywords:
    cur.execute("SELECT COUNT(*) FROM questions WHERE question LIKE ?", (f"%{kw}%",))
    count = cur.fetchone()[0]
    print(f"[{kw}] 題數: {count}")

print()
print("=== 前10筆含「精神」的題目 ===")
cur.execute(
    "SELECT question, answer FROM questions WHERE question LIKE '%精神%' LIMIT 10"
)
for i, (q, a) in enumerate(cur.fetchall(), 1):
    print(f"{i}. Q: {q[:60]}")
    print(f"   A: {a[:40]}")

conn.close()
