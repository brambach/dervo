"""Offline metadata tests. All identities and protocol responses are test doubles."""
import unittest
import connections_ui as c

class MetadataTests(unittest.TestCase):
    def test_codex_probe_only_requests_identity_never_a_thread(self):
        from unittest.mock import patch
        import tempfile
        self.assertTrue(hasattr(c, 'probe'), 'Real metadata preflight is missing')
        sent = []
        class ProtocolDouble:
            def __init__(self, argv, **kwargs):
                self.argv, self.kwargs = argv, kwargs
                self.batches = iter([[{'id':1,'result':{}}], [{'id':2,'result':{'account':None}}]])
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def send(self, message): sent.append(message)
            def read(self, timeout): return next(self.batches)
        with tempfile.TemporaryDirectory() as home, patch('transport.OwnedProcess', ProtocolDouble):
            result = c.probe({'provider':'codex','identity':'personal@example.test','category':'personal','home':home})
        self.assertEqual(result['state'], 'signed_out')
        self.assertEqual([m['method'] for m in sent], ['initialize','initialized','account/read'])
        self.assertEqual(sent[-1]['params'], {'refreshToken':False})

    def test_codex_metadata_uses_neutral_cwd_not_profile_ancestors(self):
        from unittest.mock import patch
        from pathlib import Path
        import tempfile
        import json
        seen = []
        class ProtocolDouble:
            def __init__(self, argv, **kwargs):
                seen.append(kwargs['cwd'])
                self.batches = iter([[{'id':1,'result':{}}], [{'id':2,'result':{'account':None}}]])
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def send(self, message): pass
            def read(self, timeout): return next(self.batches)
        with tempfile.TemporaryDirectory() as root:
            parent=Path(root); (parent/'.codex').mkdir(); (parent/'.codex/config.toml').write_text('# Test double ancestor config')
            home=parent/'profile'; home.mkdir()
            with patch('transport.OwnedProcess', ProtocolDouble):
                result=c.probe({'provider':'codex','home':str(home),'identity':'personal@example.test','category':'personal'})
            self.assertEqual(result['state'], 'signed_out')
            self.assertEqual(len(seen),1)
            self.assertNotEqual(seen[0],str(home))

    def test_codex_trust_in_the_profile_is_checked_but_other_config_is_not(self):
        from unittest.mock import patch
        from pathlib import Path
        import tempfile
        from test_codex_policy import TRUST
        launched = []
        class ProtocolDouble:
            def __init__(self, argv, **kwargs):
                launched.append(argv)
                self.batches = iter([[{'id':1,'result':{}}], [{'id':2,'result':{'account':None}}]])
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def send(self, message): pass
            def read(self, timeout): return next(self.batches)
        profile = {'provider':'codex','identity':'personal@example.test','category':'personal'}
        with tempfile.TemporaryDirectory() as home, patch('transport.OwnedProcess', ProtocolDouble):
            (Path(home)/'config.toml').write_text(TRUST)
            self.assertEqual(c.probe({**profile, 'home':home})['state'], 'signed_out')
            self.assertEqual(len(launched), 1)
            for text in ('model = "gpt-5.5"\n' + TRUST, '# a comment only\n'):
                (Path(home)/'config.toml').write_text(text)
                self.assertEqual(c.probe({**profile, 'home':home})['state'], 'config_blocked')
            (Path(home)/'config.toml').write_text(TRUST)
            (Path(home)/'.codex').mkdir(); (Path(home)/'.codex'/'config.toml').write_text(TRUST)
            self.assertEqual(c.probe({**profile, 'home':home})['state'], 'config_blocked')
        self.assertEqual(len(launched), 1)

    def test_identity_is_exact_and_not_send_permission(self):
        self.assertTrue(hasattr(c, 'classify'), 'Provider metadata classifier is missing')
        p = {'provider':'codex', 'identity':'personal@example.test', 'category':'personal'}
        good = {'account':{'type':'chatgpt','email':p['identity'],'planType':'plus'}}
        self.assertEqual(c.classify(p, good)['state'], 'identity_verified')
        self.assertFalse(c.classify(p, good)['canSend'])
        self.assertEqual(c.classify(p, {'account':None})['state'], 'signed_out')
        self.assertEqual(c.classify(p, {})['state'], 'unknown')
        self.assertEqual(c.classify(p, {'account':{**good['account'],'email':'work@example.test'}})['state'], 'identity_mismatch')
        self.assertEqual(c.classify(p, {'account':{**good['account'],'planType':'business'}})['state'], 'identity_mismatch')
        p['provider'] = 'claude'
        self.assertEqual(c.classify(p, {'loggedIn':False})['state'], 'signed_out')
        self.assertEqual(c.classify(p, {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','email':p['identity'],'subscriptionType':'max'})['state'], 'identity_verified')

    def test_identity_verified_detail_keeps_model_and_permissions_separate(self):
        for provider in ('codex', 'claude'):
            p = {'provider':provider, 'identity':'personal@example.test', 'category':'personal'}
            good = ({'account':{'type':'chatgpt','email':p['identity'],'planType':'pro'}} if provider == 'codex'
                    else {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','email':p['identity'],'subscriptionType':'pro'})
            result = c.classify(p, good)
            self.assertEqual(result['state'], 'identity_verified')
            self.assertFalse(result['canSend'], 'identity alone never permits a send')
            self.assertIn('checks the model and project permissions before each request', result['detail'])
        # Work accounts run since 26 Sep 2026 (Bryce: "make sure both sides work"): verified only as their own work email,
        # and the store refuses one email on both sides (test_work_accounts), so a personal sign-in never verifies here.
        work = {'provider':'claude', 'identity':'you@acme.test', 'category':'work'}
        signed = {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','subscriptionType':'team'}
        self.assertEqual(c.classify(work, {**signed, 'email':work['identity']})['state'], 'identity_verified')
        self.assertEqual(c.classify(work, {**signed, 'email':'personal@example.test'})['state'], 'identity_mismatch')

    def test_a_mismatch_says_whether_the_email_or_the_plan_failed_and_capitals_dont_count(self):
        # 16.2 (Shane, 27 Sep): "Check it's the account you meant" failed with no reason. The plan lists are unchanged.
        p = {'provider':'codex', 'identity':' Shane@Example.test', 'category':'personal'}
        signed = lambda **account: c.classify(p, {'account':{'type':'chatgpt','email':'shane@example.test','planType':'plus', **account}})
        self.assertEqual(signed()['state'], 'identity_verified', 'capitals and spaces in the typed email do not count')
        other = signed(email='someone@example.test')
        self.assertEqual(other['state'], 'identity_mismatch')
        self.assertEqual(other['detail'], 'Codex is signed in with a different email, not Shane@Example.test. Sign out of '
                                          'chatgpt.com in your browser, then sign in as Shane@Example.test. No messages were sent.')
        self.assertNotIn('someone@', other['detail'], 'an email you never added here is never repeated')
        self.assertEqual(other['seen'], 'someone@example.test')
        for plan in ('free', 'go', 'team', 'business'):
            result = signed(planType=plan)
            self.assertEqual(result['state'], 'identity_mismatch', plan)
            self.assertIn(f'is on the ChatGPT {c.PLAN_NAMES[plan]} plan. A personal account needs Plus, Pro Lite or Pro.', result['detail'])
        self.assertIn('didn’t say which ChatGPT plan', signed(planType=None)['detail'])
        # Josh, feedback #9 (27 Sep): the $100 Pro plan reads "prolite". It verifies, and a plan name Dervo doesn't know
        # yet still fails with the name Codex sent, so the next one shows up in a report the same way.
        self.assertEqual(signed(planType='prolite')['state'], 'identity_verified')
        self.assertEqual(signed(planType='ent26')['detail'], 'Shane@Example.test is on the ChatGPT ent26 plan. A personal '
                                                           'account needs Plus, Pro Lite or Pro. No messages were sent.')
        work = {'provider':'claude', 'identity':'you@acme.test', 'category':'work'}
        claude = c.classify(work, {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','email':'YOU@acme.test','subscriptionType':'free'})
        self.assertEqual(claude['detail'], 'you@acme.test is on the Claude Free plan. A work account needs Pro, Max, Team or Enterprise. No messages were sent.')
        # Only the email's case and spaces are loosened: another address, or an API-key account, never matches.
        self.assertFalse(c.same_email('shane@example.test.evil', 'shane@example.test'))
        self.assertFalse(c.same_email('', ''))
        keyed = c.classify({**p, 'method':'api_key'}, {'account':{'type':'chatgpt','email':'shane@example.test','planType':'plus'}})
        self.assertEqual(keyed['detail'], 'Codex is using a different sign-in than this account’s API key. Remove this account and add the key again.')

if __name__ == '__main__': unittest.main()
