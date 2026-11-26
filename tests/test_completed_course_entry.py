import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
tree = ast.parse(source)
method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == '_confirm_completed_course_review')


class CompletedCourseEntryTests(unittest.TestCase):
    def run_method(self, pilot, button=None, timeout=False):
        wait = Mock()
        wait.return_value.until.side_effect = TimeoutException() if timeout else None
        wait.return_value.until.return_value = button
        ns = dict(logger=logging.getLogger('completed-entry-test'), By=By, EC=EC,
                  TimeoutException=TimeoutException, WebDriverWait=wait)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'app.py', 'exec'), ns)
        return ns['_confirm_completed_course_review'](pilot)

    def test_only_known_completed_review_notice_is_accepted(self):
        button = Mock(text='確定')
        button.find_element.return_value.text = '您已完成此課程，重新學習將無法重複取得時數，確定要繼續嗎？'
        self.assertTrue(self.run_method(SimpleNamespace(running=True, driver=Mock()), button))
        button.click.assert_called_once()

    def test_absent_notice_allows_normal_course_entry(self):
        self.assertTrue(self.run_method(SimpleNamespace(running=True, driver=Mock()), timeout=True))

    def test_unknown_confirmation_is_not_accepted(self):
        for message, label in [('您已完成此課程，請接受新條款', '確定'),
                               ('您已完成此課程，重新學習將無法重複取得時數', '付款')]:
            button = Mock(text=label)
            button.find_element.return_value.text = message
            self.assertFalse(self.run_method(SimpleNamespace(running=True, driver=Mock()), button))
            button.click.assert_not_called()

    def test_stop_prevents_confirmation(self):
        button = Mock()
        self.assertFalse(self.run_method(SimpleNamespace(running=False, driver=Mock()), button))
        button.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
