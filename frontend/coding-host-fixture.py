"""Synthetic protocol browser fixture. Real host, broker, sandbox and temporary Store."""
import json, os, select, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from host import Host
from test_codex_coding import SyntheticCodingPeer, SID
import codex_coding
peers = []
class Peer(SyntheticCodingPeer):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        peers.append(self)
    def tool(self, tool, arguments, number):
        self.queue.append(dict(id='rpc'+str(number), method='item/tool/call', params=dict(threadId=SID,turnId='turn-1',callId='call'+str(number),tool=tool,arguments=arguments)))
    def send(self, message):
        if message.get('method') == 'turn/start':
            self.sent.append(message)
            self.queue.append(dict(id=message['id'],result={'turn':{'id':'turn-1'}}))
            self.tool('dervo_read_file', {'path':'test_sample.py'}, 1)
        elif message.get('id') == 'rpc1':
            self.sent.append(message)
            data=json.loads(message['result']['contentItems'][0]['text'])
            self.tool('dervo_write_file', dict(path='test_sample.py',expected_hash=data['sha256'],content=data['content']+'\n# synthetic browser correction\n'),2)
        elif message.get('id') == 'rpc2':
            self.sent.append(message)
            self.tool('dervo_run_tests',{},3)
        elif message.get('id') == 'rpc3':
            self.sent.append(message)
            self.queue.extend([dict(method='item/agentMessage/delta',params=dict(threadId=SID,turnId='turn-1',itemId='answer',delta='Synthetic peer result. Inspect actual test evidence.')),
                dict(method='turn/completed',params=dict(threadId=SID,turn=dict(id='turn-1',status='completed')))])
        else:
            super().send(message)
codex_coding.OwnedProcess = Peer
host=Host(Path(sys.argv[1]) / 'store')
try:
    buffer=b''
    while True:
        host.tick()
        if b'\n' not in buffer:
            if not select.select([sys.stdin],[],[],.02)[0]:continue
            chunk=os.read(sys.stdin.fileno(),65536)
            if not chunk:break
            buffer+=chunk
            if b'\n' not in buffer:continue
        line,buffer=buffer.split(b'\n',1)
        try:
            request=json.loads(line)
            op,args=request['operation'],request['args']
            if op=='_picked_project':result=host.add_picked_project(**args)
            elif op=='_fixture_starts':result=sum(m.get('method')=='turn/start' for p in peers for m in p.sent)
            else:result=host.dispatch(op,args)
            response={'result':result}
        except Exception as exc:response={'error':str(exc)}
        print(json.dumps(response),flush=True)
finally:host.close()
