"""Explicit rollback uses only disposable workspaces and simulated app launches."""
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
import unittest
import test_release_switch as fixtures
from host import Host
from dervo_connections import Rejected
from release_history import history_root, switch_record, rollback_choice, prepare_rollback
from release_switch import prepare, run
from release_handoff import verified_backup

class RollbackTests(unittest.TestCase):
    setUp = fixtures.SwitchTests.setUp
    close_host = fixtures.SwitchTests.close_host
    bundle = fixtures.SwitchTests.bundle
    sign = fixtures.SwitchTests.sign
    def first_update(self):
        (self.root/'data/decision.txt').write_text('before update')
        plan=prepare(self.host,self.args);self.close_host()
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'launch'}]),patch('release_switch.report'),patch('release_switch.os.kill',side_effect=ProcessLookupError),patch('release_switch.open_target',return_value=1234):
            record=run(plan)
        self.switch_id=Path(record['directory']).name
        self.host=Host(self.root/'data')
        self.rollback_args={'switchId':self.switch_id,'currentExecutable':str(self.target/'Contents/MacOS/dervo-local'),'dataOverride':True,'parentPid':1000000}
        return record
    def test_rollback_restores_old_workspace_and_preserves_newer_work(self):
        with patch('release_history.Path.home',return_value=self.root):
            first=self.first_update()
            (self.root/'data/decision.txt').write_text('newer work')
            self.host.store.add_profile('new-account','codex',self.root/'other-profile','later@example.test','personal')
            choice=rollback_choice(self.root/'data','new')
            self.assertEqual(choice['switchId'],self.switch_id)
            self.assertEqual(choice['version'],'old')
            plan=prepare_rollback(self.host,self.rollback_args);self.close_host()
            with self.sign(),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'launch'}]),patch('release_switch.report'),patch('release_switch.os.kill',side_effect=ProcessLookupError),patch('release_switch.open_target',return_value=5678):
                result=run(plan)
            self.assertEqual(result['state'],'opened')
            self.assertEqual((self.root/'data/decision.txt').read_text(),'before update')
            newer=Path(result['directory'])/'newer-work'
            verified_backup(newer,self.root/'data')
            self.assertEqual((newer/'workspace/decision.txt').read_text(),'newer work')
            with sqlite3.connect(newer/'workspace/conversations.sqlite3') as db:
                self.assertIsNotNone(db.execute("SELECT id FROM profiles WHERE id='new-account'").fetchone())
            with sqlite3.connect(self.root/'data/conversations.sqlite3') as db:
                self.assertIsNone(db.execute("SELECT id FROM profiles WHERE id='new-account'").fetchone())
            self.assertIsNone(rollback_choice(self.root/'data','old'))
    def test_wrong_version_workspace_mode_and_record_are_rejected(self):
        with patch('release_history.Path.home',return_value=self.root):
            self.first_update()
            for args in [{**self.rollback_args,'dataOverride':False},{**self.rollback_args,'currentExecutable':str(self.current/'Contents/MacOS/dervo-local')},{**self.rollback_args,'switchId':'../other'}]:
                with self.assertRaises(Rejected):prepare_rollback(self.host,args)
            with self.assertRaises(Rejected):switch_record(self.root/'another-data',self.switch_id)
            self.assertIsNone(rollback_choice(self.root/'data','unrelated-version'))
    def test_changed_backup_is_refused_while_current_host_is_still_open(self):
        with patch('release_history.Path.home',return_value=self.root):
            first=self.first_update()
            plan=prepare_rollback(self.host,self.rollback_args)
            (Path(first['directory'])/'backup/workspace/decision.txt').write_text('tampered')
            with self.sign(),patch('release_switch.report') as report,patch('release_switch.open_target') as launch:
                with self.assertRaisesRegex(RuntimeError,'changed since verification'):run(plan)
            report.assert_not_called();launch.assert_not_called()
            self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM projects').fetchone()[0],2)
    def test_linked_history_record_is_ignored_without_reading_it(self):
        with patch('release_history.Path.home',return_value=self.root):
            self.first_update()
            path=history_root(self.root/'data')/('a'*32);path.mkdir()
            (path/'switch.json').symlink_to(self.root/'missing')
            self.assertEqual(rollback_choice(self.root/'data','new')['switchId'],self.switch_id)
