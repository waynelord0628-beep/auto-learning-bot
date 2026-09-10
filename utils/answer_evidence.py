"""Answer candidates and observed attempts, isolated from the legacy question bank."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import uuid


def text(value):
    return ' '.join(unicodedata.normalize('NFC', str(value)).split())


def identity(course_id, item):
    return hashlib.sha256(json.dumps(['ecpa', str(course_id), text(item['question']),
        item['type'], sorted(text(x) for x in item['options'])], ensure_ascii=False).encode()).hexdigest()


def valid_answers(item, answers):
    options = [text(x) for x in item['options']]
    answers = [text(x) for x in answers]
    return (bool(options) and all(options) and len(set(options)) == len(options)
        and bool(answers) and len(set(answers)) == len(answers)
        and all(x in options for x in answers)
        and (item['type'] == '多選' or len(answers) == 1))


class AnswerEvidence:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AutoLearningBot' / 'answer_evidence.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS candidates (key TEXT, answer TEXT, source TEXT, created REAL, PRIMARY KEY(key, answer, source))')
            db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, course TEXT, questions TEXT, outcome TEXT, created REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT, accepted INTEGER DEFAULT 0, retry_after REAL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS confirmations (key TEXT, answer TEXT, attempt TEXT, PRIMARY KEY(key, answer, attempt))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        try:
            with db:
                yield db
        finally:
            db.close()

    def candidate(self, course_id, item, answers, source='local_ai'):
        if not valid_answers(item, answers):
            return False
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?)',
                (identity(course_id, item), json.dumps(sorted(text(x) for x in answers), ensure_ascii=False), source, time.time()))
        return True

    def lookup(self, course_id, item, verified_only=False):
        key = identity(course_id, item)
        with self.connect() as db:
            confirmed = db.execute('SELECT DISTINCT answer FROM confirmations WHERE key=?', (key,)).fetchall()
            if len(confirmed) > 1:
                return None  # Conflicting evidence is never resolved by last-write-wins.
            if confirmed:
                return {'answers': json.loads(confirmed[0][0]), 'source': 'platform_confirmed'}
            if verified_only:
                return None
            rows = db.execute('SELECT DISTINCT answer FROM candidates WHERE key=?', (key,)).fetchall()
            if len(rows) == 1:
                return {'answers': json.loads(rows[0][0]), 'source': 'candidate_unverified'}
        return None

    def import_cloud(self, course_id, rows):
        if not isinstance(rows, list):
            return 0
        added = 0
        for item in rows:
            if not isinstance(item, dict) or item.get('status') != 'pending' or item.get('source') != 'ai_unverified':
                continue
            answer = item.get('candidate_answer')
            if not isinstance(answer, str) or not isinstance(item.get('options'), list) or not item.get('question') or not item.get('type'):
                continue
            # Only exact option text, never guessed numeric positions or split multi-select strings.
            added += bool(self.candidate(course_id, item, [answer], 'cloud_ai'))
        return added

    def prepare(self, course_id, questions):
        attempt = uuid.uuid4().hex
        # Whitelist fields: no account, cookies, page URLs or form tokens.
        clean = [{k: q.get(k) for k in ('question', 'type', 'options', 'selected', 'source', 'selection_observed')} for q in questions]
        with self.connect() as db:
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?)',
                (attempt, str(course_id), json.dumps(clean, ensure_ascii=False), '{}', time.time()))
        return attempt

    def finish(self, attempt, result_text, disclosed=None):
        scores = re.findall(r'總分\s*=\s*(\d+(?:\.\d+)?)', result_text)
        score = float(scores[0]) if len(set(scores)) == 1 else None
        outcome = {'score': score, 'status': 'failed' if '不及格' in result_text else ('passed' if '及格' in result_text else 'unknown'),
                   'result_observed': score is not None, 'confirmed': 0}
        with self.connect() as db:
            row = db.execute('SELECT course, questions FROM attempts WHERE id=?', (attempt,)).fetchone()
            if row is None:
                raise ValueError('Unknown attempt')
            # A total score alone never confirms individual answers in this adapter.
            # Only the already disclosed answer key matched against the recorded options does.
            if score is not None:
                for item in json.loads(row[1]):
                    record = (disclosed or {}).get(text(item['question']))
                    if not isinstance(record, dict) or record.get('type') != item['type']:
                        continue
                    options = record.get('options')
                    if not isinstance(options, list) or sorted(text(x) for x in options) != sorted(text(x) for x in item['options']):
                        continue
                    answers = record.get('answers')
                    if isinstance(answers, list) and valid_answers(item, answers):
                        db.execute('INSERT OR IGNORE INTO confirmations VALUES (?,?,?)',
                            (identity(row[0], item), json.dumps(sorted(text(x) for x in answers), ensure_ascii=False), attempt))
                        outcome['confirmed'] += 1
            db.execute('UPDATE attempts SET outcome=? WHERE id=?', (json.dumps(outcome), attempt))
        return outcome


    def queue_confirmed(self, attempt, caption):
        """Persist only locally observed confirmations; never export AI or total-score guesses."""
        with self.connect() as db:
            row = db.execute('SELECT course, questions FROM attempts WHERE id=?', (attempt,)).fetchone()
            if row is None:
                return 0
            queued = 0
            for item in json.loads(row[1]):
                key = identity(row[0], item)
                confirmations = db.execute('SELECT DISTINCT answer FROM confirmations WHERE key=?', (key,)).fetchall()
                own = db.execute('SELECT answer FROM confirmations WHERE key=? AND attempt=?', (key, attempt)).fetchone()
                if len(confirmations) != 1 or own is None:
                    continue
                answers = json.loads(own[0])
                if not valid_answers(item, answers):
                    continue
                record = {k: item[k] for k in ('question', 'options', 'type')}
                record.update(answers=answers, source='platform_disclosed')
                payload = json.dumps({'course_id': row[0], 'course': str(caption), 'records': [record]}, ensure_ascii=False, separators=(',', ':'))
                receipt = hashlib.sha256((key + own[0]).encode()).hexdigest()
                queued += db.execute('INSERT OR IGNORE INTO outbox (id,payload) VALUES (?,?)', (receipt, payload)).rowcount
            return queued

    def flush(self, url, key, post):
        """Batch per course; persistent leases prevent duplicate concurrent sends."""
        now = time.time()
        batches = {}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT id,payload FROM outbox WHERE accepted=0 AND retry_after<=? LIMIT 50', (now,)).fetchall()
            for receipt, raw in rows:
                payload = json.loads(raw)
                record = payload['records'][0]
                confirmed = db.execute('SELECT DISTINCT answer FROM confirmations WHERE key=?',
                    (identity(payload['course_id'], record),)).fetchall()
                if len(confirmed) != 1 or json.loads(confirmed[0][0]) != sorted(text(x) for x in record['answers']):
                    # A conflict discovered after queuing must also block stale uploads.
                    db.execute('UPDATE outbox SET accepted=-1 WHERE id=?', (receipt,))
                    continue
                db.execute('UPDATE outbox SET retry_after=? WHERE id=?', (now + 900, receipt))
                batch = batches.setdefault((payload['course_id'], payload['course']), {'ids': [], 'records': []})
                batch['ids'].append(receipt)
                batch['records'].extend(payload['records'])
        import hmac
        accepted = 0
        for (course_id, caption), batch in batches.items():
            payload = json.dumps({'course_id': course_id, 'course': caption, 'records': batch['records']}, ensure_ascii=False, separators=(',', ':'))
            try:
                signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
                response = post(url, json={'action': 'ecpa_evidence', 'payload': payload, 'signature': signature}, timeout=45)
                response.raise_for_status()
                acknowledgement = response.json()
                ok = isinstance(acknowledgement, dict) and acknowledgement.get('ok') is True and acknowledgement.get('status') == 'ok' and not acknowledgement.get('dry_run')
            except Exception:
                ok = False
            with self.connect() as db:
                for receipt in batch['ids']:
                    db.execute('UPDATE outbox SET accepted=?,retry_after=? WHERE id=?', (int(ok), time.time() + (0 if ok else 60), receipt))
            accepted += len(batch['ids']) if ok else 0
        return accepted
