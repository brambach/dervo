"""Disposable CLI/PTY peer. No authentication, credentials or network."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from concurrent.futures import Future
import codex_login as m
from dervo_connections import Rejected
IDENTITY='personal@example.test'
FAKE=r'''#!/usr/local/bin/python3 -B
import os,json,sys,time
from pathlib import Path
c=Path(__CONTROL__)
if 'app-server' in sys.argv:
 for line in sys.stdin:
  msg=json.loads(line)
  with (c/'requests').open('a') as f:f.write(line)
  if msg.get('id')==1:print(json.dumps({'id':1,'result':{}}),flush=True)
  if msg.get('id')==2:print(json.dumps({'id':2,'result':json.loads((c/'identity').read_text())}),flush=True)
else:
 if (c/'descendant').exists() and os.fork()==0:
  import signal
  signal.signal(signal.SIGTERM,signal.SIG_IGN)
  (c/'child').write_text(str(os.getpid()))
  while True:
   (c/'heartbeat').write_text(str(time.monotonic()))
   time.sleep(.02)
 (c/'launch').write_text(json.dumps({'argv':sys.argv[1:],'env':dict(os.environ),'cwd':os.getcwd()}))
 for text in ['Opening brow','ser\n','https://auth.openai.com/a?code=DUMMY-CODE\n','DUMMY-TOKEN'*10000]:print(text,flush=True)
 while not (c/'finish').exists() and not (c/'fail').exists():time.sleep(.01)
 print('DUMMY-TOKEN error',flush=True)
 sys.exit(1 if (c/'fail').exists() else 0)
'''
class Fixture(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory(prefix='dervo-codex-test-');self.addCleanup(tmp.cleanup)
  self.base=Path(tmp.name).resolve();self.control=self.base/'control';self.control.mkdir()
  self.exe=self.base/'synthetic-login-peer';self.exe.write_text(FAKE.replace('__CONTROL__',repr(str(self.control))).replace('#!/usr/local/bin/python3','#!'+sys.executable,1));self.exe.chmod(0o755)
  self.home=self.base/'profiles'/'p';self.home.mkdir(parents=True)
  self.profile=dict(id='p',provider='codex',home=str(self.home),identity=IDENTITY,category='personal')
  self.metadata({'type':'chatgpt','email':IDENTITY,'planType':'plus'})
 def metadata(self,account): (self.control/'identity').write_text(json.dumps({'account':account}))
 def wait(self,fn):
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   value=fn()
   if value:return value
   time.sleep(.01)
  self.fail('Synthetic peer timed out')
 def start(self):
  s=m.LoginSession(self.profile,str(self.exe));self.addCleanup(s.cancel);return s
 def status(self,s):s.poll();return s.status()
class SessionTests(Fixture):
 def test_exact_launch_and_output_never_exposed(self):
  s=self.start();self.wait(lambda:(self.control/'launch').exists())
  launch=json.loads((self.control/'launch').read_text())
  self.assertEqual(launch['argv'],['-c','cli_auth_credentials_store="file"','-c','forced_login_method="chatgpt"','login'])
  env=launch['env'];env.pop('LC_CTYPE',None);env.pop('__CF_USER_TEXT_ENCODING',None)
  self.assertEqual(env,m.clean_environment('codex',str(self.home),str(self.home)))
  self.assertNotEqual(launch['cwd'],str(self.home));self.assertTrue(Path(launch['cwd']).is_dir())
  for _ in range(20):
   status=self.status(s);self.assertNotIn('DUMMY',json.dumps(status));self.assertIsNone(status['url'])
  (self.control/'finish').touch();self.wait(lambda:not self.status(s)['active'])
  self.assertEqual(s.state,'succeeded');self.assertFalse(Path(launch['cwd']).exists())
 def test_failure_cancel_timeout_and_no_code(self):
  for outcome in ('fail','cancel','timeout'):
   (self.control/'fail').unlink(missing_ok=True);s=self.start()
   with self.assertRaises(Rejected):s.submit_code('DUMMY-CODE')
   if outcome=='fail':(self.control/'fail').touch()
   elif outcome=='cancel':s.cancel()
   else:s.deadline=0
   self.wait(lambda:not self.status(s)['active']);result=s.status()
   self.assertEqual(result['state'],{'fail':'failed','cancel':'cancelled','timeout':'timed_out'}[outcome])
   self.assertIsNotNone(s.process.poll());self.assertNotIn('DUMMY',json.dumps(result))
 def test_all_links_suppressed_without_origin_evidence(self):
  for url in ('https://auth.openai.com/a','https://user@auth.openai.com/a','https://auth.openai.com:443/a','https://auth.openai.com.evil.test/a','http://auth.openai.com/a','https://[bad'):
   self.assertIsNone(m.official_link(url))
 def test_bad_profile_and_spawn_error_are_fixed(self):
  # A work account signs in too since 26 Sep 2026 (b16-work); an account marked neither is still refused.
  for change in ({'provider':'claude'},{'category':'both'},{'category':None},{'identity':''}):
   with self.assertRaises(Rejected):m.LoginSession({**self.profile,**change},str(self.exe))
  with self.assertRaises(Rejected) as error:m.LoginSession(self.profile,'/DUMMY-TOKEN/missing')
  self.assertNotIn('DUMMY',str(error.exception))
  (self.home/'config.toml').write_text('# synthetic')
  with self.assertRaises(Rejected):self.start()
 def test_the_trust_file_codex_writes_doesnt_block_sign_in(self):
  from test_codex_policy import TRUST
  (self.home/'config.toml').write_text(TRUST)
  s=self.start();self.wait(lambda:(self.control/'launch').exists())
  (self.control/'finish').touch();self.wait(lambda:not self.status(s)['active'])
  self.assertEqual(s.state,'succeeded')
  (self.home/'config.toml').write_text('notify = ["/bin/sh"]\n'+TRUST)
  with self.assertRaisesRegex(Rejected,'needs review: config.toml'):self.start()
 def test_cleanup_denial_stays_unknown_and_blocks_replacement(self):
  s=self.start()
  with patch.object(m.os,'killpg',side_effect=PermissionError('DUMMY-TOKEN')):
   s.cancel()
  self.assertEqual(s.status()['state'],'unknown');self.assertTrue(s.status()['active'])
  self.assertNotIn('DUMMY',json.dumps(s.status()))
  s.cancel();self.assertTrue(s.closed)
 def test_exact_environment_passed_to_popen(self):
  import subprocess
  launches=[]
  def spawn(args,**kwargs):
   launches.append((args,kwargs));return subprocess.Popen(args,**kwargs)
  s=m.LoginSession(self.profile,str(self.exe),popen=spawn);self.addCleanup(s.cancel)
  self.assertEqual(launches[0][1]['env'],m.clean_environment('codex',str(self.home),str(self.home)))
  self.assertTrue(launches[0][1]['start_new_session'])
  s.cancel()
 def test_cancel_kills_descendant_that_ignores_term(self):
  (self.control/'descendant').touch();s=self.start()
  self.wait(lambda:(self.control/'heartbeat').exists())
  started=time.monotonic();s.cancel()
  self.assertLess(time.monotonic()-started,1.5);self.assertTrue(s.closed)
  time.sleep(.1);heartbeat=(self.control/'heartbeat').read_text()
  time.sleep(.1);self.assertEqual((self.control/'heartbeat').read_text(),heartbeat)
  self.assertIsNotNone(s.process.poll())
class HostTests(Fixture):
 def setUp(self):
  super().setUp()
  from host import Host
  self.host=Host(self.base);self.addCleanup(self.host.close)
  self.host.store.add_profile('p','codex',self.home,IDENTITY,'personal')
  self.host.store.add_profile('x','codex',self.base/'profiles'/'x','other@example.test','personal')
  self.enterContext(patch('codex_policy.executable',return_value=str(self.exe)))
 def call(self,op):return self.host.dispatch(op,{'profileId':'p'})
 def until(self,condition):
  def step():
   s=self.call('connection_login_status');return s if condition(s) else None
  return self.wait(step)
 def test_success_duplicate_mismatch_and_unknown(self):
  self.assertEqual(self.call('connection_login_status')['state'],'idle');self.assertFalse((self.control/'launch').exists())
  self.host.store.add_project('project',self.base,'p','synthetic-model')
  self.host.store.add_thread('thread','project','channel','shared')
  self.host.store.db.execute("INSERT INTO messages VALUES ('queued','thread','client','local-user','Synthetic queued draft','user')")
  self.host.store.db.execute("INSERT INTO turns VALUES ('queued','thread','queued',NULL,NULL)")
  for account,expected in (({'type':'chatgpt','email':IDENTITY,'planType':'plus'},'identity_verified'),({'type':'chatgpt','email':'wrong@example.test','planType':'plus'},'identity_mismatch'),(None,'signed_out')):
   (self.control/'finish').unlink(missing_ok=True);self.metadata(account)
   self.call('connection_login');s=self.host.logins['p'];self.call('connection_login');self.assertIs(s,self.host.logins['p'])
   with self.assertRaises(Rejected):self.call('connection_check')
   with self.assertRaises(Rejected):self.call('model_check')
   (self.control/'finish').touch()
   result=self.until(lambda s:s.get('identity',{}).get('state') not in (None,'checking'))
   self.assertEqual(result['identity']['state'],expected);self.assertFalse(result['identity']['canSend'])
   self.assertEqual(self.call('connection_status')['state'],expected)
  self.assertEqual({json.loads(line)['method'] for line in (self.control/'requests').read_text().splitlines()},{'initialize','initialized','account/read','account/rateLimits/read'})  # a verified account also reports its rate-limit windows
  self.assertEqual(self.host.turns,{})
  self.assertEqual(self.host.store.turn('queued')['state'],'queued')
  self.assertEqual(self.host.dispatch('connection_status',{'profileId':'x'})['state'],'unchecked')
  self.assertFalse((self.base/'profiles'/'x'/'auth.json').exists())
 def test_late_probe_timeout_and_explicit_recheck(self):
  old=Future()
  with patch.object(self.host.checker,'submit',return_value=old):self.call('connection_check')
  self.call('connection_login');self.host.logins['p'].deadline=0
  self.assertEqual(self.call('connection_login_status')['state'],'timed_out')
  old.set_result({'state':'identity_verified','canSend':False})
  self.assertEqual(self.call('connection_status')['state'],'unchecked')
  self.call('connection_check');self.wait(lambda:self.call('connection_status')['state']=='identity_verified')
  self.assertEqual(self.call('connection_login_status')['state'],'timed_out')
  self.assertEqual(self.call('connection_status')['state'],'identity_verified')
 def test_profile_change_cancels_and_invalidates(self):
  self.call('connection_login');s=self.host.logins['p']
  self.host.store.db.execute('UPDATE profiles SET identity=? WHERE id=?',('changed@example.test','p'))
  self.call('connection_login_status');self.assertTrue(s.closed)
  self.assertEqual(self.call('connection_status')['state'],'unchecked')
  self.assertNotIn('identity',self.call('connection_login_status'))
 def test_a_profile_holding_codex_trust_signs_in_and_checks(self):
  from test_codex_policy import TRUST
  (self.home/'config.toml').write_text(TRUST)
  self.call('connection_login');(self.control/'finish').touch()
  result=self.until(lambda s:s.get('identity',{}).get('state') not in (None,'checking'))
  self.assertEqual(result['identity']['state'],'identity_verified')
  self.assertEqual(self.call('connection_status')['state'],'identity_verified')
  (self.home/'config.toml').write_text('model = "gpt-5.5"\n'+TRUST)
  self.call('connection_check');self.wait(lambda:self.call('connection_status')['state']=='config_blocked')
 def test_unknown_fields_rejected(self):
  with self.assertRaises(Rejected):self.host.dispatch('connection_login',{'profileId':'p','token':'DUMMY'})

 def test_unknown_probe_does_not_verify(self):
  (self.control/'identity').write_text('{}')
  self.call('connection_login');(self.control/'finish').touch()
  result=self.until(lambda s:s.get('identity',{}).get('state') not in (None,'checking'))
  self.assertEqual(result['identity']['state'],'unknown');self.assertFalse(result['identity']['canSend'])
 def test_changed_home_rejects_and_stops_original_process(self):
  self.call('connection_login');session=self.host.logins['p']
  self.host.store.db.execute('UPDATE profiles SET home=? WHERE id=?',(str(self.base/'unowned'),'p'))
  with self.assertRaises(Rejected):self.call('connection_status')
  self.assertTrue(session.closed);self.assertNotIn('p',self.host.checks)
 def test_failed_restart_does_not_reuse_previous_success(self):
  self.call('connection_login');(self.control/'finish').touch()
  self.until(lambda s:s.get('identity',{}).get('state')=='identity_verified')
  with patch('codex_policy.executable',side_effect=Rejected('Synthetic missing CLI')):
   with self.assertRaises(Rejected):self.call('connection_login')
  self.assertEqual(self.call('connection_login_status')['state'],'idle')
  self.assertEqual(self.call('connection_status')['state'],'unchecked')
 def test_host_tick_times_out_without_renderer_polling(self):
  self.call('connection_login');session=self.host.logins['p'];session.deadline=0
  self.host.tick()
  self.assertTrue(session.closed);self.assertEqual(session.state,'timed_out')
  self.assertEqual(self.call('connection_status')['state'],'unchecked')
