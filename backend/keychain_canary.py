"""Read only a disposable native self-check item. Never accept a real namespace."""
import ctypes as C
import re
import sys
from account_keychain import LegacyMacKeychain


def main(account):
    if not re.fullmatch('0{24}[0-9a-f]{40}', account): return 2
    store = LegacyMacKeychain(project_ref='z' * 20, profile='private-beta', workspace_id='native-self-check')
    store.service = 'com.dervo.account.v2.' + 'z' * 20 + '.private-beta'
    store.account = account
    result = C.c_void_p()
    with store._dictionary({**store._base(), 'kSecReturnData': store._symbol('kCFBooleanTrue', cf=True)}) as query:
        status = store.sec.SecItemCopyMatching(query, C.byref(result))
    if result.value: store.cf.CFRelease(result)
    # Interaction-not-allowed or authentication-failed proves Python wasn't granted access.
    if status in (-25308, -25293): return 0
    if status == -25300: return 3
    if status == 0: return 5
    return 4


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]) if len(sys.argv) == 2 else 2)
