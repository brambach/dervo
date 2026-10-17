"""The Dervo host for the parallel-run load test (tools/load_parallel_runs.py): the real host.py request loop, in its own
process as the app runs it, with only what has to be fake swapped in. Claude Code is backend/fake_claude_load.py behind
a wrapper script, its auth status is a fixed signed-in personal account, the version check is skipped, and the Dervo
account is offline (as release_checks.py does). Nothing here reaches a provider, an account or the network.

    python3 -B tools/load_parallel_host.py <data dir> <fake claude wrapper> <parallel limit>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))

import account_config  # noqa: E402
import claude_coding  # noqa: E402
import claude_version  # noqa: E402
import coding_integration  # noqa: E402

IDENTITY = 'load-test@example.test'
data, wrapper, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
account_config.create = lambda store, data_root, resources=None: None
claude_coding.executable = lambda: wrapper
claude_coding.auth_status = lambda *args, **kwargs: {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                                                      'email': IDENTITY, 'subscriptionType': 'max'}
claude_version.require = lambda *args, **kwargs: None
coding_integration.MAX_PARALLEL_OWNERS = limit

import host  # noqa: E402

served = host.Host(data)
try:
    host.serve(served)
finally:
    served.close()
