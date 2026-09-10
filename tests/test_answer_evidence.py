import tempfile
from pathlib import Path
import unittest
from utils.answer_evidence import AnswerEvidence, identity


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AnswerEvidence(Path(self.tmp.name) / 'evidence.db')
        self.q = {'question': '是否不可以？', 'type': '單選', 'options': ['可以', '不可以'], 'selected': ['可以'], 'source': 'local_ai', 'selection_observed': True}

    def disclosed(self, answers, **overrides):
        record = dict(options=self.q['options'], type=self.q['type'], answers=answers)
        record.update(overrides)
        return {self.q['question']: record}

    def test_shuffle_and_version_isolation(self):
        self.store.candidate('1', self.q, ['不可以'])
        shuffled = dict(self.q, options=list(reversed(self.q['options'])))
        self.assertEqual(self.store.lookup('1', shuffled)['answers'], ['不可以'])
        self.assertIsNone(self.store.lookup('2', shuffled))
        self.assertNotEqual(identity('1', self.q), identity('1', dict(self.q, question='是否可以？')))
        self.assertNotEqual(identity('1', self.q), identity('1', dict(self.q, options=['可以', '禁止'])))

    def test_totals_never_promote(self):
        for score in [0, 30, 80, 100]:
            attempt = self.store.prepare('1', [self.q])
            outcome = self.store.finish(attempt, f'總分 = {score} 評量結果 =及格')
            self.assertEqual(outcome['confirmed'], 0)
        self.assertIsNone(self.store.lookup('1', self.q, verified_only=True))

    def test_exact_disclosed_key_promotes_and_conflict_blocks(self):
        attempt = self.store.prepare('1', [self.q])
        self.store.finish(attempt, '總分 = 80', self.disclosed(['不可以']))
        self.assertEqual(self.store.lookup('1', self.q)['source'], 'platform_confirmed')
        self.store.finish(attempt, '總分 = 80', self.disclosed(['不可以']))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM confirmations').fetchone()[0], 1)
        other = self.store.prepare('1', [self.q])
        self.store.finish(other, '總分 = 80', self.disclosed(['可以']))
        self.assertIsNone(self.store.lookup('1', self.q))

    def test_invalid_disclosure_does_not_promote(self):
        for key in [self.disclosed(['1']), {'是否可以？': ['可以']}, self.disclosed(['可以', '不可以']), self.disclosed(['可以'], options=['可以', '禁止']), self.disclosed(['可以'], type='多選')]:
            attempt = self.store.prepare('1', [self.q])
            self.assertEqual(self.store.finish(attempt, '總分 = 80', key)['confirmed'], 0)
        attempt = self.store.prepare('1', [self.q])
        self.assertEqual(self.store.finish(attempt, '尚未送出', self.disclosed(['可以']))['confirmed'], 0)

    def test_candidate_conflict_and_multiselect(self):
        self.store.candidate('1', self.q, ['可以'])
        self.store.candidate('1', self.q, ['不可以'])
        self.assertIsNone(self.store.lookup('1', self.q))
        multi = dict(self.q, type='多選')
        self.assertTrue(self.store.candidate('1', multi, ['可以', '不可以']))
        self.assertEqual(set(self.store.lookup('1', multi)['answers']), {'可以', '不可以'})
        duplicate = dict(self.q, options=['可以', '可以'])
        self.assertFalse(self.store.candidate('1', duplicate, ['可以']))

    def test_cloud_candidate_requires_exact_text_and_status(self):
        item = dict(self.q, candidate_answer='2', status='pending', source='ai_unverified')
        self.assertEqual(self.store.import_cloud('1', [item]), 0)
        item['candidate_answer'] = '不可以'
        self.assertEqual(self.store.import_cloud('1', [item]), 1)
        self.assertIsNone(self.store.lookup('1', self.q, verified_only=True))

    def test_prepare_drops_secrets_and_has_no_premature_result(self):
        attempt = self.store.prepare('1', [dict(self.q, password='secret', url='token')])
        with self.store.connect() as db:
            questions, outcome = db.execute('SELECT questions,outcome FROM attempts WHERE id=?', (attempt,)).fetchone()
        self.assertNotIn('secret', questions)
        self.assertNotIn('token', questions)
        self.assertEqual(outcome, '{}')

    def test_outbox_only_exports_confirmed_and_retries_failure(self):
        import hashlib
        import hmac
        import json
        from unittest.mock import Mock
        attempt = self.store.prepare('1', [self.q])
        self.store.finish(attempt, '總分 = 80')
        self.assertEqual(self.store.queue_confirmed(attempt, '課程'), 0)
        self.store.finish(attempt, '總分 = 80', self.disclosed(['不可以']))
        self.assertEqual(self.store.queue_confirmed(attempt, '課程'), 1)
        self.assertEqual(self.store.queue_confirmed(attempt, '課程'), 0)
        post = Mock(side_effect=TimeoutError)
        self.assertEqual(self.store.flush('https://example.invalid', 'test-key', post), 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT accepted FROM outbox').fetchone()[0], 0)
            db.execute('UPDATE outbox SET retry_after=0')
        response = Mock()
        response.json.return_value = {'ok': True, 'status': 'ok', 'published': 1}
        post = Mock(return_value=response)
        self.assertEqual(self.store.flush('https://example.invalid', 'test-key', post), 1)
        sent = post.call_args.kwargs['json']
        self.assertEqual(sent['signature'], hmac.new(b'test-key', sent['payload'].encode(), hashlib.sha256).hexdigest())
        self.assertEqual(json.loads(sent['payload'])['records'][0]['source'], 'platform_disclosed')
        self.assertEqual(self.store.flush('https://example.invalid', 'test-key', post), 0)
        self.assertEqual(post.call_count, 1)

    def test_conflicting_confirmations_are_not_exported(self):
        first = self.store.prepare('1', [self.q])
        self.store.finish(first, '總分 = 80', self.disclosed(['可以']))
        other = self.store.prepare('1', [self.q])
        self.store.finish(other, '總分 = 80', self.disclosed(['不可以']))
        self.assertEqual(self.store.queue_confirmed(first, '課程'), 0)

    def test_batch_one_course_and_block_late_conflict(self):
        from unittest.mock import Mock
        import json
        for number in range(3):
            q = dict(self.q, question=f'題目{number}')
            attempt = self.store.prepare('1', [q])
            self.store.finish(attempt, '總分 = 80', {q['question']: dict(options=q['options'], type=q['type'], answers=['可以'])})
            self.store.queue_confirmed(attempt, '課程')
        conflict_q = dict(self.q, question='題目0')
        attempt = self.store.prepare('1', [conflict_q])
        self.store.finish(attempt, '總分 = 80', {'題目0': dict(options=conflict_q['options'], type=conflict_q['type'], answers=['不可以'])})
        response = Mock()
        response.json.return_value = {'ok': True, 'status': 'ok'}
        post = Mock(return_value=response)
        self.assertEqual(self.store.flush('https://example.invalid', 'test-key', post), 2)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(json.loads(post.call_args.kwargs['json']['payload'])['records']), 2)


class PassiveDisclosureTests(unittest.TestCase):
    def test_read_only_adapter_never_requests_disclosure_or_writes_bank(self):
        import ast
        import logging
        import re
        from types import SimpleNamespace
        from unittest.mock import Mock
        source = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
        node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == '_harvest_correct_answers')
        namespace = {'re': re, 'logger': logging.getLogger('test')}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), namespace)
        driver = Mock()
        driver.current_url = 'https://example.invalid/learn/exam/view_result.php'
        driver.execute_script.return_value = [{'question': '1. 題目', 'options': ['甲', '乙'], 'type': '單選', 'answers': ['乙']}]
        obj = SimpleNamespace(driver=driver, http_session=Mock(), _save_answers_to_db=Mock())
        result = namespace['_harvest_correct_answers'](obj, driver.current_url)
        self.assertEqual(result['題目']['answers'], ['乙'])
        driver.refresh.assert_not_called()
        driver.get.assert_not_called()
        obj.http_session.get.assert_not_called()
        obj._save_answers_to_db.assert_not_called()
        script = driver.execute_script.call_args.args[0]
        self.assertNotIn('fetch(', script)
        self.assertNotIn('.click(', script)
        driver.execute_script.return_value = []
        self.assertEqual(namespace['_harvest_correct_answers'](obj, driver.current_url), {})

    def test_non_result_page_is_not_read_as_answer_key(self):
        import ast
        import logging
        import re
        from types import SimpleNamespace
        from unittest.mock import Mock
        source = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
        node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == '_harvest_correct_answers')
        namespace = {'re': re, 'logger': logging.getLogger('test')}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), namespace)
        driver = Mock(current_url='https://example.invalid/learn/exam/exam_start.php')
        self.assertEqual(namespace['_harvest_correct_answers'](SimpleNamespace(driver=driver), driver.current_url), {})
        driver.execute_script.assert_not_called()


if __name__ == '__main__':
    unittest.main()
