"""Regression tests for Taipei's local 100% / platform completion transition."""
import ast
from pathlib import Path
import types
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'taipei_eda_course.py'

def load(name, ns):
    tree = ast.parse(SOURCE.read_text(encoding='utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns[name]


class CompletionTests(unittest.TestCase):
    def checker(self, rows, running=lambda: True):
        ns = {'get_course_list': Mock(side_effect=rows), '_clean_status': lambda s: str(s or '').strip(),
              'parse_study_time': lambda s: int(s or 0),
              'wait_with_heartbeat': Mock(return_value=True)}
        fn = load('confirm_study_record', ns)
        course = {'href': 'https://example/course?id=1', 'quest': '填寫'}
        result = fn(None, None, course, 90, running)
        return result, course, ns

    def test_confirmed_platform_time_updates_questionnaire_status(self):
        result, course, _ = self.checker([[{'href': 'https://example/course?id=1', 'study': '95', 'done': '未完成', 'quest': '已完成'}]])
        self.assertTrue(result)
        self.assertEqual(course['quest'], '已完成')

    def test_delayed_record_is_rechecked(self):
        result, _, ns = self.checker([[], [{'href': 'https://example/course?id=1', 'study': '95'}]])
        self.assertTrue(result)
        self.assertEqual(ns['get_course_list'].call_count, 2)

    def test_missing_blank_or_incomplete_record_never_counts_as_complete(self):
        for rows in ([], [{'href': 'https://example/course?id=1'}],
                     [{'href': 'https://example/course?id=1', 'study': '20', 'done': '未完成'}],
                     [{'href': 'https://example/course?id=2', 'study': '100', 'done': '已完成'}]):
            with self.subTest(rows=rows):
                result, _, ns = self.checker([rows] * 3)
                self.assertFalse(result)
                self.assertEqual(ns['get_course_list'].call_count, 3)

    def test_stop_prevents_readback(self):
        result, _, ns = self.checker([], lambda: False)
        self.assertFalse(result)
        ns['get_course_list'].assert_not_called()

    def test_time_target_exits_even_without_chapter_icon(self):
        leave = object()
        driver = Mock(current_url='https://example/player')
        wait = Mock()
        wait.until.return_value = leave
        ns = {'time': types.SimpleNamespace(time=Mock(side_effect=[0, 100]), sleep=Mock()),
              'parse_study_time': lambda s: 0,
              'get_scorm_player_url': Mock(return_value='player'),
              'pause_and_mute_media': Mock(),
              'get_chapters': Mock(return_value=[{'scoid':'1', 'name':'課程', 'done':False}]),
              'dismiss_alerts': Mock(),
              'EC': types.SimpleNamespace(element_to_be_clickable=lambda x:x),
              'By': types.SimpleNamespace(XPATH='xpath'),
              'confirm_study_record': Mock(return_value=True)}
        fn = load('do_scorm_course', ns)
        course = {'name':'課程', 'href':'https://example/course?id=1', 'cert_hrs':0.05}
        self.assertTrue(fn(driver, wait, course))
        driver.execute_script.assert_called_once_with('arguments[0].click();', leave)
        ns['confirm_study_record'].assert_called_once()
        self.assertEqual(ns['get_chapters'].call_count, 2)

    def test_target_reached_mid_round_does_not_visit_remaining_chapters(self):
        clock = [0.0]
        driver = Mock(current_url='https://example/player')
        wait = Mock()
        def advance(seconds, *args, **kwargs):
            clock[0] += seconds
            return True
        chapters = [{'scoid':str(i), 'name':f'單元 {i}', 'done':False} for i in range(5)]
        ns = {'time': types.SimpleNamespace(time=lambda:clock[0], sleep=advance),
              'parse_study_time': lambda s:0, 'get_scorm_player_url':Mock(return_value='player'),
              'pause_and_mute_media':Mock(), 'get_chapters':Mock(return_value=chapters),
              'click_chapter_by_scoid':Mock(return_value=True), 'is_chapter_done':Mock(return_value=False),
              'wait_with_heartbeat':advance, 'RESIDENCE_TIME':90,
              'sec_to_hms':str, 'draw_bar':lambda *args:'', 'dismiss_alerts':Mock(),
              'EC':types.SimpleNamespace(element_to_be_clickable=lambda x:x),
              'By':types.SimpleNamespace(XPATH='xpath'), 'confirm_study_record':Mock(return_value=True)}
        fn = load('do_scorm_course', ns)
        self.assertTrue(fn(driver, wait, {'name':'課程', 'href':'course', 'cert_hrs':0.05}))
        ns['click_chapter_by_scoid'].assert_called_once_with(driver, '0')
        ns['confirm_study_record'].assert_called_once()

if __name__ == '__main__':
    unittest.main()
