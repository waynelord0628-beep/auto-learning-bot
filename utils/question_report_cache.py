"""Short-lived GAS receipts, separate from the question bank and credentials."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import unicodedata
import uuid


def _text(value):
    return " ".join(unicodedata.normalize("NFC", str(value)).split())


def question_key(url, course, item):
    content = [url, _text(course), _text(item.get("type", "")),
               _text(item.get("question", "")),
               sorted(_text(option) for option in item.get("options", []))]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode("utf-8")).hexdigest()


class QuestionReportCache:
    ACCEPTED_SECONDS = 6 * 60 * 60
    LEASE_SECONDS = 5 * 60
    RETRY_SECONDS = 60

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutoLearningBot" / "question_report_receipts.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=2)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def reserve(self, url, course, questions):
        owner = uuid.uuid4().hex
        now = time.time()
        pending = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM receipts WHERE expires <= ?", (now,))
            for item in questions:
                if not _text(item.get("question", "")):
                    continue
                key = question_key(url, course, item)
                inserted = conn.execute("INSERT OR IGNORE INTO receipts VALUES (?, ?, ?)",
                                        (key, owner, now + self.LEASE_SECONDS)).rowcount
                if inserted:
                    pending.append(item)
        return owner, pending

    def finish(self, owner, accepted):
        delay = self.ACCEPTED_SECONDS if accepted else self.RETRY_SECONDS
        with self._connect() as conn:
            conn.execute("UPDATE receipts SET expires = ? WHERE owner = ?", (time.time() + delay, owner))
