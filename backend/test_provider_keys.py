"""API-key accounts beside subscription sign-in. Disposable stores, a memory Keychain, a scripted Codex login and a
scripted provider check. No real key, provider, Keychain item or live workspace is touched.

Bryce's 17 September 2026 decision: subscription sign-in stays; an API key is offered next to it. The key is checked
once with the provider, stored (Keychain for Claude, Codex's own auth.json for Codex) and never written anywhere else.
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from host import Host, ACTOR
from dervo_connections import Rejected
from provider_keys import ProviderKeys, check_key
import connections_ui

CLAUDE_KEY = 'sk-ant-api03-fixture-key-not-real-0000000000000000abcd'
OPENAI_KEY = 'sk-proj-fixture-key-not-real-000000000000000000wxyz'


class MemoryKeychain:
    items = {}
    calls = []

    def __init__(self, **kwargs):
        MemoryKeychain.calls.append(kwargs)
        self.slot = (kwargs['project_ref'], kwargs['profile'], kwargs['workspace_id'], kwargs['purpose'])

    def read(self):
        return MemoryKeychain.items.get(self.slot)

    def write(self, data):
        MemoryKeychain.items[self.slot] = data

    def delete(self):
        MemoryKeychain.items.pop(self.slot, None)


class ProviderKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = Host(self.root / 'data'); self.addCleanup(self.host.close)
        MemoryKeychain.items.clear(); MemoryKeychain.calls.clear()
        self.fetches, self.runs, self.status = [], [], 200

        def fetch(url, headers, timeout=8):
            self.fetches.append((url, dict(headers)))
            if isinstance(self.status, Exception):
                raise self.status
            return self.status

        def run(argv, *, input, cwd, env, capture_output, timeout):
            self.runs.append(dict(argv=list(argv), input=input, cwd=cwd, env=dict(env)))
            (Path(cwd) / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'stored-by-codex'}))
            return SimpleNamespace(returncode=0)
        self.host._keys = ProviderKeys(self.host, keychain_factory=MemoryKeychain, run=run, fetch=fetch)

    def everything_saved(self):
        db = self.host.store.db
        rows = []
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            rows += [tuple(r) for r in db.execute(f'SELECT * FROM "{table}"')]
        return json.dumps(rows, default=str)

    def test_a_claude_key_is_checked_once_kept_in_keychain_and_reaches_only_the_process_environment(self):
        added = self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY, 'monthlyLimitUsd': 50})
        self.assertEqual((added['provider'], added['method'], added['identity'], added['category'], added['monthly_limit_usd']),
                         ('claude', 'api_key', 'Anthropic API key ending abcd', 'personal', 50.0))
        self.assertEqual(self.fetches, [('https://api.anthropic.com/v1/models?limit=1', {'x-api-key': CLAUDE_KEY, 'anthropic-version': '2023-06-01'})],
                         'one free models call, to Anthropic only')
        listed = next(p for p in self.host.dispatch('profiles', {}) if p['id'] == added['id'])
        self.assertEqual((listed['method'], listed['monthly_limit_usd'], listed['identity']), ('api_key', 50.0, 'Anthropic API key ending abcd'))
        self.assertEqual(len(MemoryKeychain.items), 1)
        (slot, data), = MemoryKeychain.items.items()
        self.assertEqual(data, CLAUDE_KEY.encode())
        self.assertEqual((slot[0], slot[3]), ('dervoproviderkeys001', 'provider-key'))
        self.assertEqual(json.loads(slot[2]), [str((self.root / 'data').resolve()), added['id']], 'one Keychain item per account and data root')
        self.assertNotIn(CLAUDE_KEY, self.everything_saved(), 'the key is not in any saved row')
        self.assertNotIn(CLAUDE_KEY[8:], json.dumps(added))
        # The key reaches a run's environment for this account only, read from Keychain at that moment.
        self.host.store.add_project('p', self.root, added['id'], 'claude-sonnet-5')
        channel = self.host.dispatch('create_conversation', {'projectId': 'p', 'name': 'Keyed', 'private': False})['id']
        binding = self.host.store.binding(channel, 'p', actor=ACTOR)
        self.assertEqual((binding['method'], binding['monthly_limit_usd']), ('api_key', 50.0))
        self.assertEqual(self.host.keys.key_for(binding), CLAUDE_KEY)
        view = self.host.dispatch('binding', {'projectId': 'p', 'channelId': channel})
        self.assertEqual(view['method'], 'api_key')
        self.assertNotIn(CLAUDE_KEY[8:], json.dumps(view), 'the renderer binding carries the method, never the key')
        from transport import clean_environment
        with_key = clean_environment('claude', binding['home'], binding['home'], api_key=CLAUDE_KEY)
        without = clean_environment('claude', binding['home'], binding['home'])
        self.assertEqual(with_key['ANTHROPIC_API_KEY'], CLAUDE_KEY)
        self.assertNotIn('ANTHROPIC_API_KEY', without)
        self.assertNotIn('ANTHROPIC_API_KEY', clean_environment('codex', binding['home'], binding['home'], api_key=None))
        seen = []
        from claude_policy import auth_status
        def run(argv, **kwargs):
            seen.append((list(argv), kwargs['env']))
            return SimpleNamespace(returncode=0, stdout=b'{"loggedIn": true, "authMethod": "api_key", "apiProvider": "firstParty", "apiKeySource": "ANTHROPIC_API_KEY"}')
        metadata = auth_status('/fixture/claude', binding['home'], run=run, api_key=CLAUDE_KEY)
        self.assertEqual(seen[0][1]['ANTHROPIC_API_KEY'], CLAUDE_KEY)
        self.assertNotIn(CLAUDE_KEY, ' '.join(seen[0][0]), 'never in argv')
        self.assertEqual(connections_ui.classify({**binding, 'identity': binding['identity']}, metadata)['state'], 'identity_verified')
        # A subscription account has no API-key path.
        sub = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@example.test', 'category': 'personal'})['id']
        self.assertIsNone(self.host.keys.key_for({'method': 'subscription', 'provider': 'claude', 'profile_id': sub}))
        for op in ('connection_login', 'connection_login_status', 'connection_instructions'):
            with self.subTest(op=op), self.assertRaisesRegex(Rejected, 'API key'):
                self.host.dispatch(op, {'profileId': added['id']})

    def test_a_codex_key_goes_to_codex_own_store_from_stdin_and_codex_starts_with_the_api_login_method(self):
        added = self.host.dispatch('profile_key_add', {'provider': 'codex', 'key': OPENAI_KEY})
        self.assertEqual((added['identity'], added['method'], added['monthly_limit_usd']), ('OpenAI API key ending wxyz', 'api_key', None))
        self.assertEqual(self.fetches[0][0], 'https://api.openai.com/v1/models')
        self.assertEqual(self.fetches[0][1], {'Authorization': 'Bearer ' + OPENAI_KEY})
        self.assertEqual(len(self.runs), 1)
        run = self.runs[0]
        self.assertEqual(run['argv'][1:], ['-c', 'cli_auth_credentials_store="file"', 'login', '--with-api-key'])
        self.assertNotIn(OPENAI_KEY, ' '.join(run['argv']), 'the key is read from stdin, never an argument')
        self.assertEqual(run['input'], OPENAI_KEY.encode())
        home = self.root / 'data' / 'profiles' / added['id']
        self.assertEqual((run['env']['CODEX_HOME'], run['cwd']), (str(home), str(home)))
        self.assertEqual(stat.S_IMODE(os.stat(home / 'auth.json').st_mode), 0o600)
        self.assertEqual(MemoryKeychain.items, {}, 'Codex keeps the key itself; nothing goes to Keychain')
        self.assertNotIn(OPENAI_KEY, self.everything_saved())
        from codex_policy import argv as chat_argv
        from codex_coding_policy import argv as coding_argv
        self.assertIn('forced_login_method="api"', chat_argv('/fixture/codex', login_method='api'))
        self.assertIn('forced_login_method="chatgpt"', chat_argv('/fixture/codex'))
        self.assertIn('forced_login_method="api"', coding_argv(login_method='api'))
        self.assertNotIn('forced_login_method="chatgpt"', coding_argv(login_method='api'))
        with self.assertRaises(Rejected):
            chat_argv('/fixture/codex', login_method='chatgpt-or-api')
        profile = dict(self.host.store.db.execute('SELECT * FROM profiles WHERE id=?', (added['id'],)).fetchone())
        self.assertEqual(connections_ui.classify(profile, {'account': {'type': 'apiKey'}})['state'], 'identity_verified')
        self.assertEqual(connections_ui.classify(profile, {'account': None})['state'], 'signed_out')
        self.assertEqual(connections_ui.classify(profile, {'account': {'type': 'chatgpt', 'email': profile['identity'], 'planType': 'pro'}})['state'], 'identity_mismatch')
        self.assertIn('API key ready', connections_ui.classify(profile, {'account': {'type': 'apiKey'}})['detail'])

    def test_a_bad_or_rejected_key_saves_nothing_and_is_never_repeated(self):
        before = self.everything_saved()
        for provider, key in (('claude', 'sk-ant-short'), ('claude', OPENAI_KEY), ('codex', 'sk-'), ('claude', ' ' + CLAUDE_KEY),
                              ('claude', CLAUDE_KEY + '\n'), ('claude', 'sk-ant-' + 'k' * 600), ('claude', None), ('openrouter', CLAUDE_KEY)):
            with self.subTest(key=str(key)[:12]), self.assertRaises(Rejected) as caught:
                self.host.dispatch('profile_key_add', {'provider': provider, 'key': key})
            if len(str(key)) >= 20:
                self.assertNotIn(str(key)[8:20], str(caught.exception), 'the refusal never repeats the key')
        self.assertEqual(self.fetches, [], 'a malformed key is never sent anywhere')
        self.status = 401
        with self.assertRaisesRegex(Rejected, 'Anthropic rejected that key'):
            self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY})
        self.status = OSError('offline')
        with self.assertRaisesRegex(Rejected, 'Couldn’t reach Anthropic'):
            self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY})
        self.status = 500
        with self.assertRaisesRegex(Rejected, 'Couldn’t reach OpenAI'):
            self.host.dispatch('profile_key_add', {'provider': 'codex', 'key': OPENAI_KEY})
        with self.assertRaises(Rejected):
            self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY, 'monthlyLimitUsd': -1})
        with self.assertRaises(Rejected):
            self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY, 'extra': True})
        self.assertEqual(self.everything_saved(), before)
        self.assertEqual((MemoryKeychain.items, self.runs), ({}, []))
        self.assertEqual(check_key('claude', CLAUDE_KEY, fetch=lambda url, headers: 403), 'invalid')

    def test_remove_and_spend_limit_apply_to_api_key_accounts_only_and_remove_waits_for_users(self):
        claude = self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY})['id']
        codex = self.host.dispatch('profile_key_add', {'provider': 'codex', 'key': OPENAI_KEY})['id']
        sub = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@example.test', 'category': 'personal'})['id']
        self.assertEqual(self.host.dispatch('profile_limit_set', {'profileId': claude, 'monthlyLimitUsd': 20}), {'id': claude, 'monthly_limit_usd': 20.0})
        self.assertEqual(self.host.dispatch('profile_limit_set', {'profileId': claude, 'monthlyLimitUsd': None})['monthly_limit_usd'], None)
        with self.assertRaisesRegex(Rejected, 'subscription'):
            self.host.dispatch('profile_limit_set', {'profileId': sub, 'monthlyLimitUsd': 20})
        with self.assertRaisesRegex(Rejected, 'Only an API-key account'):
            self.host.dispatch('profile_key_remove', {'profileId': sub})
        self.host.store.add_project('p', self.root, claude, 'claude-sonnet-5')
        with self.assertRaisesRegex(Rejected, '1 project or conversation still use'):
            self.host.dispatch('profile_key_remove', {'profileId': claude})
        self.assertEqual(len(MemoryKeychain.items), 1, 'the key stays while the account is in use')
        self.assertEqual(self.host.dispatch('profile_key_remove', {'profileId': codex}), {'id': codex, 'state': 'removed'})
        self.assertFalse((self.root / 'data' / 'profiles' / codex / 'auth.json').exists())
        self.assertEqual({p['id'] for p in self.host.dispatch('profiles', {})}, {claude, sub})
        with self.host.store.db:
            self.host.store.db.execute('DELETE FROM projects WHERE id=?', ('p',))
        self.host.dispatch('profile_key_remove', {'profileId': claude})
        self.assertEqual(MemoryKeychain.items, {})
        with self.assertRaises(Rejected):
            self.host.dispatch('profile_key_remove', {'profileId': claude})

    def test_older_stores_gain_the_method_column_as_subscription_and_the_adapters_check_by_method(self):
        columns = {row[1]: row for row in self.host.store.db.execute('PRAGMA table_info(profiles)')}
        self.assertIn('method', columns); self.assertIn('monthly_limit_usd', columns)
        sub = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@example.test', 'category': 'personal'})['id']
        row = dict(self.host.store.db.execute('SELECT * FROM profiles WHERE id=?', (sub,)).fetchone())
        self.assertEqual((row['method'], row['monthly_limit_usd']), ('subscription', None))
        key_meta = {'loggedIn': True, 'authMethod': 'api_key', 'apiProvider': 'firstParty', 'apiKeySource': 'ANTHROPIC_API_KEY'}
        sub_meta = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'email': 'me@example.test', 'subscriptionType': 'max'}
        keyed = {**row, 'method': 'api_key'}
        self.assertEqual(connections_ui.classify(row, key_meta)['state'], 'identity_mismatch', 'a subscription account is not verified by a key')
        self.assertEqual(connections_ui.classify(row, sub_meta)['state'], 'identity_verified')
        self.assertEqual(connections_ui.classify(keyed, sub_meta)['state'], 'identity_mismatch', 'an API-key account is not verified by a login')
        self.assertEqual(connections_ui.classify(keyed, key_meta)['state'], 'identity_verified')
        self.assertEqual(connections_ui.classify(keyed, {**key_meta, 'apiKeySource': 'apiKeyHelper'})['state'], 'identity_mismatch')
        from adapters import ClaudeAdapter
        binding = {**row, 'profile_id': sub, 'provider': 'claude', 'model': 'm', 'cwd': '/x', 'id': 't', 'session_id': None}
        adapter = ClaudeAdapter({**binding, 'method': 'api_key'}, lambda *_: None)
        adapter.authenticate(key_meta)
        self.assertEqual(adapter.state, 'ready')
        with self.assertRaisesRegex(Rejected, 'not using this account’s API key'):
            ClaudeAdapter({**binding, 'method': 'api_key'}, lambda *_: None).authenticate(sub_meta)
        with self.assertRaises(Rejected):
            ClaudeAdapter(binding, lambda *_: None).authenticate(key_meta)


    # ---------------------------------------------------------------- Where a key may go (Settings sweep, 19 Sep 2026)
    def launch_claude(self, binding, api_key):
        """Start a coding run far enough to see what the process would get, then stop before it runs."""
        import claude_coding
        from codex_coding import TrustedTaskBinding
        from unittest.mock import patch
        seen = []

        class Launch:
            def __init__(self, argv, **kwargs):
                seen.append((list(argv), dict(kwargs['env'])))
                raise Rejected('stopped at launch for the test')
        key_meta = {'loggedIn': True, 'authMethod': 'api_key', 'apiProvider': 'firstParty', 'apiKeySource': 'ANTHROPIC_API_KEY'}
        sub_meta = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'email': binding['identity'], 'subscriptionType': 'max'}
        with patch.object(claude_coding, 'OwnedProcess', Launch), patch.object(claude_coding, 'executable', lambda: '/fixture/claude'), \
                patch.object(claude_coding, 'auth_status', lambda exe, home, api_key=None, **kwargs: dict(key_meta if api_key else sub_meta)):
            with self.assertRaisesRegex(Rejected, 'stopped at launch'):
                claude_coding.ClaudeCodingTurn(binding, task=TrustedTaskBinding('t', binding, lambda *_: True),
                                               context=lambda: ('', 'r'), emit=lambda *_: None, api_key=api_key)
        return seen[0]

    def binding_for(self, profile_id):
        self.host.store.add_project('p-' + profile_id, self.root, profile_id, 'claude-sonnet-5')
        channel = self.host.dispatch('create_conversation', {'projectId': 'p-' + profile_id, 'name': 'Run', 'private': False})['id']
        return self.host.store.binding(channel, 'p-' + profile_id, actor=ACTOR)

    def test_a_coding_run_gets_the_key_through_its_environment_only_and_a_subscription_run_never_does(self):
        keyed = self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY})['id']
        sub = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@example.test', 'category': 'personal'})['id']
        keyed_binding, sub_binding = self.binding_for(keyed), self.binding_for(sub)
        # What coding_integration hands the run: the key read for that account at that moment, or None.
        argv, env = self.launch_claude(keyed_binding, self.host.keys.key_for(keyed_binding))
        self.assertEqual(env['ANTHROPIC_API_KEY'], CLAUDE_KEY)
        self.assertNotIn(CLAUDE_KEY, ' '.join(argv), 'never in argv')
        self.assertFalse(any(CLAUDE_KEY[8:] in part for part in argv))
        self.assertIsNone(self.host.keys.key_for(sub_binding), 'a subscription account has no key to read')
        argv, env = self.launch_claude(sub_binding, self.host.keys.key_for(sub_binding))
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertFalse(any(CLAUDE_KEY[8:] in value for value in env.values()), 'another account’s key never leaks in')
        # A Codex API-key account keeps its key in Codex's own store; nothing puts it in a Claude run or an environment.
        self.assertIsNone(self.host.keys.key_for({'method': 'api_key', 'provider': 'codex', 'profile_id': keyed}))

    def test_nothing_the_host_prints_or_records_ever_holds_a_key(self):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            keyed = self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY})['id']
            self.host.dispatch('profile_key_add', {'provider': 'codex', 'key': OPENAI_KEY})
            for bad in (CLAUDE_KEY + 'x y', ' ' + CLAUDE_KEY):
                with self.assertRaises(Rejected) as caught:
                    self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': bad})
                self.assertNotIn(CLAUDE_KEY[8:], str(caught.exception))
            self.status = 401
            with self.assertRaises(Rejected) as caught:
                self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': CLAUDE_KEY})
            self.assertNotIn(CLAUDE_KEY[8:], str(caught.exception))
            MemoryKeychain.items.clear()  # the Keychain item went missing: the run's refusal names no key
            with self.assertRaises(Rejected) as caught:
                self.host.keys.key_for(self.binding_for(keyed))
            self.assertNotIn(CLAUDE_KEY[8:], str(caught.exception))
            payloads = [self.host.dispatch('profiles', {}), self.host.dispatch('usage_summary', {}), self.host.dispatch('status', {})]
        for text in (out.getvalue(), err.getvalue(), self.everything_saved(), json.dumps(payloads, default=str)):
            self.assertNotIn(CLAUDE_KEY[8:], text)
            self.assertNotIn(OPENAI_KEY[8:], text)
        rows = [p for p in payloads[0] if p['id'] == keyed]
        self.assertEqual(rows[0]['identity'], 'Anthropic API key ending abcd', 'the renderer sees only the last four characters')


class CodexLoginMethodTests(unittest.TestCase):
    """Codex must echo, at config/read, the login method Dervo launched it with: 'api' for an API-key account, 'chatgpt'
    for a subscription (19 Sep). verify_config used to compare with the chatgpt policy only, so every API-key Codex
    launch was refused with "Codex launch policy was not applied exactly"."""
    MISSING = object()

    def policies(self):
        import codex_policy, codex_coding_policy
        return (('chat', codex_policy.POLICY, codex_policy.verify_config),
                ('coding', codex_coding_policy.POLICY, codex_coding_policy.verify_config))

    def echoed(self, policy, method, effective=None):
        """A config/read answer whose session flags carry ``method`` (or none), and whose effective value is ``effective``
        when given, else the same."""
        import copy
        flags = copy.deepcopy(policy)
        flags.pop('forced_login_method')
        if method is not self.MISSING:
            flags['forced_login_method'] = method
        config = copy.deepcopy(flags)
        if effective is not None:
            config['forced_login_method'] = effective
        return {'config': config, 'layers': [{'name': {'type': 'sessionFlags'}, 'config': flags}]}

    def test_what_each_launch_passes_is_what_its_check_accepts(self):
        import tomllib
        import codex_policy, codex_coding_policy
        for method, binding in (('api', {'method': 'api_key'}), ('chatgpt', {'method': 'subscription'}), ('chatgpt', {})):
            self.assertEqual(codex_policy.login_method(binding), method)
            for name, build, verify in (('chat', lambda m: codex_policy.argv('/fixture/codex', login_method=m), codex_policy.verify_config),
                                        ('coding', lambda m: codex_coding_policy.argv(login_method=m), codex_coding_policy.verify_config)):
                with self.subTest(name=name, binding=binding):
                    args, flags = build(codex_policy.login_method(binding)), {}
                    for i in range(1, len(args) - 4, 2):
                        flags.update(tomllib.loads(args[i + 1]))
                    verify({'config': dict(flags), 'layers': [{'name': {'type': 'sessionFlags'}, 'config': flags}]},
                           codex_policy.login_method(binding))

    def test_an_api_key_launch_passes_on_chat_and_coding(self):
        for name, policy, verify in self.policies():
            with self.subTest(name=name):
                verify(self.echoed(policy, 'api'), 'api')

    def test_a_chatgpt_launch_passes_on_chat_and_coding(self):
        for name, policy, verify in self.policies():
            with self.subTest(name=name):
                verify(self.echoed(policy, 'chatgpt'), 'chatgpt')
                verify(self.echoed(policy, 'chatgpt'))  # a caller that names no method gets the subscription check

    def test_a_crossed_or_unknown_login_method_is_refused(self):
        for name, policy, verify in self.policies():
            for launched in ('chatgpt', 'api'):
                for echo in ('chatgpt', 'api', 'apikey', 'API', '', None, self.MISSING):
                    if echo == launched:
                        continue
                    with self.subTest(name=name, launched=launched, echo=echo), self.assertRaisesRegex(Rejected, 'not applied exactly'):
                        verify(self.echoed(policy, echo), launched)
                other = 'chatgpt' if launched == 'api' else 'api'
                with self.subTest(name=name, launched=launched, effective=other), self.assertRaisesRegex(Rejected, 'differs from the launch policy'):
                    verify(self.echoed(policy, launched, effective=other), launched)
            for unknown in ('apikey', 'API', '', None, 'chatgpt-or-api'):
                with self.subTest(name=name, unknown=unknown), self.assertRaisesRegex(Rejected, 'chatgpt or api'):
                    verify(self.echoed(policy, unknown), unknown)

    def test_an_api_key_codex_account_is_left_out_of_helpers_before_any_launch(self):
        # The vendored harness worker pins forced_login_method "chatgpt" and refuses an apiKey account ("API-key billing").
        # An API-key Codex account never gets that far: it has no account email, so it's excluded with a reason and nothing
        # is launched for it.
        import harness_codex
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        host = Host(Path(temp.name) / 'data'); self.addCleanup(host.close)
        project_dir = Path(temp.name) / 'project'; project_dir.mkdir()
        host.store.add_profile('keyed', 'codex', Path(host.root).resolve() / 'profiles' / 'keyed', 'OpenAI API key ending wxyz',
                               'personal', method='api_key', monthly_limit_usd=None)
        project = host.add_picked_project(str(project_dir), 'keyed', 'gpt-5.5')['id']
        host.dispatch('create_conversation', {'projectId': project, 'name': 'Planning', 'private': False})
        launched = []
        helpers, excluded = harness_codex.derive(host.store, host.root, executable=lambda *a: launched.append(a))
        self.assertEqual(helpers, {})
        self.assertEqual([(row['profile'], row['model']) for row in excluded], [('keyed', 'gpt-5.5')])
        self.assertIn('not an account email', excluded[0]['reason'])
        self.assertEqual(launched, [])

    def test_each_adapter_checks_against_its_own_accounts_launch(self):
        import codex_policy, codex_coding_policy
        from adapters import CodexAdapter
        from codex_coding import CodexCodingAdapter, TrustedTaskBinding
        from test_codex_coding import BINDING, INIT
        keyed = {**BINDING, 'method': 'api_key', 'identity': 'OpenAI API key ending wxyz'}
        def started(binding, coding):
            sent = []
            adapter = (CodexCodingAdapter(binding, sent.append, lambda *_: None, task=TrustedTaskBinding('t', binding, lambda *_: True))
                       if coding else CodexAdapter(binding, sent.append, lambda *_: None))
            adapter.connect()
            adapter.receive({'id': sent[-1]['id'], 'result': INIT})
            return adapter, sent
        for coding, policy in ((False, codex_policy.POLICY), (True, codex_coding_policy.POLICY)):
            policy = codex_policy.for_folder(policy, BINDING['cwd'])  # the launch marks the binding's folder untrusted
            for binding, method, crossed in ((keyed, 'api', 'chatgpt'), (BINDING, 'chatgpt', 'api')):
                with self.subTest(coding=coding, method=method):
                    adapter, sent = started(binding, coding)
                    adapter.receive({'id': sent[-1]['id'], 'result': self.echoed(policy, method)})
                    self.assertEqual(sent[-1]['method'], 'account/read', 'the launch policy checked out')
                    adapter, sent = started(binding, coding)
                    with self.assertRaises(Rejected):
                        adapter.receive({'id': sent[-1]['id'], 'result': self.echoed(policy, crossed)})
                    self.assertNotEqual(sent[-1]['method'], 'account/read')


if __name__ == '__main__':
    unittest.main()
