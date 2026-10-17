"""Browser test fixture. Production Host with synthetic peers, no provider process."""
import json
import select
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from host import Host
from test_codex_live import SyntheticPeer

signed_in = False
peers = []
def factory(*args, **kwargs):
    peer = SyntheticPeer(*args, **kwargs, signed_in=signed_in)
    peers.append(peer)
    return peer

host = Host(sys.argv[1], transport_factory=factory)
try:
    import os
    buffer = b''
    while True:
        host.tick()
        if b'\n' not in buffer:
            if not select.select([sys.stdin], [], [], .02)[0]:
                continue
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            if b'\n' not in buffer:
                continue
        line, buffer = buffer.split(b'\n', 1)
        try:
            request = json.loads(line)
            operation, args = request['operation'], request['args']
            if operation == '_fixture_signed_in':
                signed_in = args['value']
                result = None
            elif operation == '_fixture_starts':
                result = sum(m.get('method') == 'turn/start' for p in peers for m in p.sent)
            elif operation == '_picked_project':
                result = host.add_picked_project(**args)
            else:
                result = host.dispatch(operation, args)
            response = {'result': result}
        except Exception as error:
            response = {'error': str(error)}
        print(json.dumps(response), flush=True)
finally:
    host.close()
