"""The one-trial-per-computer key: a salted SHA-256 of the machine's own ID, never the ID itself.

The server keeps only this hash (dervo_private.trial_identities), so a second account on the same Mac can't start a
second free trial. It isn't a secret and it isn't proof of anything: someone determined can change it, and the email
check on the server still applies. A machine with no readable ID gets a random one, kept in Dervo's data folder.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import uuid

SALT = b'dervo-trial-device-v1:'
FALLBACK = 'trial-device-id'


def _mac_id():
    out = subprocess.run(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], capture_output=True, text=True,
                         timeout=5, check=True).stdout
    found = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"', out)
    return found.group(1).upper() if found else None


def _windows_id():
    import winreg  # noqa: PLC0415 - Windows only
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        value, _ = winreg.QueryValueEx(key, 'MachineGuid')
    return str(value).upper() if re.fullmatch(r'[0-9A-Fa-f-]{36}', str(value)) else None


def _linux_id():
    value = Path('/etc/machine-id').read_text().strip()
    return value if re.fullmatch('[0-9a-f]{32}', value) else None


def machine_id():
    try:
        if sys.platform == 'darwin':
            return _mac_id()
        if sys.platform == 'win32':
            return _windows_id()
        return _linux_id()
    except Exception:  # noqa: BLE001 - any failure falls back to the stored random ID
        return None


def _fallback(data_root):
    if data_root is None:
        return None
    path = Path(data_root) / FALLBACK
    try:
        value = path.read_text().strip()
        if re.fullmatch('[0-9a-f]{32}', value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex
    try:
        path.write_text(value)
    except OSError:
        return None
    return value


def device_key(data_root=None, read=machine_id):
    """The hex hash the server stores, or None when this machine has no ID and no data folder to keep one in."""
    identity = read() or _fallback(data_root)
    if not identity:
        return None
    return hashlib.sha256(SALT + identity.encode()).hexdigest()
