import ast
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from utils.ui_log_buffer import LogBuffer


class ResponsivenessTests(unittest.TestCase):
    def test_log_burst_is_bounded_and_counts_discarded(self):
        buffer = LogBuffer(capacity=100)
        threads = [threading.Thread(target=lambda: [buffer.append('x'*9000) for _ in range(1000)]) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        first, dropped = buffer.drain(50)
        second, again = buffer.drain(50)
        self.assertEqual((len(first),len(second),dropped,again), (50,50,3900,0))
        self.assertEqual(len(first[0]),8192)
        self.assertEqual(buffer.drain(), ([],0))

    def test_log_clear_discards_previous_run(self):
        buffer=LogBuffer(1)
        buffer.append('old');buffer.append('old2');buffer.clear();buffer.append('new')
        self.assertEqual(buffer.drain(), (['new'],0))

    def test_gui_start_failure_never_waits_for_enter_and_still_cleans_up(self):
        source=(Path(__file__).resolve().parents[1]/'app.py').read_text(encoding='utf-8')
        node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name=='run')
        namespace={'logger':logging.getLogger('test'),'sys':SimpleNamespace(stdin=Mock(isatty=Mock(return_value=True))),
                   'Fore':SimpleNamespace(CYAN='',RED='',GREEN='',YELLOW=''),'Style':SimpleNamespace(RESET_ALL=''),
                   'input':Mock(), 'print':Mock()}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),namespace)
        pilot=SimpleNamespace(config={},version='test',log_callback=Mock(),_start_keep_awake=Mock(),init_engine=Mock(return_value=False),_cleanup=Mock())
        namespace['run'](pilot)
        namespace['input'].assert_not_called()
        pilot._cleanup.assert_called_once()
        pilot.log_callback=None
        namespace['run'](pilot)
        namespace['input'].assert_called_once()

    def test_http_pool_closes_only_at_terminal_cleanup(self):
        source=(Path(__file__).resolve().parents[1]/'app.py').read_text(encoding='utf-8')
        node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name=='_cleanup')
        namespace={};exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),namespace)
        pilot=SimpleNamespace(config={},_cleanup_lock=threading.Lock(),driver=None,http_session=Mock(),_stop_keep_awake=Mock(),_kill_managed_processes=Mock(),running=True)
        namespace['_cleanup'](pilot,stop=False)
        pilot.http_session.close.assert_not_called()
        namespace['_cleanup'](pilot)
        pilot.http_session.close.assert_called_once()


if __name__=='__main__':unittest.main()
