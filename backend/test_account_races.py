"""Offline regressions for cancellation and beta access at asynchronous boundaries."""
import sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
from supabase_account import SupabaseAccount
from supabase_client import Session
from dervo_connections import Rejected
import test_codex_live

USER='11111111-1111-4111-8111-111111111111'
class Vault:
 def __init__(self):self.value=None
 def load(self):return self.value
 def save(self,s):self.value=s
 def delete(self):self.value=None
class Client:
 project=SimpleNamespace(url='https://synthetic.supabase.co')
 def send_code(self,email):pass
 def verify_code(self,email,code):return Session('synthetic-access','synthetic-refresh',USER,10000)
 def user(self,token,expected_user):return dict(issuer=self.project.url+'/auth/v1',subject=expected_user)
 def access(self,s):return dict(status='approved',allowed=True,expires_at=None,release_channel='private-beta',is_admin=False)
 def profile(self,s):return dict(display_name='Synthetic')
 def sign_out(self,s):pass
class Gate:
 allowed=True
 def require_access(self):
  if not self.allowed:raise Rejected('Synthetic beta grant revoked')
 def tick(self):pass
 def close(self):pass
class TrialGate(Gate):
 # Signed in still, but the trial or plan ended: refuses new work, not a run under way.
 def plan_lapsed(self):return not self.allowed

class AccountRaceTests(unittest.TestCase):
 def test_cancel_completed_but_unconsumed_verification(self):
  db=sqlite3.connect(':memory:');vault=Vault();a=SupabaseAccount(SimpleNamespace(db=db),Client(),vault,clock=lambda:1000)
  try:
   a.future.result(timeout=2);a.tick()
   a.dispatch('dervo_account_send_code',{'email':'synthetic@example.test'});a.future.result(timeout=2);a.tick()
   result=a.dispatch('dervo_account_verify_code',{'operationId':a.operation['id'],'code':'123456'})
   verify_id=result['operation_id'];a.future.result(timeout=2) # completed worker, still a current pending UI operation
   error=None
   try:a.dispatch('dervo_account_cancel',{'operationId':verify_id})
   except Rejected as exc:error=str(exc)
   if a.future is not None:a.future.result(timeout=2);a.tick()
   public=a.status()
   self.assertIsNone(error)
   self.assertFalse(public['membership'] and public['membership']['allowed'])
   self.assertIsNone(vault.value)
  finally:a.close();a.worker.shutdown(wait=True);db.close()
 def test_beta_revoked_during_ordinary_chat_preflight(self):
  fixture=test_codex_live.HostLiveTests('test_explicit_send_stream_persistence_and_no_auto_send')
  with patch('account_config.create',return_value=None):fixture.setUp()
  gate=Gate();fixture.host.account_session=gate
  try:
   mid=fixture.queue();fixture.host.dispatch('start_turn',{**fixture.scope,'messageId':mid})
   gate.allowed=False
   fixture.settle()
   sent=sum(m.get('method')=='turn/start' for m in fixture.peers[0].sent)
   self.assertEqual(sent,0)
  finally:fixture.tearDown()



class HarnessAccountRaceTests(unittest.TestCase):
    def test_revocation_after_start_blocks_both_provider_prompts(self):
        import test_harness_codex as codex
        import test_harness_claude as claude
        from harness_adapter import GuidedPort
        for provider, module in [('codex', codex), ('claude', claude)]:
            with self.subTest(provider=provider):
                cls = module.CodexHelperTests if provider == 'codex' else module.ClaudeHelperTests
                fixture = cls('runTest')
                with patch('account_config.create', return_value=None):
                    fixture.setUp()
                gate = Gate()
                fixture.host.account_session = gate
                try:
                    scope, profile, home = fixture.seed()
                    name = __import__('harness_' + provider).helper_name(profile, module.MODEL)
                    original = GuidedPort.start
                    def revoke_after_start(port, *args):
                        ref = original(port, *args)
                        gate.allowed = False
                        return ref
                    with patch.object(GuidedPort, 'start', revoke_after_start):
                        request = fixture.ask(scope, 'revocation-fixture', 'Synthetic private request.', name)
                        state = fixture.settle(scope, request['id'], timeout=5)
                    if provider == 'codex':
                        sent = 'turn/start' in fixture.methods(home)
                    else:
                        sent = any(row.get('type') == 'user' for row in fixture.received(home))
                    self.assertFalse(sent)
                    self.assertEqual(state['state'], 'cancelled')
                    self.assertIsNone(state['linked_return'])
                finally:
                    fixture.doCleanups()

    def test_unconfirmed_stop_retains_scope_and_pumps_no_more_work(self):
        import test_harness_host as fixtures
        from harness_adapter import GuidedPort
        fixture = fixtures.HarnessHostTests('runTest')
        with patch('account_config.create', return_value=None):
            fixture.setUp()
        gate = Gate()
        fixture.host.account_session = gate
        try:
            scope = fixture.seed()
            port = fixture.helpers['dates'].port
            original_cancel, original_start = port.cancel, GuidedPort.start
            allow_exit = False
            stop_calls = []
            def unconfirmed_cancel(ref):
                stop_calls.append(ref)
                return original_cancel(ref) if allow_exit else None
            def revoke_after_start(wrapper, *args):
                ref = original_start(wrapper, *args)
                gate.allowed = False
                return ref
            with patch.object(port, 'cancel', unconfirmed_cancel), patch.object(port, 'drain', wraps=port.drain) as drain:
                with patch.object(GuidedPort, 'start', revoke_after_start):
                    request = fixture.ask(scope, 'unconfirmed-stop', 'What date is next Friday?', 'dates')
                    pending = fixture.settle(scope, request['id'], states=('stopping',))
                self.assertEqual(pending['state'], 'stopping')
                self.assertIsNone(pending['linked_return'])
                self.assertTrue(stop_calls)
                self.assertEqual(drain.call_count, 0)
                gate.allowed = True
                with self.assertRaises(Rejected):
                    fixture.ask(scope, 'no-overlap', 'What date is next Friday?', 'dates')
                gate.allowed = False
                allow_exit = True
                stopped = fixture.settle(scope, request['id'])
                self.assertEqual(stopped['state'], 'cancelled')
                self.assertIsNone(stopped['linked_return'])
                self.assertEqual(drain.call_count, 0)
            gate.allowed = True
            next_request = fixture.ask(scope, 'after-confirmed-stop', 'What date is next Friday?', 'dates')
            self.assertEqual(fixture.settle(scope, next_request['id'])['state'], 'completed')
        finally:
            fixture.doCleanups()

    def test_a_trial_ending_mid_run_lets_that_run_finish(self):
        import test_harness_host as fixtures
        from harness_adapter import GuidedPort
        fixture = fixtures.HarnessHostTests('runTest')
        with patch('account_config.create', return_value=None):
            fixture.setUp()
        gate = TrialGate()
        fixture.host.account_session = gate
        fixture.host.require_beta_access = gate.require_access  # the gate itself, however the runner sets the account up
        try:
            scope = fixture.seed()
            original_start = GuidedPort.start
            def trial_ends_after_start(wrapper, *args):
                ref = original_start(wrapper, *args)
                gate.allowed = False
                return ref
            with patch.object(GuidedPort, 'start', trial_ends_after_start):
                request = fixture.ask(scope, 'trial-ends', 'What date is next Friday?', 'dates')
                self.assertEqual(fixture.settle(scope, request['id'])['state'], 'completed')
            with self.assertRaises(Rejected):
                fixture.ask(scope, 'after-trial', 'What date is next Friday?', 'dates')
        finally:
            fixture.doCleanups()

if __name__ == '__main__':
    unittest.main(verbosity=2)
