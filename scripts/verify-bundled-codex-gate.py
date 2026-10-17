"""Exercise the bundled host with a fresh signed-out profile, no model turn."""
import hashlib
import json
from pathlib import Path
import select
import subprocess
import tempfile
import time

root=Path(__file__).resolve().parents[1]
resources=root/'target/release/bundle/macos/Dervo Local.app/Contents/Resources/backend'
modules=('host.py','codex_live.py','codex_policy.py','adapters.py','runner.py','dervo_connections.py','connections_ui.py','transport.py')
hashes={}
for name in modules:
    source=(root/'backend'/name).read_bytes()
    assert source==(resources/name).read_bytes(),name+' bundle differs from source'
    hashes[name]=hashlib.sha256(source).hexdigest()
with tempfile.TemporaryDirectory(prefix='dervo-bundle-noauth-',dir='/private/tmp') as data:
    p=subprocess.Popen(['/usr/local/bin/python3','-E','-s','-B',str(resources/'host.py'),data],
        env={},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    def call(operation,args=None):
        p.stdin.write((json.dumps({'operation':operation,'args':args or {}})+'\n').encode())
        buffer=b''
        deadline=time.monotonic()+10
        while b'\n' not in buffer:
            assert select.select([p.stdout],[],[],max(.001,deadline-time.monotonic()))[0],'Bundled host timed out'
            part=p.stdout.read(1)
            assert part,'Bundled host disconnected'
            buffer+=part
        response=json.loads(buffer)
        assert 'error' not in response,response
        return response['result']
    try:
        profile=call('add_profile',{'provider':'codex','identity':'synthetic-bundle@example.test','category':'personal'})['id']
        project=call('_picked_project',{'path':data,'profile_id':profile,'model':'synthetic-unavailable-model'})['id']
        channel=call('create_conversation',{'projectId':project,'name':'Synthetic signed-out gate','private':True})['id']
        scope={'projectId':project,'channelId':channel}
        message=call('queue',{**scope,'clientId':'synthetic-noauth','text':'Synthetic local draft. Must not be sent.','parentId':None})['id']
        request={**scope,'messageId':message}
        call('start_turn',request)
        deadline=time.monotonic()+40
        while True:
            status=call('turn_status',request)
            if not status['active']:break
            assert time.monotonic()<deadline,'Preflight did not settle'
            time.sleep(.05)
        assert status['state']=='queued',status
        assert 'Personal subscription identity' in status['error'],status
        binding=call('binding',scope)
        assert binding['session_id'] is None,binding
        assert len(call('history',scope))==1
        assert call('shared_context',{'projectId':project})==[]
        result={'scope':'Real bundled host and installed Codex, fresh no-auth profile. No model or project prompt sent.',
            'turn_status':status,'saved_session_id':binding['session_id'],'bundle_source_hashes':hashes,
            'private_context_excluded':True}
    finally:
        p.stdin.close()
        p.wait(timeout=35)
        p.stdout.close();p.stderr.close()
        assert p.returncode==0
(root/'evidence/codex-bundle-noauth.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
