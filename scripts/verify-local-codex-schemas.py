"""Run production request generation against the installed official schemas."""
import json
from pathlib import Path
import sys
import unittest
import jsonschema
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from adapters import CodexAdapter
E=ROOT/'evidence/codex-local-upgrade'
names={'initialize':'InitializeParams','config/read':'ConfigReadParams','account/read':'GetAccountParams','model/list':'ModelListParams','thread/start':'ThreadStartParams','thread/resume':'ThreadResumeParams','thread/read':'ThreadReadParams','turn/start':'TurnStartParams','turn/interrupt':'TurnInterruptParams'}
original=CodexAdapter.request
checked={}
def request(self,method,params):
    matches=list((E/'schema').rglob(names[method]+'.json'))
    assert len(matches)==1,(method,matches)
    schema=json.loads(matches[0].read_text())
    jsonschema.Draft7Validator(schema).validate(params)
    checked[method]=checked.get(method,0)+1
    return original(self,method,params)
CodexAdapter.request=request
suite=unittest.defaultTestLoader.discover(str(ROOT/'backend'),pattern='test_codex_live.py')
result=unittest.TextTestRunner(verbosity=1).run(suite)
(E/'schema-validation.json').write_text(json.dumps({'successful':result.wasSuccessful(),'requests_checked':checked},indent=2))
assert result.wasSuccessful()
print(json.dumps(checked,indent=2))
