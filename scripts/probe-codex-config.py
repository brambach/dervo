"""No-auth metadata only. Never creates threads or requests a model turn."""
import sys, tempfile, json, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from transport import OwnedProcess,clean_environment
with tempfile.TemporaryDirectory(prefix='dervo-config-',dir='/private/tmp') as home, tempfile.TemporaryDirectory(prefix='dervo-neutral-',dir='/private/tmp') as cwd:
    from codex_policy import argv as policy_argv
    argv=policy_argv()
    with OwnedProcess(argv,cwd=cwd,env=clean_environment('codex',home,home)) as p:
        for i,(method,params) in enumerate([('initialize',{'clientInfo':{'name':'dervo','version':'0.1.0'},'capabilities':{'experimentalApi':False}}),('account/read',{'refreshToken':False}),('config/read',{'includeLayers':True,'cwd':cwd})],1):
            p.send({'id':i,'method':method,'params':params})
            done=False
            while not done:
                try: batch=p.read(20)
                except TimeoutError: raise RuntimeError('Metadata deadline: '+p.stderr.decode())
                if batch is None: raise RuntimeError(p.stderr.decode())
                for m in batch:
                    if m.get('id')==i:
                        print(json.dumps({'method':method,'response':m}))
                        done=True
            if i==1:p.send({'method':'initialized','params':{}})
