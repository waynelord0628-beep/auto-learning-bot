import unittest, random
from types import SimpleNamespace
from unittest.mock import Mock
from utils.media_playback import start_unstarted_video

def choose(links, attempted, active_unit, keep_current_video):
    target = None
    if keep_current_video:
        target = next((link for link in links if
            (link.text.strip(), link.get_attribute("href") or "") == active_unit), None)
    if target is None:
        target = next(
            (link for link in links if link.text not in attempted),
            random.choice(links) if links else None,
        )
    return target

class Tests(unittest.TestCase):
 def setUp(self):
  self.first=SimpleNamespace(text='上集',get_attribute=lambda name:'first')
  self.second=SimpleNamespace(text='下集',get_attribute=lambda name:'second')
 def test_playing_keeps_current_over_unvisited(self):
  self.assertIs(choose([self.first,self.second],{'上集'},('上集','first'),True),self.first)
 def test_ended_can_advance(self):
  self.assertIs(choose([self.first,self.second],{'上集'},('上集','first'),False),self.second)
 def test_missing_old_link_recovers(self):
  self.assertIs(choose([self.second],{'上集'},('上集','first'),True),self.second)
 def test_inspection_passes_readonly_flag(self):
  d=Mock();d.execute_script.return_value='playing'
  self.assertEqual(start_unstarted_video(d,inspect_only=True),'playing')
  self.assertEqual(d.execute_script.call_args.args[-1],True)
  d.find_elements.assert_not_called()
if __name__ == '__main__': unittest.main()
