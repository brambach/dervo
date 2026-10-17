"""Explicit synthetic Codex peer for stdio integration tests only.

No model calls, credentials, shell tools or network. Never launched by host.py.
"""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from test_codex_live import SyntheticPeer
peer=SyntheticPeer(sys.argv[1:],cwd=os.getcwd(),env=dict(os.environ))  # the -c flags Dervo would have launched Codex with
for line in sys.stdin:
    peer.send(json.loads(line))
    while peer.queue:
        print(json.dumps(peer.queue.pop(0)),flush=True)
