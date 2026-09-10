"""Offline regression tests; no browser, credentials, network or real process kills."""
import ast
import json
import logging
import pathlib
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch, Mock
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.config_store import atomic_write_json
from utils import playback_wait
import usage_tracker


def method(file, name, namespace=None):
    namespace = {} if namespace is None else namespace
    tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), namespace)
    return namespace[name]


class RegressionTests(unittest.TestCase):
    def test_atomic_write_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "config.json"
            p.write_text('{"accounts": []}', encoding="utf-8")
            original = p.read_bytes()
            with patch("utils.config_store.os.replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    atomic_write_json(p, {"accounts": ["new"]})
            self.assertEqual(p.read_bytes(), original)
            self.assertEqual(len(list(pathlib.Path(folder).iterdir())), 1)

    def test_atomic_serialization_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "config.json"
            p.write_text("original", encoding="utf-8")
            with self.assertRaises(TypeError):
                atomic_write_json(p, {"invalid": object()})
            self.assertEqual(p.read_text(), "original")

    def test_usage_never_overwrites_config_and_reuses_id(self):
        for content in ['{"accounts": [', '[]', '{"usage_device_id":"legacy-id","accounts":[]}']:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as folder:
                p = pathlib.Path(folder) / "config.json"
                p.write_text(content, encoding="utf-8")
                with patch.object(usage_tracker, "_base_dir", return_value=pathlib.Path(folder)), patch.object(usage_tracker, "_device_id", None):
                    first = usage_tracker.get_device_id()
                    self.assertEqual(usage_tracker.get_device_id(), first)
                    self.assertEqual(p.read_text(), content)
                    self.assertEqual(json.loads((p.parent / "usage_state.json").read_text())["usage_device_id"], first)
                    if "legacy-id" in content:
                        self.assertEqual(first, "legacy-id")

    def test_usage_readonly_keeps_in_memory_id(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(usage_tracker, "_base_dir", return_value=pathlib.Path(folder)), patch.object(usage_tracker, "_device_id", None), patch.object(usage_tracker, "atomic_write_json", side_effect=OSError()):
            self.assertEqual(usage_tracker.get_device_id(), usage_tracker.get_device_id())

    def test_playback_reduces_updates_and_flushes_at_end(self):
        for duration, expected in [(75, 15), (12, 3), (2, 1)]:
            clock = [0.0]
            calls = []
            fake = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda n: clock.__setitem__(0, clock[0]+n))
            with patch.object(playback_wait, "time", fake):
                self.assertTrue(playback_wait.wait_with_heartbeat(duration, lambda: True, lambda: calls.append(clock[0])))
            self.assertEqual(len(calls), expected)
            self.assertAlmostEqual(calls[-1], duration)

    def test_playback_cancels_before_next_browser_update(self):
        clock = [0.0]
        calls = []
        fake = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda n: clock.__setitem__(0, clock[0]+n))
        with patch.object(playback_wait, "time", fake):
            self.assertFalse(playback_wait.wait_with_heartbeat(75, lambda: clock[0] < 1, lambda: calls.append(clock[0])))
        self.assertLessEqual(clock[0], 1.2)
        self.assertEqual(calls, [])

    def test_startup_never_enumerates_foreign_processes(self):
        owner = types.SimpleNamespace(_kill_managed_processes=Mock())
        method("app.py", "kill_orphan_drivers")(owner)
        owner._kill_managed_processes.assert_called_once()

    def test_reused_pid_is_not_killed(self):
        proc = types.SimpleNamespace(create_time=lambda: 200, kill=Mock(), children=Mock(return_value=[]))
        ps = types.SimpleNamespace(Process=lambda pid: proc, NoSuchProcess=ProcessLookupError, AccessDenied=PermissionError)
        obj = types.SimpleNamespace(_managed_pids={99}, _managed_process_times={99: 100})
        fn = method("app.py", "_kill_managed_processes", {"psutil": ps, "logger": logging.getLogger(), "time": types.SimpleNamespace(sleep=lambda n: None)})
        fn(obj)
        proc.kill.assert_not_called()
        proc.children.assert_not_called()

    def test_cleanup_uses_captured_pilot(self):
        old = types.SimpleNamespace(_cleanup=Mock())
        new = types.SimpleNamespace(_cleanup=Mock())
        obj = types.SimpleNamespace(pilot=new)
        method("ui.py", "_cleanup_pilot_async", {"logger": logging.getLogger()})(obj, old, None)
        old._cleanup.assert_called_once()
        new._cleanup.assert_not_called()

    def test_stop_returns_while_cleanup_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        def cleanup():
            entered.set()
            release.wait(3)
        obj = types.SimpleNamespace(pilot=types.SimpleNamespace(running=True, _cleanup=cleanup), cleanup_thread=None, _pilot_thread=None, _run_stop_event=threading.Event())
        clean = method("ui.py", "_cleanup_pilot_async", {"logger": logging.getLogger()})
        obj._cleanup_pilot_async = lambda p, w: clean(obj, p, w)
        try:
            method("ui.py", "_request_stop_current_pilot", {"threading": threading})(obj)
            self.assertTrue(entered.wait(1))
            self.assertTrue(obj.cleanup_thread.is_alive())
            self.assertTrue(obj._run_stop_event.is_set())
            self.assertFalse(obj.pilot.running)
        finally:
            release.set()
            obj.cleanup_thread.join(2)

    def test_taipei_old_owner_cannot_close_new_driver(self):
        old, new = object(), object()
        driver = types.SimpleNamespace(quit=Mock())
        ns = {"_ACTIVE_DRIVER": driver, "_ACTIVE_OWNER": new}
        fn = method("taipei_eda_course.py", "force_close_active_driver", ns)
        fn(owner=old)
        driver.quit.assert_not_called()
        fn(owner=new)
        driver.quit.assert_called_once()
        self.assertIsNone(ns["_ACTIVE_DRIVER"])

    def test_restart_is_blocked_until_old_run_finishes(self):
        for active_worker, active_cleanup in [(True, False), (False, True)]:
            info = Mock()
            obj = types.SimpleNamespace(
                _pilot_thread=types.SimpleNamespace(is_alive=lambda: active_worker),
                cleanup_thread=types.SimpleNamespace(is_alive=lambda: active_cleanup),
            )
            fn = method("ui.py", "_start_pilot_background", {"QMessageBox": types.SimpleNamespace(information=info)})
            self.assertFalse(fn(obj, {}))
            info.assert_called_once()

    def test_concurrent_cleanup_quits_driver_once(self):
        quit_driver = Mock(side_effect=lambda: time.sleep(0.01))
        obj = types.SimpleNamespace(
            _cleanup_lock=threading.Lock(), running=True, config={},
            _stop_keep_awake=lambda: None,
            driver=types.SimpleNamespace(quit=quit_driver),
            _kill_managed_processes=lambda: None,
        )
        fn = method("app.py", "_cleanup")
        threads = [threading.Thread(target=fn, args=(obj,)) for _ in range(2)]
        for worker in threads:
            worker.start()
        for worker in threads:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        quit_driver.assert_called_once()

    def test_login_has_no_credential_defaults(self):
        fn = method("taipei_eda_course.py", "do_login")
        self.assertIsNone(fn.__defaults__)

    def test_initialization_is_background_and_stop_is_honored(self):
        entered, release = threading.Event(), threading.Event()
        instances = []
        class Pilot:
            def __init__(self, **kwargs):
                self.running = True
                self.run = Mock()
                self._cleanup = Mock()
                instances.append(self)
                entered.set()
                release.wait(3)
        signal = lambda: types.SimpleNamespace(notify=types.SimpleNamespace(connect=lambda callback: None))
        obj = types.SimpleNamespace(cleanup_thread=None, entry=types.SimpleNamespace(load_config=lambda: {"settings": {}}), immersive=types.SimpleNamespace(append_text=lambda text: None), _on_update_available=lambda *args: None)
        ns = {"threading": threading, "AdminEfficiencyPilot": Pilot, "UpdateSignal": signal, "logger": logging.getLogger(), "atexit": types.SimpleNamespace(unregister=lambda callback: None)}
        try:
            self.assertTrue(method("ui.py", "_start_pilot_background", ns)(obj, {"name": "test"}))
            self.assertTrue(entered.wait(1))
            obj._run_stop_event.set()
        finally:
            release.set()
            obj._pilot_thread.join(3)
        self.assertFalse(obj._pilot_thread.is_alive())
        instances[0].run.assert_not_called()
        instances[0]._cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
