import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit

from utils.helpers import to_sec


source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
tree = ast.parse(source)
methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
           and node.name in {'recover_login_session', '_recover_course_after_logout'}]
namespace = {'logger': logging.getLogger('recovery-test'), 'to_sec': to_sec}
exec(compile(ast.Module(body=methods, type_ignores=[]), 'app.py', 'exec'), namespace)


class RecoveryTests(unittest.TestCase):
    def test_known_homepage_exit_requires_exact_host_path_and_frame_evidence(self):
        condition = next(node.test for node in ast.walk(tree) if isinstance(node, ast.If)
                         and 'page.scheme' in ast.unparse(node.test))
        expression = compile(ast.Expression(condition), 'app.py', 'eval')
        for url, frames, expected in [
            ('https://elearn.hrd.gov.tw/mooc/index.php', ['newsFrame'], True),
            ('https://npm.elearn.hrd.gov.tw/mooc/index.php', ['newsFrame'], True),
            ('https://center.elearn.hrd.gov.tw/mooc/index.php', ['newsFrame'], True),
            ('http://npm.elearn.hrd.gov.tw/mooc/index.php', ['newsFrame'], False),
            ('https://npm.elearn.hrd.gov.tw.example.test/mooc/index.php', ['newsFrame'], False),
            ('https://elearn.hrd.gov.tw/mooc/index.php', ['newsFrame', 's_catalog'], False),
            ('https://elearn.hrd.gov.tw/mooc/index.php', [], False),
            ('https://elearn.hrd.gov.tw/learn/path/index.php', ['newsFrame'], False),
            ('https://elearn.hrd.gov.tw.example.test/mooc/index.php', ['newsFrame'], False),
        ]:
            self.assertEqual(eval(expression, {'page': urlsplit(url), 'host': urlsplit(url).hostname,
                                             'frame_ids': frames}), expected)

    def pilot(self):
        return SimpleNamespace(running=True, driver=Mock(), stat_url='https://example.test/records',
                               safe_sleep=Mock(return_value=True), login=Mock(return_value=True),
                               sync_session=Mock(return_value=True), _accept_alert_if_present=Mock(),
                               _progress_cache={'old': ('stale', 0)})

    def test_success_requires_login_and_cookie_sync_and_invalidates_old_progress(self):
        pilot = self.pilot()
        self.assertTrue(namespace['recover_login_session'](pilot))
        pilot.login.assert_called_once()
        pilot.sync_session.assert_called_once()
        self.assertEqual(pilot._progress_cache, {})

    def test_login_failure_does_not_sync_or_claim_success(self):
        pilot = self.pilot()
        pilot.login.return_value = False
        self.assertFalse(namespace['recover_login_session'](pilot))
        pilot.sync_session.assert_not_called()
        self.assertIn('old', pilot._progress_cache)

    def test_cookie_failure_is_not_recovered(self):
        pilot = self.pilot()
        pilot.sync_session.return_value = False
        self.assertFalse(namespace['recover_login_session'](pilot))

    def test_stop_before_or_during_wait_never_logs_in(self):
        for before in [True, False]:
            pilot = self.pilot()
            if before:
                pilot.running = False
            else:
                pilot.safe_sleep.return_value = False
            self.assertFalse(namespace['recover_login_session'](pilot))
            pilot.login.assert_not_called()

    def test_stop_during_login_does_not_continue_to_cookie_sync(self):
        pilot = self.pilot()
        def login():
            pilot.running = False
            return True
        pilot.login.side_effect = login
        self.assertFalse(namespace['recover_login_session'](pilot))
        pilot.sync_session.assert_not_called()

    def test_three_unchanged_attempts_then_stop_progress_allows_recovery_again(self):
        pilot = self.pilot()
        pilot.recover_login_session = Mock(return_value=True)
        course = {'course_id': 'one', 'rss': '00:05:00'}
        recover = namespace['_recover_course_after_logout']
        for _ in range(3):
            self.assertTrue(recover(pilot, course))
        self.assertFalse(recover(pilot, course))
        self.assertEqual(pilot.recover_login_session.call_count, 3)
        self.assertFalse(recover(pilot, {**course, 'rss': '00:04:00'}))
        self.assertTrue(recover(pilot, {**course, 'rss': '00:06:00'}))
        self.assertTrue(recover(pilot, {**course, 'course_id': 'other'}))

    def test_main_loop_relogin_branch_really_recovers_before_success(self):
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "res == 'RELOGIN'")
        # Run the production branch inside its enclosing control-flow shape.
        loop = ast.While(test=ast.Constant(True), body=[branch, ast.Break()], orelse=[])
        module = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
        for success in [False, True]:
            pilot = SimpleNamespace(current_idx=4, _recover_course_after_logout=Mock(return_value=success))
            count = [0]
            def recover(course):
                count[0] += 1
                if count[0] > 1:
                    return False
                return success
            pilot._recover_course_after_logout.side_effect = recover
            log = Mock()
            exec(compile(module, 'app.py', 'exec'), {'self': pilot, 'res': 'RELOGIN',
                 'pending': [{'course_id': 'one'}], 'logger': log})
            self.assertEqual(pilot.current_idx, 3 if success else 4)
            self.assertEqual(log.info.call_count, 1 if success else 0)


if __name__ == '__main__':
    unittest.main()
