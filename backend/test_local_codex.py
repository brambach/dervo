"""Pinned local executable regressions. No live model requests."""
import json
from pathlib import Path
import shlex
import sys
import unittest
from adapters import CodexAdapter
from connections_ui import instructions
from codex_policy import argv
from dervo_connections import Rejected

ROOT=Path(__file__).resolve().parents[1]
BINARY=str(ROOT/('runtime/codex-linux/bin/codex' if sys.platform.startswith('linux') else 'runtime/codex'))
BINDING={'provider':'codex','home':'/private/tmp/synthetic-owned','identity':'synthetic@example.test','category':'personal','cwd':'/private/tmp'}

class LocalCodexTests(unittest.TestCase):
    def test_new_execution_features_are_disabled_in_effective_policy(self):
        from codex_policy import POLICY, verify_config
        from copy import deepcopy
        keys=('code_mode_prewarm','unified_exec_tty','shell_snapshot_v2','in_app_local_automation','worktrees','sleep_tool','step_model_switching')
        for key in keys:
            self.assertIs(POLICY['features'].get(key),False,key)
        self.assertIs(POLICY['features'].get('skip_host_skill_discovery'),True)
        config={'layers':[{'name':{'type':'sessionFlags'},'config':deepcopy(POLICY)}], 'config':deepcopy(POLICY)}
        verify_config(config)
        for key in keys:
            changed=deepcopy(config); changed['config']['features'][key]=True
            with self.assertRaises(Rejected):verify_config(changed)

    def test_skills_and_goals_stay_out_of_the_model_instructions(self):
        # 19 Sep: skip_host_skill_discovery was inert in app-server 0.154.0 and skills/list showed a repo's
        # .agents/skills entry plus six bundled skills with Dervo's exact policy. Goals were on and unrecorded. Still inert in
        # 0.156.1 (25 Sep): skills/list shows a repo skill, and these keys keep it out of prompt-input.
        from codex_policy import POLICY, verify_config
        from copy import deepcopy
        self.assertIs(POLICY['features'].get('goals'), False)
        self.assertEqual(POLICY['skills'], {'include_instructions': False, 'bundled': {'enabled': False}})
        config={'layers':[{'name':{'type':'sessionFlags'},'config':deepcopy(POLICY)}], 'config':deepcopy(POLICY)}
        verify_config(config)
        for path in (('include_instructions',), ('bundled','enabled')):
            changed=deepcopy(config)
            target=changed['config']['skills']
            for key in path[:-1]: target=target[key]
            target[path[-1]]=True
            with self.assertRaises(Rejected): verify_config(changed)
        dropped=deepcopy(config); del dropped['config']['skills']
        with self.assertRaises(Rejected): verify_config(dropped)

    def test_identity_probe_uses_same_restricted_launch(self):
        from unittest.mock import patch
        import tempfile
        import connections_ui
        seen=[]
        class Peer:
            def __init__(self,args,**kwargs):seen.append(args)
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def send(self,message):pass
            def read(self,timeout):return None
        with tempfile.TemporaryDirectory() as home, patch('transport.OwnedProcess',Peer):
            connections_ui.probe({**BINDING,'home':home})
        self.assertEqual(seen,[argv()])

    def test_launch_and_official_login_use_pinned_local_binary(self):
        self.assertEqual(argv()[0],BINARY)
        self.assertIn(BINARY,shlex.split(instructions(BINDING)['command']))
        self.assertNotIn('/usr/local/bin/codex',instructions(BINDING)['command'])

    def signed_in(self, *announcements, before_account_read=()):
        """A catalog check up to model/list. ``announcements`` arrive while account/read is pending, the way Codex 0.156.1
        sends account/updated on a signed-in profile (25 Sep); ``before_account_read`` arrive before it is asked."""
        from codex_policy import VERSION
        from test_codex_policy import config_read
        sent=[]
        adapter=CodexAdapter(BINDING,sent.append,lambda *_:None,catalog_only=True)
        adapter.connect()
        adapter.receive({'id':1,'result':{'userAgent':'dervo/'+VERSION+' (synthetic peer)','codexHome':BINDING['home']}})
        for params in before_account_read:
            adapter.receive({'method':'account/updated','params':params})
        adapter.receive({'id':2,'result':config_read(argv('/fixture/codex',cwd=BINDING['cwd']))})
        self.assertEqual(sent[-1]['method'],'account/read')
        for params in announcements:
            adapter.receive({'method':'account/updated','params':params})
        adapter.receive({'id':3,'result':{'account':{'type':'chatgpt','email':BINDING['identity'],'planType':'pro'}}})
        self.assertEqual(sent[-1]['method'],'model/list')
        return adapter

    def test_the_sign_in_announcement_during_account_read_is_accepted_once(self):
        adapter=self.signed_in({'authMode':'chatgpt','planType':'pro'})
        with self.assertRaisesRegex(Rejected,'Account changed'):
            adapter.receive({'method':'account/updated','params':{'authMode':'chatgpt','planType':'pro'}})
        self.assertEqual(adapter.state,'blocked')

    def test_any_other_account_update_still_stops(self):
        chatgpt={'authMode':'chatgpt','planType':'pro'}
        for case in ({'announcements':({'authMode':'apikey','planType':None},)},
                     {'announcements':({'authMode':None,'planType':None},)},
                     {'announcements':(chatgpt,chatgpt)},
                     {'before_account_read':(chatgpt,)}):
            with self.subTest(**case), self.assertRaisesRegex(Rejected,'Account changed'):
                self.signed_in(*case.get('announcements',()),before_account_read=case.get('before_account_read',()))

    def test_only_verified_new_version_advances_to_config(self):
        for version in ('0.154.0','0.156.1','0.156.2'):
            sent=[]
            adapter=CodexAdapter(BINDING,sent.append,lambda *_:None,catalog_only=True)
            adapter.connect()
            response={'id':1,'result':{'userAgent':'dervo/'+version+' (synthetic peer)','codexHome':BINDING['home']}}
            if version=='0.156.1':
                adapter.receive(response)
                self.assertEqual(sent[-1]['method'],'config/read')
            else:
                with self.assertRaises(Rejected):adapter.receive(response)
                self.assertEqual(len(sent),1)
