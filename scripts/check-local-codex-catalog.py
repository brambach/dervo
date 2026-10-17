"""Read only the authorised personal profile's model catalog. Never send a turn."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from connections_ui import probe_catalog, owned_profile
from transport import OwnedProcess
from codex_policy import EXECUTABLE, VERSION, verify_config
E=ROOT/'evidence/codex-local-upgrade'
PROFILE='c35062c4c28e46568b2a18cbf20616c7'
class ReadOnlyStore:
    db=sqlite3.connect('file:'+str(ROOT/'data/conversations.sqlite3')+'?mode=ro',uri=True)
ReadOnlyStore.db.row_factory=sqlite3.Row
profile=owned_profile(ReadOnlyStore,ROOT/'data',PROFILE)
assert profile['provider']=='codex' and profile['category']=='personal'
result={'version':VERSION,'executable':EXECUTABLE,'profile_id':PROFILE,'methods':[], 'identity_verified':False,'policy_verified':False,'turn_start_count':0}
class MetadataProcess(OwnedProcess):
    def send(self,message):
        method=message.get('method')
        assert method in ('initialize','initialized','config/read','account/read','model/list'), 'Non-metadata request forbidden'
        if method=='model/list':assert result['identity_verified'] and result['policy_verified']
        result['methods'].append(method)
        return super().send(message)
    def read(self,timeout):
        batch=super().read(timeout)
        for event in batch or []:
            value=event.get('result',{})
            if 'userAgent' in value:result['initialize']=value
            if 'layers' in value:
                verify_config(value); result['policy_verified']=True
            if 'account' in value:
                account=value['account'] or {}
                result['identity_verified']=(account.get('email')==profile['identity'] and account.get('type')=='chatgpt' and account.get('planType') in ('plus','prolite','pro'))
                result['plan_type']=account.get('planType')
        return batch
try:
    catalog=probe_catalog(profile,factory=MetadataProcess)
    result['models']=catalog['models']
    result['astra_present']=any(m['model']=='gpt-6-astra' for m in catalog['models'])
    result['state']=catalog['state']
finally:
    ReadOnlyStore.db.close()
    (E/'signed-in-catalog.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('models','initialize')},indent=2))
print('Catalog:',[m['model'] for m in result['models']])
