import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from utils.helpers import to_sec

tree = ast.parse(Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8'))
selected = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
            and n.name in {'auto_questionnaire', '_unresolved_course_tasks'}]
ns = {'logger': logging.getLogger('platform-completion-test'), 'to_sec': to_sec}
exec(compile(ast.Module(body=selected, type_ignores=[]), 'app.py', 'exec'), ns)


class CompletionTests(unittest.TestCase):
    def pilot(self, snapshots):
        return SimpleNamespace(running=True, driver=Mock(), stat_url='https://example.test/records',
            safe_sleep=Mock(return_value=True), sync_session=Mock(return_value=True),
            _submit_questionnaire=Mock(return_value=True),
            fetch_course_list_checked=Mock(side_effect=snapshots), _is_open_course=lambda c: True,
            config={'target_percentage': 1.05}, _exam_fail_counts={}, _completed_in_session={'q'})

    def test_submit_alone_never_confirms_and_three_failed_submissions_stop(self):
        record = {'course_id': 'q', 'write_questionnaire': 'survey', 'fill': '0'}
        p = self.pilot([[record]] * 9)
        for _ in range(3):
            self.assertFalse(ns['auto_questionnaire'](p, record))
        self.assertFalse(ns['auto_questionnaire'](p, record))
        self.assertEqual(p._submit_questionnaire.call_count, 3)

    def test_platform_can_confirm_after_delayed_read(self):
        record = {'course_id': 'q', 'write_questionnaire': 'survey', 'fill': '0'}
        p = self.pilot([[record], [{**record, 'fill': '1'}]])
        self.assertTrue(ns['auto_questionnaire'](p, record))
        self.assertEqual(p.fetch_course_list_checked.call_count, 2)

    def test_missing_records_and_incomplete_record_are_not_success(self):
        for snapshots in [[[]], [[{'course_id': 'other', 'fill': '1'}]] * 3,
                          [[{'course_id': 'q'}]] * 3]:
            p = self.pilot(snapshots)
            self.assertFalse(ns['auto_questionnaire'](p, {'course_id': 'q'}))

    def test_explicitly_no_questionnaire_is_complete(self):
        p = self.pilot([[{'course_id': 'q', 'write_questionnaire': ''}]])
        self.assertTrue(ns['auto_questionnaire'](p, {'course_id': 'q'}))

    def test_read_error_is_recorded_and_stop_prevents_submission(self):
        p = self.pilot([])
        p.fetch_course_list_checked.side_effect = ValueError('invalid response')
        self.assertFalse(ns['auto_questionnaire'](p, {'course_id': 'q'}))
        self.assertEqual(p._questionnaire_fail_counts['q'], 1)
        p.running = False
        ns['auto_questionnaire'](p, {'course_id': 'q'})
        self.assertEqual(p._submit_questionnaire.call_count, 1)

    def test_cookie_failure_is_bounded(self):
        p = self.pilot([])
        p.sync_session.return_value = False
        self.assertFalse(ns['auto_questionnaire'](p, {'course_id': 'q'}))
        self.assertEqual(p._questionnaire_fail_counts['q'], 1)

    def test_final_audit_includes_skipped_exam_and_unconfirmed_questionnaire(self):
        record = {'course_id': 'q', 'caption': 'test', 'rss': '01:03:00',
                  'criteria_content_hour': '01:00:00', 'exam_exists': '1',
                  'exam_score': None, 'fill': '0', 'write_questionnaire': 'survey'}
        p = self.pilot([])
        p._exam_fail_counts = {'q': 3}
        result = ns['_unresolved_course_tasks'](p, [record])
        self.assertEqual(result[0][1], ['測驗', '問卷'])
        p._exam_fail_counts.clear()
        self.assertEqual(ns['_unresolved_course_tasks'](p, [{**record, 'exam_score': 80, 'fill': '1'}]), [])

    def test_existing_platform_pass_preserves_target_margin_policy(self):
        p = self.pilot([])
        record = {'course_id': 'q', 'rss': '01:00:00', 'criteria_content_hour': '01:00:00',
                  'exam_exists': '1', 'exam_score': 80, 'fill': '1', 'write_questionnaire': 'survey'}
        self.assertEqual(ns['_unresolved_course_tasks'](p, [record]), [])
        self.assertEqual(ns['_unresolved_course_tasks'](p, [{**record, 'fill': '0'}])[0][1],
                         ['研習時數', '問卷'])


if __name__ == '__main__':
    unittest.main()
