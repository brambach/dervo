"""Sign a built Dervo bundle: Developer ID with notarisation when Bryce's certificate is in the login Keychain, ad hoc
otherwise.

release_builder calls ``sign`` once the bundle is complete. With a "Developer ID Application" identity present (or one
named in the build config) the bundle is signed inside out with the hardened runtime and a secure timestamp, uploaded to
Apple's notary service through the ``dervo-notary`` keychain profile, stapled, then checked with codesign and
Gatekeeper. Without one, the bundle is signed ad hoc exactly as before. Every step's exit code and log land in the
release folder through the builder's ``run``.

Nothing here reads or prints a secret. The notarisation password lives in the login Keychain under the profile that
``xcrun notarytool store-credentials dervo-notary`` created, and codesign reads the private key from the Keychain
itself. A password in the build config is refused rather than passed anywhere.

Bundled binaries that already carry a Developer ID signature with the hardened runtime, such as the official Codex
runtime (signed by OpenAI), are left exactly as shipped. codex_runtime.py pins their SHA-256, and Apple's notary
service accepts nested code signed by another team as long as it's hardened and timestamped.
"""
import json
from pathlib import Path
import plistlib
import re
import subprocess

PROFILE = 'dervo-notary'
ENTITLEMENTS = 'signing/Dervo.entitlements'
# Nested code that needs entitlements of its own. The voice audio helper reads the microphone, which the hardened
# runtime blocks without audio-input; dervo-local itself stays without it.
NESTED_ENTITLEMENTS = {'Contents/Resources/voice/dervo-voice-audio': 'voice/mac/dervo-voice-audio.entitlements'}
MAIN_EXECUTABLE = 'Contents/MacOS/dervo-local'
NOTARY_UPLOAD = 'notary-upload.zip'
NOTARY_LOG = 'notary-log.json'
# Keys a build config must never carry. Credentials go into the Keychain through notarytool, never through a file
# that ends up in a release folder log.
SECRET_KEYS = {'applePassword', 'appleIdPassword', 'notaryPassword', 'password', 'appSpecificPassword', 'apiKey',
               'notaryKey', 'privateKey', 'appleApiKey', 'appleApiIssuer'}
IDENTITY = re.compile(r'^\s*\d+\)\s+([0-9A-F]{40})\s+"(Developer ID Application: (.+?) \(([A-Z0-9]{10})\))"\s*$', re.M)
MACH_O_MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
                b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def inspect(command, env=None, pass_fds=()):
    """A read-only probe whose output is parsed rather than logged. Returns (exit code, combined output)."""
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120, check=False, pass_fds=pass_fds)
    return result.returncode, (result.stdout or '') + (result.stderr or '')


def identities(text):
    """Developer ID Application identities in ``security find-identity -v -p codesigning`` output. Apple Development,
    Mac App Store and installer identities are ignored on purpose: none of them can sign a notarised download."""
    return [{'hash': hash_, 'name': name, 'holder': holder, 'team': team}
            for hash_, name, holder, team in IDENTITY.findall(text)]


def choose_identity(config, listed):
    """The identity to sign with, or None for ad hoc. ``signingIdentity`` in the config may name one (its full name,
    the holder's name, its Team ID or its SHA-1) or be "-" to force ad hoc while a certificate exists."""
    explicit = config.get('signingIdentity')
    if explicit is not None and (type(explicit) is not str or not explicit.strip()):
        raise RuntimeError('signingIdentity in the build config must be a certificate name, a Team ID or "-".')
    if explicit == '-':
        return None
    if explicit:
        wanted = explicit.strip()
        matches = [item for item in listed if wanted in (item['name'], item['holder'], item['team'], item['hash'])]
        if len(matches) != 1:
            raise RuntimeError('signingIdentity in the build config doesn’t name exactly one Developer ID Application '
                               'certificate in the login Keychain: ' + wanted)
        return matches[0]
    if len(listed) > 1:
        raise RuntimeError('More than one Developer ID Application certificate is in the login Keychain. Set '
                           'signingIdentity in the build config to the one Dervo ships with.')
    return listed[0] if listed else None


def refuse_secrets(config):
    """Credentials never travel through the build config: a value there would end up in argv and a log."""
    found = sorted(key for key in config if key in SECRET_KEYS or 'password' in key.lower() or 'secret' in key.lower())
    if found:
        raise RuntimeError('The build config carries a credential (' + ', '.join(found) + '). Remove it and store the '
                           'notarisation password with: xcrun notarytool store-credentials ' + PROFILE)


def is_mach_o(path):
    try:
        with path.open('rb') as stream:
            return stream.read(4) in MACH_O_MAGIC
    except OSError:
        return False


def mach_o_files(bundle):
    """Every Mach-O inside the bundle except the main executable, deepest first so nested code is signed before what
    contains it. Symlinks and the signature folder are skipped."""
    bundle = Path(bundle)
    found = []
    for path in bundle.rglob('*'):
        if path.is_symlink() or not path.is_file() or '_CodeSignature' in path.parts:
            continue
        relative = path.relative_to(bundle)
        if str(relative) == MAIN_EXECUTABLE or not is_mach_o(path):
            continue
        found.append(path)
    return sorted(found, key=lambda path: (-len(path.relative_to(bundle).parts), str(path)))


def pinned_runtime_files(bundle):
    """Binaries whose SHA-256 the app checks at launch (codex_runtime.py). Re-signing one would change its hash."""
    manifest = Path(bundle) / 'Contents/Resources/runtime/codex.json'
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return set()
    names = {data.get('file')}
    companion = data.get('companion')
    if type(companion) is dict:
        names.add(companion.get('file'))
    return {'Contents/Resources/runtime/' + name for name in names if type(name) is str and name}


def signature_details(text):
    """TeamIdentifier, hardened-runtime flag and timestamp presence from ``codesign -d --verbose=2`` output."""
    team = re.search(r'^TeamIdentifier=(.*)$', text, re.M)
    team = team.group(1).strip() if team else ''
    if team == 'not set' or not re.fullmatch(r'[A-Z0-9]{10}', team):
        team = None
    hardened = bool(re.search(r'^CodeDirectory .*flags=0x[0-9a-f]+\((?:[a-z,]*,)?runtime(?:,[a-z,]*)?\)', text, re.M))
    timestamped = bool(re.search(r'^Timestamp=', text, re.M))
    return {'team': team, 'hardened': hardened, 'timestamped': timestamped}


def log_text(output, label):
    try:
        return (Path(output) / (label + '.log')).read_text(errors='replace')
    except OSError:
        return ''


def notary_result(text):
    """Submission id and final status from ``notarytool submit --wait`` output."""
    ids = re.findall(r'^\s*id:\s*([0-9a-f-]{36})\s*$', text, re.M)
    statuses = re.findall(r'^\s*status:\s*(\S.*?)\s*$', text, re.M)
    return (ids[0] if ids else None), (statuses[-1] if statuses else None)


def gatekeeper_accepted(text):
    return bool(re.search(r':\s*accepted\s*$', text, re.M)) and 'source=Notarized Developer ID' in text


def sign(bundle, *, run, output, work, config, inspect=inspect, env=None):
    """Sign, notarise, staple and verify ``bundle``. Returns the record the builder stores as ``state['signing']``.
    ``run(label, command, cwd, timeout=...)`` is the builder's logged step runner and raises on a non-zero exit."""
    bundle, output, work = Path(bundle), Path(output), Path(work)
    refuse_secrets(config)
    code, listing = inspect(['/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning'], env)
    if code != 0:
        raise RuntimeError('Dervo couldn’t check the signing identities in Keychain. No signing was attempted. '
                           'Unlock the login keychain and try again.')
    listed = identities(listing)
    identity = choose_identity(config, listed)
    profile = config.get('notaryProfile') or PROFILE
    if type(profile) is not str or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', profile):
        raise RuntimeError('notaryProfile in the build config must be a keychain profile name.')
    if identity is None:
        # Today's path, unchanged: an ad hoc signature so the bundle is sealed and codesign --verify passes.
        run('sign', ['/usr/bin/codesign', '--force', '--sign', '-', str(bundle)], work)
        run('verify-sign', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], work)
        return {'mode': 'ad-hoc', 'identity': None, 'team': None, 'notarised': False, 'stapled': False,
                'developer_id_certificates': len(listed),
                'detail': ('Signed ad hoc. Testers see a Gatekeeper warning on first open. Add a Developer ID '
                           'Application certificate to the login Keychain and a ' + PROFILE + ' notarytool profile, '
                           'then build again.')}
    entitlements = work / ENTITLEMENTS
    if entitlements.is_symlink() or not entitlements.is_file():
        raise RuntimeError('The entitlements file is missing from the source snapshot: ' + ENTITLEMENTS)
    if type(plistlib.loads(entitlements.read_bytes())) is not dict:
        raise RuntimeError('The entitlements file must be a plist dictionary: ' + ENTITLEMENTS)
    record = {'mode': 'developer-id', 'identity': identity['name'], 'team': identity['team'], 'profile': profile,
              'entitlements': ENTITLEMENTS, 'nested': [], 'notarised': False, 'stapled': False}
    pinned = pinned_runtime_files(bundle)
    signed = 0
    for path in mach_o_files(bundle):
        relative = str(path.relative_to(bundle))
        verified, _ = inspect(['/usr/bin/codesign', '--verify', '--strict', str(path)], env)
        _, shown = inspect(['/usr/bin/codesign', '-d', '--verbose=2', str(path)], env)
        details = signature_details(shown)
        if verified == 0 and details['team'] and details['hardened'] and details['timestamped']:
            record['nested'].append({'path': relative, 'action': 'kept', 'team': details['team']})
            continue
        if relative in pinned:
            raise RuntimeError(relative + ' isn’t signed by its publisher with the hardened runtime and a timestamp, '
                               'and Dervo checks its hash at launch, so it can’t be re-signed here. Vendor the '
                               'official build again.')
        signed += 1
        extra = []
        if relative in NESTED_ENTITLEMENTS:
            own = work / NESTED_ENTITLEMENTS[relative]
            if own.is_symlink() or not own.is_file() or type(plistlib.loads(own.read_bytes())) is not dict:
                raise RuntimeError('The entitlements file is missing from the source snapshot: ' + NESTED_ENTITLEMENTS[relative])
            extra = ['--entitlements', str(own)]
        run('sign-nested-' + str(signed), ['/usr/bin/codesign', '--force', '--options', 'runtime', '--timestamp',
                                            *extra, '--sign', identity['hash'], str(path)], work)
        record['nested'].append({'path': relative, 'action': 'signed', 'team': identity['team'],
                                 **({'entitlements': NESTED_ENTITLEMENTS[relative]} if extra else {})})
    run('sign', ['/usr/bin/codesign', '--force', '--options', 'runtime', '--timestamp', '--entitlements',
                 str(entitlements), '--sign', identity['hash'], str(bundle)], work)
    run('verify-sign', ['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(bundle)], work)
    _, shown = inspect(['/usr/bin/codesign', '-d', '--verbose=2', str(bundle)], env)
    details = signature_details(shown)
    if details['team'] != identity['team'] or not details['hardened']:
        raise RuntimeError('The signed app doesn’t report the expected Team ID and hardened runtime. Open '
                           'verify-sign.log in this release folder.')
    upload = output / NOTARY_UPLOAD
    submission = status = problem = None
    try:
        run('notary-zip', ['/usr/bin/ditto', '-c', '-k', '--keepParent', str(bundle), str(upload)], work)
        try:
            run('notarise', ['/usr/bin/xcrun', 'notarytool', 'submit', str(upload), '--keychain-profile', profile,
                             '--wait', '--timeout', '55m'], work, timeout=3600)
        except RuntimeError as exc:
            problem = str(exc)
        submission, status = notary_result(log_text(output, 'notarise'))
        record['notarisation'] = {'id': submission, 'status': status}
        if submission and status != 'Accepted':
            # Best effort: Apple's reasons land beside the build. A failure here keeps the original problem.
            try:
                run('notary-log', ['/usr/bin/xcrun', 'notarytool', 'log', submission, '--keychain-profile', profile,
                                   str(output / NOTARY_LOG)], work)
            except RuntimeError:
                pass
    finally:
        try:
            upload.unlink()
        except OSError:
            pass
    if status != 'Accepted':
        raise RuntimeError('Apple didn’t accept the build for notarisation (status: ' + str(status)
                           + ('; ' + problem if problem else '') + '). Open notarise.log and ' + NOTARY_LOG
                           + ' in this release folder.')
    record['notarised'] = True
    run('staple', ['/usr/bin/xcrun', 'stapler', 'staple', str(bundle)], work)
    run('staple-validate', ['/usr/bin/xcrun', 'stapler', 'validate', str(bundle)], work)
    record['stapled'] = True
    run('verify-stapled', ['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(bundle)], work)
    run('gatekeeper', ['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=4', str(bundle)], work)
    if not gatekeeper_accepted(log_text(output, 'gatekeeper')):
        raise RuntimeError('Gatekeeper didn’t accept the notarised app. Open gatekeeper.log in this release folder.')
    record['detail'] = ('Signed with ' + identity['name'] + ', notarised by Apple and stapled. Gatekeeper accepts it '
                        'as a Notarized Developer ID app.')
    return record
