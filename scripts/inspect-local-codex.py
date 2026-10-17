"""Metadata-only compatibility inspection in a fresh signed-out profile."""
import json
from pathlib import Path
import sys
import tempfile
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from codex_policy import argv, verify_config
from transport import OwnedProcess, clean_environment
E=ROOT/'evidence/codex-local-upgrade'
url='https://raw.githubusercontent.com/openai/codex/rust-v0.156.1/codex-rs/core/config.schema.json'
(E/'config-schema.json').write_bytes(urllib.request.urlopen(url).read())
lock=json.loads((ROOT/'tools/codex-lock.json').read_text())
with tempfile.TemporaryDirectory(dir=ROOT/'tools/cache') as home, tempfile.TemporaryDirectory(dir='/private/tmp') as cwd:
    args=argv(); args[0]=lock['executable']
    with OwnedProcess(args,cwd=cwd,env=clean_environment('codex',home,home)) as p:
        records=[]
        def call(method,params,id):
            p.send({'id':id,'method':method,'params':params})
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                for m in p.read(max(.01,deadline-time.monotonic())) or []:
                    records.append(m)
                    if m.get('id')==id:
                        assert 'error' not in m,m
                        return m['result']
            raise TimeoutError(method)
        try:
            init=call('initialize',{'clientInfo':{'name':'dervo','version':'0.1.0'},'capabilities':{'experimentalApi':False}},1)
            p.send({'method':'initialized','params':{}})
            config=call('config/read',{'includeLayers':True,'cwd':cwd},2)
            verify_config(config)
            account=call('account/read',{'refreshToken':False},3)
            assert account['account'] is None
            print('New CLI initialize, exact effective policy and signed-out metadata passed.',init)
        finally:
            (E/'new-noauth.json').write_text(json.dumps(records,indent=2))
            (E/'new-noauth-stderr.log').write_bytes(p.stderr)
