//! Keychain access stays in the signed app. Only its bundled host inherits the private socket.
use serde_json::{json, Value};
use std::{ffi::c_void, io::{BufRead, BufReader, Write}, os::unix::{io::AsRawFd, net::UnixStream, process::CommandExt}, path::Path, process::Command};

const LIMIT: usize = 131072;
const WIRE_LIMIT: usize = LIMIT * 2 + 2048;
const ERROR: &str = "Dervo couldn't access its saved session in macOS Keychain.";
type Ref = *const c_void;

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFRelease(value: Ref);
    fn CFEqual(a: Ref, b: Ref) -> u8;
    fn CFGetTypeID(value: Ref) -> usize;
    fn CFArrayCreate(allocator: Ref, values: *const Ref, count: isize, callbacks: Ref) -> Ref;
    fn CFArrayGetCount(value: Ref) -> isize;
    fn CFArrayGetValueAtIndex(value: Ref, index: isize) -> Ref;
    fn CFDataGetTypeID() -> usize;
    fn CFDataCreate(allocator: Ref, bytes: *const u8, length: isize) -> Ref;
    fn CFDataGetLength(value: Ref) -> isize;
    fn CFDataGetBytePtr(value: Ref) -> *const u8;
    fn CFStringCreateWithBytes(allocator: Ref, bytes: *const u8, length: isize, encoding: u32, external: u8) -> Ref;
    fn CFDictionaryCreateMutable(allocator: Ref, capacity: isize, keys: Ref, values: Ref) -> Ref;
    fn CFDictionarySetValue(dictionary: Ref, key: Ref, value: Ref);
    fn CFDictionaryGetValue(dictionary: Ref, key: Ref) -> Ref;
    static kCFTypeArrayCallBacks: u8;
    static kCFTypeDictionaryKeyCallBacks: u8;
    static kCFTypeDictionaryValueCallBacks: u8;
    static kCFBooleanTrue: Ref;
    static kCFBooleanFalse: Ref;
}
#[link(name = "Security", kind = "framework")]
extern "C" {
    fn SecItemCopyMatching(query: Ref, result: *mut Ref) -> i32;
    fn SecItemAdd(query: Ref, result: *mut Ref) -> i32;
    fn SecItemUpdate(query: Ref, attributes: Ref) -> i32;
    fn SecTrustedApplicationCreateFromPath(path: *const i8, result: *mut Ref) -> i32;
    fn SecAccessCreate(description: Ref, trusted: Ref, result: *mut Ref) -> i32;
    fn SecAccessCopyACLList(access: Ref, result: *mut Ref) -> i32;
    fn SecACLCopyContents(acl: Ref, applications: *mut Ref, description: *mut Ref, prompt: *mut u16) -> i32;
    fn SecACLSetContents(acl: Ref, applications: Ref, description: Ref, prompt: u16) -> i32;
    fn SecKeychainSetUserInteractionAllowed(allowed: u8) -> i32;
    fn SecCodeCopySelf(flags: u32, result: *mut Ref) -> i32;
    fn SecCodeCopySigningInformation(code: Ref, flags: u32, result: *mut Ref) -> i32;
    fn SecCodeCopyStaticCode(code: Ref, flags: u32, result: *mut Ref) -> i32;
    fn SecStaticCodeCheckValidity(code: Ref, flags: u32, requirement: Ref) -> i32;
    static kSecCodeInfoTeamIdentifier: Ref;
    static kSecClass: Ref;
    static kSecClassGenericPassword: Ref;
    static kSecAttrService: Ref;
    static kSecAttrAccount: Ref;
    static kSecAttrSynchronizable: Ref;
    static kSecUseAuthenticationUI: Ref;
    static kSecUseAuthenticationUIFail: Ref;
    static kSecReturnData: Ref;
    static kSecMatchLimit: Ref;
    static kSecMatchLimitOne: Ref;
    static kSecValueData: Ref;
    static kSecAttrAccess: Ref;
}
extern "C" { fn fcntl(fd: i32, command: i32, ...) -> i32; }

struct Owned(Ref);
impl Owned {
    fn checked(value: Ref) -> Result<Self, String> { if value.is_null() { Err(ERROR.into()) } else { Ok(Self(value)) } }
    fn string(value: &str) -> Result<Self, String> { Self::checked(unsafe { CFStringCreateWithBytes(std::ptr::null(), value.as_ptr(), value.len() as isize, 0x08000100, 0) }) }
    fn data(value: &[u8]) -> Result<Self, String> { Self::checked(unsafe { CFDataCreate(std::ptr::null(), value.as_ptr(), value.len() as isize) }) }
    fn dictionary() -> Result<Self, String> { Self::checked(unsafe { CFDictionaryCreateMutable(std::ptr::null(), 0, &kCFTypeDictionaryKeyCallBacks as *const _ as Ref, &kCFTypeDictionaryValueCallBacks as *const _ as Ref) }) }
    fn set(&self, key: Ref, value: Ref) { unsafe { CFDictionarySetValue(self.0, key, value) } }
}
impl Drop for Owned { fn drop(&mut self) { unsafe { CFRelease(self.0) } } }

fn copied(call: impl FnOnce(*mut Ref) -> i32) -> Result<Owned, String> {
    let mut value = std::ptr::null();
    let status = call(&mut value);
    if status != 0 { if !value.is_null() { unsafe { CFRelease(value) } } return Err(ERROR.into()); }
    Owned::checked(value)
}

pub fn signed() -> Result<bool, String> {
    let code = copied(|out| unsafe { SecCodeCopySelf(0, out) })?;
    let info = copied(|out| unsafe { SecCodeCopySigningInformation(code.0, 2, out) })?;
    let team = unsafe { CFDictionaryGetValue(info.0, kSecCodeInfoTeamIdentifier) };
    if team.is_null() { return Ok(false); }
    if unsafe { CFEqual(team, Owned::string("PP5K3YUUX6")?.0) } == 0 { return Err("This app has an unexpected signing team.".into()); }
    let static_code = copied(|out| unsafe { SecCodeCopyStaticCode(code.0, 0, out) })?;
    if unsafe { SecStaticCodeCheckValidity(static_code.0, 1 | 16, std::ptr::null()) } != 0 {
        return Err("Dervo's signed resources couldn't be verified. Reinstall the verified app.".into());
    }
    Ok(true)
}

fn namespace(service: &str, account: &str) -> bool {
    let parts: Vec<_> = service.split('.').collect();
    parts.len() == 6 && parts[0] == "com" && parts[1] == "dervo"
        && matches!(parts[2], "account" | "provider-key" | "openrouter") && parts[3] == "v2"
        && parts[4].len() == 20 && parts[4].bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        && matches!(parts[5], "private-beta" | "stable" | "personal-experimental")
        && (parts[2] != "openrouter" || parts[5] == "personal-experimental")
        && account.len() == 64 && account.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn query(service: &str, account: &str) -> Result<Owned, String> {
    // The file-based Keychain needs its process-level no-prompt switch as well.
    if unsafe { SecKeychainSetUserInteractionAllowed(0) } != 0 { return Err(ERROR.into()); }
    let value = Owned::dictionary()?;
    unsafe {
        value.set(kSecClass, kSecClassGenericPassword);
        value.set(kSecAttrService, Owned::string(service)?.0);
        value.set(kSecAttrAccount, Owned::string(account)?.0);
        value.set(kSecAttrSynchronizable, kCFBooleanFalse);
        value.set(kSecUseAuthenticationUI, kSecUseAuthenticationUIFail);
    }
    Ok(value)
}

fn read(service: &str, account: &str) -> Result<Option<Vec<u8>>, String> {
    let query = query(service, account)?;
    unsafe { query.set(kSecReturnData, kCFBooleanTrue); query.set(kSecMatchLimit, kSecMatchLimitOne); }
    let mut result = std::ptr::null();
    let status = unsafe { SecItemCopyMatching(query.0, &mut result) };
    if status == -25300 { return Ok(None); }
    if status != 0 { if !result.is_null() { unsafe { CFRelease(result) } } return Err(format!("{ERROR} (read status {status})")); }
    let data = Owned::checked(result)?;
    if unsafe { CFGetTypeID(data.0) != CFDataGetTypeID() } { return Err(ERROR.into()); }
    let length = unsafe { CFDataGetLength(data.0) };
    if length <= 0 || length as usize > LIMIT + 1 { return Err(ERROR.into()); }
    let bytes = unsafe { CFDataGetBytePtr(data.0) };
    if bytes.is_null() { return Err(ERROR.into()); }
    decode_record(unsafe { std::slice::from_raw_parts(bytes, length as usize) }).map(Some)
}

// SecItemUpdate can leave the old data in place when given an empty value.
// Every native v2 item therefore has an explicit state byte, including tombstones.
fn encode_record(bytes: &[u8]) -> Result<Vec<u8>, String> {
    if bytes.len() > LIMIT { return Err(ERROR.into()); }
    let mut record = Vec::with_capacity(bytes.len() + 1);
    record.push(u8::from(!bytes.is_empty()));
    record.extend_from_slice(bytes);
    Ok(record)
}

fn decode_record(record: &[u8]) -> Result<Vec<u8>, String> {
    match record {
        [0] => Ok(Vec::new()),
        [1, payload @ ..] if !payload.is_empty() && payload.len() <= LIMIT => Ok(payload.to_vec()),
        _ => Err("Dervo's saved Keychain record has an invalid format.".into()),
    }
}

fn access() -> Result<Owned, String> {
    let description = Owned::string("Dervo saved session")?;
    let trusted = copied(|out| unsafe { SecTrustedApplicationCreateFromPath(std::ptr::null(), out) })?;
    let trusted_apps = Owned::checked(unsafe { CFArrayCreate(std::ptr::null(), &trusted.0, 1, &kCFTypeArrayCallBacks as *const _ as Ref) })?;
    let access = copied(|out| unsafe { SecAccessCreate(description.0, trusted_apps.0, out) })?;
    let entries = copied(|out| unsafe { SecAccessCopyACLList(access.0, out) })?;
    let mut restricted = 0;
    for index in 0..unsafe { CFArrayGetCount(entries.0) } {
        let acl = unsafe { CFArrayGetValueAtIndex(entries.0, index) };
        let (mut apps, mut label, mut prompt) = (std::ptr::null(), std::ptr::null(), 0);
        let status = unsafe { SecACLCopyContents(acl, &mut apps, &mut label, &mut prompt) };
        let apps = if apps.is_null() { None } else { Some(Owned(apps)) };
        let label = if label.is_null() { None } else { Some(Owned(label)) };
        if status != 0 { return Err(ERROR.into()); }
        if apps.is_some() {
            // A trusted app remains automatic. An untrusted caller must require
            // the user's password, not the default confirmation-only prompt.
            let label = label.as_ref().ok_or(ERROR)?;
            if unsafe { SecACLSetContents(acl, trusted_apps.0, label.0, prompt | 1) } != 0 { return Err(ERROR.into()); }
            restricted += 1;
        }
    }
    if restricted == 0 { return Err(ERROR.into()); }
    Ok(access)
}

fn write(service: &str, account: &str, bytes: &[u8]) -> Result<(), String> {
    let query = query(service, account)?;
    let attributes = Owned::dictionary()?;
    let access = access()?;
    let data = Owned::data(&encode_record(bytes)?)?;
    unsafe { attributes.set(kSecValueData, data.0); attributes.set(kSecAttrAccess, access.0); }
    let mut status = unsafe { SecItemUpdate(query.0, attributes.0) };
    if status == -25300 {
        unsafe { query.set(kSecValueData, data.0); query.set(kSecAttrAccess, access.0); }
        status = unsafe { SecItemAdd(query.0, std::ptr::null_mut()) };
    }
    if status == 0 { Ok(()) } else { Err(format!("{ERROR} (write status {status})")) }
}

// Dervo on your phone (src/phone/): the Mac's secret key and room key, one item per data folder. Only this process
// reads it: the host's Keychain socket only serves `namespace` services, which this isn't.
const PHONE_LINK_SERVICE: &str = "com.dervo.phone-link.v1";
pub(crate) fn phone_link_read(account: &str) -> Result<Option<Vec<u8>>, String> {
    Ok(read(PHONE_LINK_SERVICE, account)?.filter(|bytes| !bytes.is_empty()))
}
pub(crate) fn phone_link_write(account: &str, bytes: &[u8]) -> Result<(), String> { write(PHONE_LINK_SERVICE, account, bytes) }

fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }
fn unhex(text: &str) -> Result<Vec<u8>, String> {
    if text.is_empty() || text.len() > LIMIT * 2 || text.len() % 2 != 0 || !text.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) { return Err(ERROR.into()); }
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i+2], 16).map_err(|_| ERROR.into())).collect()
}

fn request(value: Value) -> Result<Value, String> {
    let object = value.as_object().ok_or(ERROR)?;
    let op = value["op"].as_str().ok_or(ERROR)?;
    let service = value["service"].as_str().ok_or(ERROR)?;
    let account = value["account"].as_str().ok_or(ERROR)?;
    if !namespace(service, account) || object.len() != if op == "write" { 4 } else { 3 } { return Err(ERROR.into()); }
    match op {
        "read" => Ok(match read(service, account)? { None => json!({"state":"missing"}), Some(data) if data.is_empty() => json!({"state":"deleted"}), Some(data) => json!({"state":"found","data":hex(&data)}) }),
        "write" => { write(service, account, &unhex(value["data"].as_str().ok_or(ERROR)?)?)?; Ok(json!({"ok":true})) },
        // Keep an explicit tombstone so an interrupted sign-out cannot resurrect a legacy session.
        "delete" => { write(service, account, &[])?; Ok(json!({"ok":true})) },
        _ => Err(ERROR.into()),
    }
}

pub struct Endpoint { parent: UnixStream, child: UnixStream }
pub fn configure(command: &mut Command, python: &Path, script: &Path) -> Result<Option<Endpoint>, String> {
    if !signed()? { return Ok(None); }
    let executable = std::env::current_exe().map_err(|_| ERROR)?;
    let contents = executable.parent().and_then(Path::parent).ok_or(ERROR)?;
    own_host(contents, python, script)?;
    let (parent, child) = UnixStream::pair().map_err(|_| ERROR)?;
    let fd = child.as_raw_fd();
    command.env("DERVO_KEYCHAIN_FD", fd.to_string());
    unsafe { command.pre_exec(move || {
        let flags = fcntl(fd, 1);
        if flags < 0 || fcntl(fd, 2, flags & !1) < 0 { return Err(std::io::Error::last_os_error()); }
        Ok(())
    }); }
    Ok(Some(Endpoint { parent, child }))
}
/// The keychain is served only to a host this app started from its own files: Contents/Resources/backend/host.py on the
/// Python 3.13 the app carries (launch::BUNDLED_PYTHON). Links are resolved before comparing, so a link to either is the
/// same file and a different file with the same name isn't.
fn own_host(contents: &Path, python: &Path, script: &Path) -> Result<(), String> {
    let same = |given: &Path, expected: &Path| -> Result<bool, String> {
        Ok(given.canonicalize().map_err(|_| ERROR)? == expected.canonicalize().map_err(|_| ERROR)?)
    };
    if !same(script, &contents.join("Resources/backend/host.py"))? || !same(python, &crate::launch::bundled_python(contents))? {
        return Err("Signed Dervo uses only its bundled host and its own Python.".into());
    }
    Ok(())
}
impl Endpoint {
    pub fn serve(self) {
        drop(self.child);
        std::thread::spawn(move || {
            let mut stream = self.parent;
            let reader_stream = match stream.try_clone() { Ok(s) => s, Err(_) => return };
            let mut reader = BufReader::new(reader_stream);
            loop {
                let mut line = Vec::new();
                use std::io::Read;
                if (&mut reader).take((WIRE_LIMIT + 1) as u64).read_until(b'\n', &mut line).unwrap_or(0) == 0 { break; }
                if line.len() > WIRE_LIMIT || line.last() != Some(&b'\n') { break; }
                let result = serde_json::from_slice(&line).map_err(|_| ERROR.to_string()).and_then(request);
                let reply = result.unwrap_or_else(|_| json!({"error":ERROR}));
                if writeln!(stream, "{reply}").and_then(|_| stream.flush()).is_err() { break; }
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn only_owned_namespaces_and_exact_accounts_are_accepted() {
        let account = "a".repeat(64);
        assert!(namespace("com.dervo.account.v2.abcdefghijklmnopqrst.private-beta", &account));
        for service in ["com.dervo.account.v1.abcdefghijklmnopqrst.private-beta", "com.dervo.account.v2.abcdefghijklmnopqrst.private-beta.extra", "com.dervo.openrouter.v2.abcdefghijklmnopqrst.private-beta", "com.apple.password"] { assert!(!namespace(service, &account)); }
        assert!(!namespace("com.dervo.account.v2.abcdefghijklmnopqrst.private-beta", "other"));
    }
    #[test] fn binary_encoding_is_bounded_and_round_trips() {
        assert_eq!(unhex(&hex(&[0, 1, 128, 255])).unwrap(), vec![0, 1, 128, 255]);
        for value in ["", "0", "xx", "é"] { assert!(unhex(value).is_err()); }
        assert!(unhex(&"00".repeat(LIMIT + 1)).is_err());
    }
    #[test] fn native_record_states_are_unambiguous_and_bounded() {
        for value in [b"".as_slice(), b"secret".as_slice(), &[0], &[1], &[0, 1, 0]] {
            let encoded = encode_record(value).unwrap();
            assert!(!encoded.is_empty());
            assert_eq!(decode_record(&encoded).unwrap(), value);
        }
        for invalid in [b"".as_slice(), &[1], &[0, 1], &[2, 1]] { assert!(decode_record(invalid).is_err()); }
        assert!(encode_record(&vec![42; LIMIT + 1]).is_err());
        assert!(decode_record(&vec![1; LIMIT + 2]).is_err());
        assert_eq!(decode_record(&encode_record(&vec![42; LIMIT]).unwrap()).unwrap().len(), LIMIT);
    }
    #[test]
    #[ignore = "Writes and removes only fixed-namespace disposable macOS Keychain fixtures"]
    fn disposable_native_round_trip() {
        let service = "com.dervo.account.v2.zzzzzzzzzzzzzzzzzzzz.private-beta";
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let account = format!("{:024x}{:032x}{:08x}", 0, stamp, std::process::id());
        let result = (|| -> Result<(), String> {
            for (stage, value) in [("create", b"Disposable native unit fixture".as_slice()), ("rotate", b"Rotated fixture".as_slice()), ("tombstone", b"".as_slice())] {
                write(service, &account, value).map_err(|e| format!("{stage} write: {e}"))?;
                let actual = read(service, &account).map_err(|e| format!("{stage} read: {e}"))?;
                if actual.as_deref() != Some(value) {
                    return Err(format!("{stage}: expected {} bytes, found {:?}", value.len(), actual.as_ref().map(Vec::len)));
                }
            }
            Ok(())
        })();
        extern "C" { fn SecItemDelete(query: Ref) -> i32; }
        let cleanup = unsafe { SecItemDelete(query(service, &account).unwrap().0) };
        assert!(cleanup == 0 || cleanup == -25300, "Disposable cleanup failed: {cleanup}");
        assert!(result.is_ok(), "{}", result.unwrap_err());
    }
    #[test] fn keychain_serves_only_the_apps_own_host_on_its_own_python() {
        let root = std::env::temp_dir().join(format!("dervo keychain host {}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let contents = root.join("Dervo Local.app/Contents");
        let file = |path: &Path| { std::fs::create_dir_all(path.parent().unwrap()).unwrap(); std::fs::write(path, "").unwrap(); path.to_path_buf() };
        let host = file(&contents.join("Resources/backend/host.py"));
        let own = file(&crate::launch::bundled_python(&contents));
        let framework = file(&root.join("Library/Frameworks/Python.framework/Versions/3.13/bin/python3"));
        let other_host = file(&root.join("elsewhere/backend/host.py"));
        let link = root.join("python link");
        std::os::unix::fs::symlink(&own, &link).unwrap();
        let refused = [(framework, host.clone()), (own.clone(), other_host), (root.join("missing/python3"), host.clone())]
            .iter().filter(|(python, script)| own_host(&contents, python, script).is_ok()).count();
        let accepted = [own_host(&contents, &own, &host), own_host(&contents, &link, &host)];
        let _ = std::fs::remove_dir_all(&root);
        assert_eq!(refused, 0, "the framework, another host.py or a missing Python must be refused");
        assert!(accepted.iter().all(Result::is_ok), "the app's own Python, or a link to it, is served: {accepted:?}");
    }
    #[test] fn malformed_requests_never_reach_keychain() {
        for value in [json!({}), json!({"op":"read","service":"com.apple.password","account":"a"}), json!({"op":"shell","service":"com.dervo.account.v2.abcdefghijklmnopqrst.private-beta","account":"a".repeat(64)})] { assert!(request(value).is_err()); }
    }
}

/// A fixed disposable namespace, no session values or caller-selected service names.
pub fn self_check() -> Result<(), String> {
    if !signed().map_err(|e| format!("Verify native identity: {e}"))? { return Err("The Keychain self-check needs a verified Developer ID build.".into()); }
    let service = "com.dervo.account.v2.zzzzzzzzzzzzzzzzzzzz.private-beta";
    let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|_| ERROR)?.as_nanos();
    let account = format!("{:024x}{:032x}{:08x}", 0, stamp, std::process::id());
    extern "C" { fn SecItemDelete(query: Ref) -> i32; }
    let result = (|| {
        if read(service, &account).map_err(|e| format!("Initial absence check: {e}"))?.is_some() { return Err("Initial absence check: disposable item already exists.".into()); }
        write(service, &account, b"Dervo disposable native access check").map_err(|e| format!("Create disposable item: {e}"))?;
        if read(service, &account).map_err(|e| format!("Read created item: {e}"))?.as_deref() != Some(b"Dervo disposable native access check".as_slice()) { return Err("Read created item: the disposable value is missing or different.".into()); }
        let exe = std::env::current_exe().map_err(|_| ERROR)?;
        let script = exe.parent().and_then(Path::parent).ok_or(ERROR)?.join("Resources/backend/keychain_canary.py");
        let python = crate::launch::resolve_app_python(None)?;
        let denied = Command::new(python).args(["-E", "-s", "-B"]).arg(script).arg(&account)
            .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
            .status().map_err(|_| ERROR)?;
        match denied.code() {
            Some(0) | Some(3) => {},
            Some(5) => return Err("Python could read the disposable native Keychain item.".into()),
            other => return Err(format!("The Python Keychain check couldn't complete (exit {other:?}).")),
        }
        // macOS can hide inaccessible items from a query. Confirm the item still
        // exists and is readable by its native owner before accepting that result.
        if read(service, &account).map_err(|e| format!("Read after Python denial: {e}"))?.as_deref() != Some(b"Dervo disposable native access check".as_slice()) {
            return Err("The disposable native Keychain item disappeared during the access check.".into());
        }
        write(service, &account, b"Rotated disposable value").map_err(|e| format!("Rotate disposable item: {e}"))?;
        if read(service, &account).map_err(|e| format!("Read rotated item: {e}"))?.as_deref() != Some(b"Rotated disposable value".as_slice()) { return Err("Read rotated item: the disposable value is missing or different.".into()); }
        write(service, &account, &[]).map_err(|e| format!("Write disposable tombstone: {e}"))?;
        if read(service, &account).map_err(|e| format!("Read tombstone: {e}"))? != Some(Vec::new()) { return Err("Read tombstone: the disposable value is missing or nonempty.".into()); }
        Ok(())
    })();
    let status = unsafe { SecItemDelete(query(service, &account)?.0) };
    if status != 0 && status != -25300 { return Err("The disposable Keychain check couldn't remove its test item.".into()); }
    result
}
