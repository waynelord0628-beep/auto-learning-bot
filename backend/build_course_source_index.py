"""Export public source URLs only; do not copy DB answers or user credentials."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlparse


def course_key(value):
    return ''.join(c for c in unicodedata.normalize('NFC', value or '').lower()
                   if not c.isspace() and unicodedata.category(c)[0] not in 'PS')


def question_key(value):
    value = re.sub(r'^[\d０-９]+[.．、。）)\s]+', '', value)
    return ' '.join(unicodedata.normalize('NFC', value).split())


def build(database, output):
    buckets = defaultdict(lambda: defaultdict(set))
    allowed = {'www.peigogo.com', 'www.rodiyer.idv.tw', 'roddayeye.pixnet.net'}
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        for category, question, url in db.execute('SELECT category,question,source_url FROM questions'):
            parsed = urlparse(url or '')
            if parsed.scheme != 'https' or parsed.netloc not in allowed:
                continue
            values = ['q:' + question_key(question)]
            if category:
                values.append('c:' + course_key(category))
            for value in values:
                key = hashlib.sha256(value.encode()).hexdigest()
                buckets[key[:2]][key].add(url)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for prefix, entries in buckets.items():
        payload = {k: sorted(v) for k, v in sorted(entries.items())}
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if len(text.encode()) >= 900000:
            raise ValueError('Index shard exceeds GitHub Contents API limit')
        (output / (prefix + '.json')).write_text(text, encoding='utf-8')
    return {'shards': len(buckets), 'keys': sum(map(len, buckets.values()))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('output')
    args = parser.parse_args()
    print(json.dumps(build(args.database, args.output)))
