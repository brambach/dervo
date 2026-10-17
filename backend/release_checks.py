"""Run offline build checks without connecting disposable Hosts to the bundled account.

This is a test entry point, never used by the app host. Tests that explicitly pass
an account runtime or resource root retain that configuration and its access gates.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import account_config


def run(names):
    create = account_config.create
    def offline_default(store, data_root, resources=None):
        return create(store, data_root, resources) if resources is not None else None
    with patch.object(account_config, 'create', offline_default):
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        return unittest.TextTestRunner(verbosity=2, durations=20).run(suite).wasSuccessful()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('Name the offline test modules to run.')
    if sys.platform.startswith('linux') and not os.environ.get('XDG_DATA_HOME'):
        # Linux keeps app data in XDG_DATA_HOME or ~/.local/share from the account database (platform_os.app_support_dir),
        # which the Mac tests' Path.home patches don't reach. A throwaway folder keeps the run out of the real one.
        data = tempfile.TemporaryDirectory(prefix='dervo-checks-data-')
        os.environ['XDG_DATA_HOME'] = data.name
    raise SystemExit(0 if run(sys.argv[1:]) else 1)
