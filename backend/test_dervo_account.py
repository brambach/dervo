"""Synthetic Dervo identity adapter. No credentials, browser or real account operations."""
import json
import tempfile
import unittest
from pathlib import Path

from dervo_account import AccountSession
from dervo_connections import Rejected
from host import Host

IDENTITY = dict(issuer='synthetic.dervo.test', subject='person-one', display_name='Synthetic User')


class Adapter:
    def __init__(self):
        self.calls=[]
        self.next_state='awaiting_user'
        self.identity=dict(IDENTITY)
        self.expiry=2000
        self.error=False
    def response(self, op):
        if self.error:raise RuntimeError('synthetic-sensitive-marker')
        answer={'operation_id':op,'state':self.next_state}
        if self.next_state=='signed_in':answer.update(identity=dict(self.identity),expires_at=self.expiry)
        return answer
    def perform(self, kind, op, previous):
        self.calls.append((kind,op,previous));return self.response(op)
    def poll(self, op):
        self.calls.append(('poll',op));return self.response(op)


class DervoAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.adapter=Adapter();self.now=1000
        self.host=Host(self.root/'store',dervo_account_adapter=self.adapter)
        self.addCleanup(lambda:self.host.close())
        self.account=AccountSession(self.host.store,self.adapter,lambda:self.now)
        self.host.account_session=self.account
        project=self.root/'project';project.mkdir()
        self.host.store.add_profile('provider','codex',self.root/'profile','fixture@example.test','personal')
        self.host.store.add_project('project',project,'provider','synthetic-model')
        self.host.store.add_thread('thread','project','channel','private','local-user')
        self.provider_rows=self.rows('profiles'),self.rows('projects'),self.rows('threads')

    def rows(self,table):return [tuple(r) for r in self.host.store.db.execute('SELECT * FROM '+table)]
    def call(self,op,**args):return self.host.dispatch('dervo_account_'+op,args)
    def signin(self):
        self.adapter.next_state='signed_in';return self.call('start',mode='sign_in')
    def tearDown(self):
        self.assertEqual((self.rows('profiles'),self.rows('projects'),self.rows('threads')),self.provider_rows)

    def test_default_host_is_unavailable_and_creates_no_account_state(self):
        # Where the saved-credentials store can't be set up, whichever system runs the test. A Linux runner with a
        # Secret Service would otherwise build the real account from the checkout's config (PR #25 review, 9 Oct).
        import account_config
        from unittest.mock import patch
        def broken(**_):raise RuntimeError('no credential store')
        with tempfile.TemporaryDirectory() as root, patch.object(account_config,'MacKeychain',broken), \
                patch.object(account_config,'LinuxSecretService',broken), patch.object(account_config,'WindowsCredentials',broken):
            host=Host(root)
            try:
                state=host.dispatch('dervo_account_status',{})
                self.assertEqual(state['state'],'unavailable');self.assertIsNone(state['identity'])
                self.assertFalse(state['can_start']);self.assertFalse(state['can_sign_out'])
                with self.assertRaises(Rejected):host.dispatch('dervo_account_start',{'mode':'sign_up'})
                self.assertIsNone(host.store.db.execute("SELECT name FROM sqlite_master WHERE name='dervo_account_state'").fetchone())
            finally:host.close()

    def test_signup_pending_is_not_authenticated_and_tick_progresses_when_panel_is_closed(self):
        state=self.call('start',mode='sign_up');op=state['operation_id']
        self.assertEqual(state['state'],'awaiting_user');self.assertIsNone(state['identity'])
        with self.assertRaises(Rejected):self.account.require_identity(IDENTITY['issuer'],IDENTITY['subject'])
        with self.assertRaises(Rejected):self.call('start',mode='sign_in')
        self.adapter.next_state='signed_in';self.host.tick()
        state=self.call('status');self.assertEqual(state['identity'],IDENTITY)
        self.assertEqual(self.adapter.calls[-1],('poll',op))
        self.assertEqual(len(self.account.require_identity(IDENTITY['issuer'],IDENTITY['subject'])),64)
        self.assertEqual(state['ownership'],'local_unclaimed')

    def test_cancel_rejects_late_success_and_stale_renderer_poll(self):
        first=self.call('start',mode='sign_in')['operation_id']
        self.adapter.next_state='cancelled';state=self.call('cancel',operationId=first)
        self.assertEqual(state['state'],'cancelled');self.assertTrue(state['can_start'])
        self.adapter.next_state='signed_in'
        self.account._result(first,self.adapter.response(first))
        self.assertEqual(self.call('status')['state'],'cancelled')
        with self.assertRaises(Rejected):self.call('poll',operationId=first)
        self.adapter.next_state='awaiting_user';second=self.call('start',mode='sign_in')['operation_id']
        self.assertNotEqual(first,second)
        self.account._result(first,dict(operation_id=first,state='signed_in',identity=IDENTITY,expires_at=2000))
        self.assertEqual(self.call('status')['operation_id'],second)

    def test_restart_requires_verified_restoration_not_saved_identity(self):
        self.signin()
        held=self.rows('dervo_account_state');self.assertNotIn('expires_at',held[0][1])
        restored=AccountSession(self.host.store,self.adapter,lambda:self.now)
        self.assertEqual(restored.status()['state'],'restore_required');self.assertIsNone(restored.status()['identity'])
        with self.assertRaises(Rejected):restored.require_identity(IDENTITY['issuer'],IDENTITY['subject'])
        self.host.account_session=restored
        self.assertEqual(self.call('restore')['state'],'signed_in')

    def test_crash_pending_reconciles_and_never_replays_start(self):
        pending=self.call('start',mode='sign_up')['operation_id'];count=len(self.adapter.calls)
        restored=AccountSession(self.host.store,self.adapter,lambda:self.now);self.host.account_session=restored
        self.assertEqual(restored.status()['state'],'interrupted');self.assertEqual(len(self.adapter.calls),count)
        with self.assertRaises(Rejected):self.call('start',mode='sign_up')
        self.adapter.next_state='signed_out';self.assertEqual(self.call('restore')['state'],'signed_out')
        self.assertEqual(self.adapter.calls[-1][0],'restore');self.assertEqual(self.adapter.calls[-1][2],pending)

    def test_expiry_denies_identity_without_signing_out_providers(self):
        self.signin();self.now=2001
        self.assertEqual(self.call('status')['state'],'expired')
        with self.assertRaises(Rejected):self.account.require_identity(IDENTITY['issuer'],IDENTITY['subject'])
        self.assertEqual([c[0] for c in self.adapter.calls],['sign_in'])

    def test_signout_uses_only_dervo_adapter_and_pending_does_not_claim_success(self):
        self.signin();self.adapter.next_state='verifying';state=self.call('sign_out')
        self.assertEqual(state['state'],'verifying');self.assertIsNone(state['identity'])
        self.assertEqual(self.adapter.calls[-1][0],'sign_out')
        self.adapter.next_state='signed_out';self.host.tick()
        self.assertEqual(self.call('status')['state'],'signed_out')
        self.assertFalse(self.host.logins)

    def test_unconfirmed_signout_cannot_be_retried_or_leak_adapter_diagnostics(self):
        self.signin();self.adapter.error=True;state=self.call('sign_out')
        self.assertEqual(state['state'],'unknown');self.assertIsNone(state['identity'])
        self.assertNotIn('synthetic-sensitive-marker',json.dumps(state))
        with self.assertRaises(Rejected):self.call('sign_out')
        with self.assertRaises(Rejected):self.call('start',mode='sign_in')
        self.assertNotIn('synthetic-sensitive-marker',self.rows('dervo_account_state')[0][1])
        self.adapter.error=False;self.adapter.next_state='signed_out';self.assertEqual(self.call('restore')['state'],'signed_out')

    def test_wrong_subject_never_transfers_local_work_or_activates(self):
        self.signin();self.adapter.next_state='signed_out';self.call('sign_out')
        self.adapter.next_state='signed_in';self.adapter.identity['subject']='person-two'
        result=self.call('start',mode='sign_in');self.assertEqual(result['state'],'account_mismatch');self.assertIsNone(result['identity'])
        saved=json.loads(self.rows('dervo_account_state')[0][1]);self.assertEqual(saved['last_identity'],IDENTITY)
        with self.assertRaises(Rejected):self.account.require_identity(IDENTITY['issuer'],'person-two')

    def test_strict_adapter_and_renderer_boundaries_keep_extra_fields_out_of_storage(self):
        for extra in [{'identity':IDENTITY},{'token':'synthetic-sensitive-marker'},{'adapter':'fake'}]:
            with self.assertRaises(Rejected):self.call('status',**extra)
        state=self.call('start',mode='sign_in');op=state['operation_id']
        result=dict(operation_id=op,state='signed_in',identity=IDENTITY,expires_at=2000,token='synthetic-sensitive-marker')
        self.account._result(op,result)
        self.assertEqual(self.call('status')['state'],'unknown')
        self.assertNotIn('synthetic-sensitive-marker',self.rows('dervo_account_state')[0][1])

    def test_failed_signout_and_restore_require_reconciliation_not_new_login(self):
        self.signin();self.adapter.next_state='failed'
        state=self.call('sign_out');self.assertEqual(state['state'],'unknown');self.assertFalse(state['can_start'])
        state=self.call('restore');self.assertEqual(state['state'],'unknown');self.assertFalse(state['can_start'])
        self.assertIsNone(state['expires_at'])

    def test_expiry_is_public_metadata_but_never_a_persisted_authentication_assertion(self):
        state=self.signin();self.assertEqual(state['expires_at'],2000)
        self.assertNotIn('expires_at',self.rows('dervo_account_state')[0][1])
        self.now=2000;state=self.call('status');self.assertEqual(state['state'],'expired');self.assertIsNone(state['expires_at'])
