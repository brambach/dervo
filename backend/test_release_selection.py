import unittest
from unittest.mock import patch
from pathlib import Path
from release_selection import selected_target
from release_handoff import RECOVERY_MARKER
from release_history import history_root
import test_release_history as fixtures

class SelectionTests(unittest.TestCase):
    setUp=fixtures.RollbackTests.setUp
    close_host=fixtures.RollbackTests.close_host
    bundle=fixtures.RollbackTests.bundle
    sign=fixtures.RollbackTests.sign
    first_update=fixtures.RollbackTests.first_update
    def test_successful_choice_redirects_old_app_and_never_loops(self):
        with patch('release_history.Path.home',return_value=self.root),self.sign():
            first=self.first_update()
            current=str(self.current/'Contents/MacOS/dervo-local');target=str(self.target/'Contents/MacOS/dervo-local')
            self.assertEqual(selected_target(self.root/'data',current,True),target)
            self.assertIsNone(selected_target(self.root/'data',target,True))
            self.assertIn('export DERVO_OPEN_THIS_VERSION=1',(Path(first['directory'])/'Open previous Dervo.command').read_text())
            launcher=(history_root(self.root/'data')/'Open current Dervo.command').read_text()
            self.assertNotIn('export DERVO_OPEN_THIS_VERSION',launcher)
    def test_changed_target_and_workspace_mode_fail_without_fallback(self):
        with patch('release_history.Path.home',return_value=self.root),self.sign():
            self.first_update()
            current=str(self.current/'Contents/MacOS/dervo-local')
            with self.assertRaisesRegex(RuntimeError,'doesn’t match'):selected_target(self.root/'data',current,False)
            (self.target/'Contents/MacOS/dervo-local').write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'changed'):selected_target(self.root/'data',current,True)
    def test_no_selection_and_pending_restore(self):
        with patch('release_history.Path.home',return_value=self.root):
            self.assertIsNone(selected_target(self.root/'data',self.current/'Contents/MacOS/dervo-local',True))
            (self.root/'data'/RECOVERY_MARKER).write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'interrupted'):selected_target(self.root/'data',self.current/'Contents/MacOS/dervo-local',True)
