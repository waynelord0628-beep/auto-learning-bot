import pathlib
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from utils.media_playback import start_unstarted_video


class PlaybackTests(unittest.TestCase):
    def test_playing_video_stops_frame_search(self):
        driver = Mock()
        driver.execute_script.return_value = 'playing'
        self.assertEqual(start_unstarted_video(driver), 'playing')
        driver.find_elements.assert_not_called()
        self.assertEqual(driver.switch_to.default_content.call_count, 2)

    def test_nested_player_restores_frame_context(self):
        driver = Mock()
        driver.execute_script.side_effect = ['unavailable', 'requested']
        driver.find_elements.return_value = [object()]
        self.assertEqual(start_unstarted_video(driver, muted=True), 'requested')
        driver.switch_to.parent_frame.assert_called_once()
        self.assertEqual(driver.switch_to.default_content.call_count, 2)
        self.assertTrue(driver.execute_script.call_args.args[1])

    def test_frame_tree_has_bounded_work(self):
        driver = Mock()
        driver.execute_script.return_value = 'unavailable'
        driver.find_elements.return_value = [object(), object(), object()]
        self.assertEqual(start_unstarted_video(driver), 'unavailable')
        self.assertEqual(driver.execute_script.call_count, 32)
        self.assertEqual(driver.switch_to.default_content.call_count, 2)

    def test_navigation_error_restores_context(self):
        driver = Mock()
        driver.execute_script.side_effect = RuntimeError('navigated')
        self.assertEqual(start_unstarted_video(driver), 'unavailable')
        self.assertEqual(driver.switch_to.default_content.call_count, 2)


class NativeVideoTests(unittest.TestCase):
    def test_native_non_jplayer_video_is_observed_without_control(self):
        import json, subprocess, shutil
        from utils.media_playback import START_VIDEO
        node = shutil.which('node')
        if not node: self.skipTest('Node runtime unavailable')
        script = "const video={paused:false,ended:false,error:null,closest:()=>null};const document={querySelectorAll:s=>s==='video'?[video]:[]};const window={};const run=new Function('document','window'," + json.dumps('return (function(){'+START_VIDEO+'}).call(null,false,true);') + ");if(run(document,window)!=='playing')throw Error('Native video missed');"
        subprocess.run([node,'-e',script],check=True,capture_output=True)

    def test_unstarted_unknown_player_is_not_auto_controlled(self):
        import json, subprocess, shutil
        from utils.media_playback import START_VIDEO
        node = shutil.which('node')
        if not node: self.skipTest('Node runtime unavailable')
        script = "const video={paused:true,ended:false,error:null,currentTime:0,readyState:4,closest:()=>null};const document={querySelectorAll:s=>[video]};const window={};const run=new Function('document','window'," + json.dumps('return (function(){'+START_VIDEO+'}).call(null,false,false);') + ");if(run(document,window)!=='unavailable')throw Error('Unknown player controlled');"
        subprocess.run([node,'-e',script],check=True,capture_output=True)
