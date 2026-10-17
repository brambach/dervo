"""The Mac icon picker's host side (beta.15, group 7): which icons an account may use, the founder check on its own
thread, the answer kept per account on this Mac, and the three lists of ids kept in step. No network, no native app."""
import json
from pathlib import Path
import re
import sqlite3
import threading
from types import SimpleNamespace
import unittest

import app_icons
from app_icons import AppIcons
from dervo_connections import Rejected

ROOT = Path(__file__).resolve().parent.parent
SUBJECT = '11111111-1111-1111-1111-111111111111'


class Account:
    def __init__(self, subject=SUBJECT):
        self.session = SimpleNamespace(user_id=subject, access_token='token')
        self.subject = subject

    def status(self):
        return {'identity': {'subject': self.subject, 'issuer': 'https://example.test/auth/v1'}}


class Fetch:
    """Stands in for the dervo_is_founder() call. Each answer waits until the test releases it."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0
        self.release, self.done = threading.Event(), threading.Event()

    def __call__(self, account):
        self.calls += 1
        self.release.wait(5)
        answer = self.answers.pop(0)
        self.done.set()
        if isinstance(answer, Exception):
            raise answer
        return answer

    def finish(self, icons):
        self.release.set()
        self.done.wait(5)
        for thread in threading.enumerate():
            if thread.name == 'dervo-founder-check':
                thread.join(5)
        self.release.clear(); self.done.clear()


class AppIconTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.now = [1000.0]
        self.host = SimpleNamespace(store=SimpleNamespace(db=self.db), account_session=Account())

    def icons(self, fetch):
        return AppIcons(self.host, clock=lambda: self.now[0], fetch=fetch)

    def available(self, status):
        return [icon['id'] for icon in status['icons'] if icon['available']]

    def test_nine_are_free_and_obsidian_waits_for_the_check(self):
        fetch = Fetch(True)
        icons = self.icons(fetch)
        status = icons.status()
        self.assertEqual(status['founder'], 'checking')
        self.assertEqual(self.available(status), ['cream', 'forest', 'meadow', 'peek', 'crew', 'terminal', 'pixel', 'night', 'clay'])
        self.assertEqual(icons.check('obsidian'), {'allowed': False, 'detail': app_icons.DETAIL['checking']})
        self.assertTrue(icons.check('clay')['allowed'])
        fetch.finish(icons)
        status = icons.status()
        self.assertEqual(status['founder'], 'founder')
        self.assertIn('obsidian', self.available(status))
        self.assertEqual(icons.check('obsidian'), {'allowed': True, 'detail': ''})
        self.assertEqual(fetch.calls, 1, 'one check an hour, not one per poll')

    def test_a_tester_after_launch_is_told_plainly(self):
        fetch = Fetch(False)
        icons = self.icons(fetch)
        icons.status(); fetch.finish(icons)
        status = icons.status()
        self.assertEqual((status['founder'], status['detail']), ('not_founder', app_icons.DETAIL['not_founder']))
        self.assertFalse(icons.check('obsidian')['allowed'])

    def test_it_stays_with_the_account_while_offline_and_a_failed_check_takes_nothing_away(self):
        fetch = Fetch(True, OSError('offline'))
        icons = self.icons(fetch)
        icons.status(); fetch.finish(icons)
        self.assertEqual(icons.status()['founder'], 'founder')
        # A new session on this Mac (a restart) with the network gone still knows.
        later = AppIcons(self.host, clock=lambda: self.now[0], fetch=fetch)
        self.assertEqual(later.status()['founder'], 'founder')
        fetch.finish(later)
        self.assertEqual(later.status()['founder'], 'founder')
        self.assertTrue(later.check('obsidian')['allowed'])

    def test_an_unanswered_check_is_unknown_never_a_guess(self):
        fetch = Fetch(OSError('offline'))
        icons = self.icons(fetch)
        icons.status(); fetch.finish(icons)
        status = icons.status()
        self.assertEqual(status['founder'], 'unknown')
        self.assertNotIn('obsidian', self.available(status))
        self.assertFalse(icons.check('obsidian')['allowed'])

    def test_it_checks_again_after_an_hour(self):
        fetch = Fetch(True, True)
        icons = self.icons(fetch)
        icons.status(); fetch.finish(icons)
        icons.status()
        self.assertEqual(fetch.calls, 1)
        self.now[0] += app_icons.RECHECK_SECONDS
        icons.status(); fetch.finish(icons)
        self.assertEqual(fetch.calls, 2)

    def test_each_account_has_its_own_answer(self):
        fetch = Fetch(True, False)
        icons = self.icons(fetch)
        icons.status(); fetch.finish(icons)
        self.host.account_session = Account('22222222-2222-2222-2222-222222222222')
        self.assertEqual(icons.status()['founder'], 'checking')
        fetch.finish(icons)
        self.assertEqual(icons.status()['founder'], 'not_founder')
        self.host.account_session = Account()
        self.assertEqual(icons.status()['founder'], 'founder')

    def test_signed_out_or_mismatched_sessions_read_as_signed_out(self):
        fetch = Fetch()
        self.host.account_session = None
        icons = self.icons(fetch)
        self.assertEqual(icons.status()['founder'], 'signed_out')
        account = Account(); account.session = SimpleNamespace(user_id='someone-else', access_token='token')
        self.host.account_session = account
        self.assertEqual(icons.status()['founder'], 'signed_out')
        self.assertEqual(fetch.calls, 0)
        self.assertTrue(icons.check('forest')['allowed'], 'the free icons need no account')

    def test_the_rpc_answer_must_be_a_boolean(self):
        client = SimpleNamespace(_request=lambda *args: {'founder': True})
        account = SimpleNamespace(session=SimpleNamespace(access_token='token'), client=client)
        with self.assertRaises(ValueError):
            AppIcons._fetch(account)
        calls = []
        client = SimpleNamespace(_request=lambda *args: calls.append(args) or True)
        self.assertTrue(AppIcons._fetch(SimpleNamespace(session=SimpleNamespace(access_token='token'), client=client)))
        self.assertEqual(calls, [('POST', '/rest/v1/rpc/dervo_is_founder', {}, 'token')])

    def test_only_listed_ids_and_exact_fields(self):
        icons = self.icons(Fetch())
        for icon in ('gold-leaf', 'Obsidian', '../cream', ''):
            self.assertFalse(icons.check(icon)['allowed'])
        for operation, args in [('app_icon_status', {'x': 1}), ('_app_icon_check', {}), ('_app_icon_check', {'icon': 1}),
                                ('app_icon_set', {'icon': 'clay'}), ('app_icon_founder_set', {})]:
            with self.assertRaises(Rejected):
                icons.dispatch(operation, args)


class CatalogueTests(unittest.TestCase):
    """icons/choices/choices.json, backend/app_icons.py and src/app_icon.rs list the same ids in the same order."""
    def test_the_three_lists_agree(self):
        catalogue = json.loads((ROOT / 'icons/choices/choices.json').read_text())['choices']
        self.assertEqual([(c['id'], c['name']) for c in catalogue], list(app_icons.ICONS))
        self.assertEqual([c['id'] for c in catalogue if c['founder']], [app_icons.FOUNDER])
        rust = (ROOT / 'src/app_icon.rs').read_text()
        listed = re.search(r'pub const ICONS: \[&str; \d+\] = \[(.*?)\];', rust).group(1)
        self.assertEqual(re.findall(r'"([a-z]+)"', listed), [icon for icon, _ in app_icons.ICONS])

    def test_every_choice_has_a_large_source_a_small_face_and_built_images(self):
        choices = ROOT / 'icons/choices'
        for choice in json.loads((choices / 'choices.json').read_text())['choices']:
            for key in ('source', 'small'):
                self.assertTrue((choices / choice[key]).is_file(), choice['id'] + ' ' + key)
            if 'tiny' in choice:
                self.assertTrue((choices / choice['tiny']).is_file(), choice['id'] + ' tiny')
            self.assertTrue((choices / (choice['id'] + '.icns')).is_file(), choice['id'] + ' ICNS: run scripts/build-icons.py')
            self.assertTrue((ROOT / 'frontend/public/app-icons' / (choice['id'] + '.png')).is_file(), choice['id'] + ' preview')

    def test_the_dock_tile_plugin_is_built_and_bundled_from_snapshotted_sources(self):
        import release_builder
        config = json.loads((ROOT / 'tauri.conf.json').read_text())
        self.assertIn('build-docktile.sh', config['build']['beforeBundleCommand'])
        self.assertEqual(config['bundle']['macOS']['files'],
                         {'PlugIns/DervoDockTile.docktileplugin': 'target/docktile/DervoDockTile.docktileplugin'})
        for item in ('src', 'scripts', 'icons', 'Info.plist', 'tauri.conf.json'):
            self.assertIn(item, release_builder.SOURCE_ITEMS)
        self.assertIn('<key>NSDockTilePlugIn</key>', (ROOT / 'Info.plist').read_text())
        plugin = (ROOT / 'src/docktile/Info.plist').read_text()
        self.assertIn('<string>DervoDockTile</string>', plugin)


if __name__ == '__main__':
    unittest.main()
