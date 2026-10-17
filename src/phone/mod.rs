//! Dervo on your phone, Mac side, in the app's own process (b19/phone-link). It used to run in the window
//! (companion/mac/link.ts, PR #11); here it keeps working while the window is closed, and its keys live in the Keychain.
//!
//! One thread owns everything: the relay socket, the link's state machine (`link.rs`) and its timers. The window talks
//! to it through the commands at the bottom, and hears about changes on the `dervo-phone-link` event.
pub mod link;
pub mod proto;
#[cfg(test)]
mod tests;

use link::{Host, Link, LinkState, Store};
use serde_json::{json, Value};
use std::{net::{TcpStream, ToSocketAddrs}, path::PathBuf, sync::{mpsc, Arc, Mutex}, time::Duration};
use tungstenite::{stream::MaybeTlsStream, Message, WebSocket};

pub const EVENT: &str = "dervo-phone-link";
const PING_MS: i64 = 25_000;
const PONG_MS: i64 = 10_000;
const MAX_BACKOFF_MS: i64 = 30_000;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
/// How long one read waits before the loop looks at its timers and the window's commands.
const READ_WAIT: Duration = Duration::from_millis(500);

pub fn now_ms() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

// ---------- what survives a restart ----------

/// Where the secret key and room key go. The Keychain on a signed Mac build with the everyday data folder; a file
/// beside the state, readable only by you, everywhere else (an unsigned build, a disposable DERVO_DATA_DIR, Windows).
pub trait Secrets: Send {
    fn read(&self) -> Result<Option<Value>, String>;
    fn write(&self, secrets: &Value) -> Result<(), String>;
}

pub struct FileSecrets(pub PathBuf);
impl Secrets for FileSecrets {
    fn read(&self) -> Result<Option<Value>, String> { read_json(&self.0) }
    fn write(&self, secrets: &Value) -> Result<(), String> { write_private(&self.0, secrets.to_string().as_bytes()) }
}

/// One Keychain item per data folder (`account`), so a beta and a stable Dervo on one Mac each keep their own pairing.
#[cfg(target_os = "macos")]
pub struct KeychainSecrets(String);
#[cfg(target_os = "macos")]
impl Secrets for KeychainSecrets {
    fn read(&self) -> Result<Option<Value>, String> {
        crate::keychain::phone_link_read(&self.0)?.map(|bytes| serde_json::from_slice(&bytes).map_err(|e| e.to_string())).transpose()
    }
    fn write(&self, secrets: &Value) -> Result<(), String> { crate::keychain::phone_link_write(&self.0, secrets.to_string().as_bytes()) }
}

fn read_json(path: &PathBuf) -> Result<Option<Value>, String> {
    match std::fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(|e| format!("{}: {e}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

/// Written to a temporary file first and renamed, so a crash mid-write never leaves half a file. Only you can read it.
fn write_private(path: &PathBuf, bytes: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let temp = path.with_extension("tmp");
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
    let mut file = options.open(&temp).map_err(|e| e.to_string())?;
    // A temporary file left from before keeps its old mode through `open`, so it's set here too.
    #[cfg(unix)]
    { use std::os::unix::fs::PermissionsExt; file.set_permissions(std::fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?; }
    file.write_all(bytes).and_then(|_| file.sync_all()).map_err(|e| e.to_string())?;
    std::fs::rename(&temp, path).map_err(|e| e.to_string())
}

/// The link's state in `phone-link.json` in Dervo's data folder, without the two secrets, which go to `Secrets` and
/// are written only when they change.
pub struct FileStore { path: PathBuf, secrets: Box<dyn Secrets>, written: Mutex<Option<Value>> }
impl FileStore {
    pub fn new(path: PathBuf, secrets: Box<dyn Secrets>) -> Self { Self { path, secrets, written: Mutex::new(None) } }

    /// The saved state, or None when there's none or its secrets are missing or don't match its public key (a fresh
    /// Keychain, say). Then the window sets the relay again and you pair again.
    pub fn load(&self) -> Result<Option<LinkState>, String> {
        let Some(mut saved) = read_json(&self.path)? else { return Ok(None) };
        let Some(secrets) = self.secrets.read()? else { return Ok(None) };
        if secrets["publicKey"] != saved["keys"]["publicKey"] { return Ok(None); }
        saved["keys"]["secretKey"] = secrets["secretKey"].clone();
        saved["roomKey"] = secrets["roomKey"].clone();
        let state = serde_json::from_value(saved).map_err(|e| e.to_string())?;
        *self.written.lock().unwrap() = Some(secrets);
        Ok(Some(state))
    }

    pub fn clear(&self) -> Result<(), String> {
        match std::fs::remove_file(&self.path) {
            Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error.to_string()),
            _ => Ok(()),
        }
    }
}
impl Store for FileStore {
    fn save(&self, state: &LinkState) -> Result<(), String> {
        let secrets = json!({"publicKey": state.keys.public_key, "secretKey": state.keys.secret_key, "roomKey": state.room_key});
        let mut written = self.written.lock().map_err(|_| "The phone link's store is unavailable")?;
        if written.as_ref() != Some(&secrets) {
            self.secrets.write(&secrets)?;
            *written = Some(secrets);
        }
        let mut public = serde_json::to_value(state).map_err(|e| e.to_string())?;
        public["keys"]["secretKey"] = Value::Null;
        public["roomKey"] = Value::Null;
        write_private(&self.path, public.to_string().as_bytes())
    }
}

// ---------- the relay socket ----------

type Socket = WebSocket<MaybeTlsStream<TcpStream>>;

fn tls() -> Arc<rustls::ClientConfig> {
    let roots = rustls::RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() };
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    Arc::new(rustls::ClientConfig::builder_with_provider(provider).with_safe_default_protocol_versions().expect("TLS 1.2 and 1.3")
        .with_root_certificates(roots).with_no_client_auth())
}

fn dial(url: &str) -> Result<Socket, String> {
    use tungstenite::client::IntoClientRequest;
    let request = url.into_client_request().map_err(|e| e.to_string())?;
    let uri = request.uri();
    let host = uri.host().ok_or("The relay address has no host")?.trim_start_matches('[').trim_end_matches(']').to_owned();
    let port = uri.port_u16().unwrap_or(if uri.scheme_str() == Some("wss") { 443 } else { 80 });
    // Each address in turn (IPv6 and IPv4, say), so one that doesn't answer doesn't stop the rest.
    let mut last = "The relay's address didn't resolve".to_owned();
    let mut tcp = None;
    for address in (host.as_str(), port).to_socket_addrs().map_err(|e| e.to_string())? {
        match TcpStream::connect_timeout(&address, CONNECT_TIMEOUT) { Ok(stream) => { tcp = Some(stream); break; } Err(error) => last = error.to_string() }
    }
    let tcp = tcp.ok_or(last)?;
    tcp.set_read_timeout(Some(CONNECT_TIMEOUT)).and_then(|_| tcp.set_write_timeout(Some(CONNECT_TIMEOUT))).map_err(|e| e.to_string())?;
    tcp.set_nodelay(true).ok();
    let config = tungstenite::protocol::WebSocketConfig { max_message_size: Some(1024 * 1024), max_frame_size: Some(1024 * 1024), ..Default::default() };
    let (socket, _) = tungstenite::client_tls_with_config(request, tcp, Some(config), Some(tungstenite::Connector::Rustls(tls())))
        .map_err(|e| e.to_string())?;
    let tcp = match socket.get_ref() { MaybeTlsStream::Plain(s) => s, MaybeTlsStream::Rustls(s) => &s.sock, _ => return Err("Unexpected stream".into()) };
    tcp.set_read_timeout(Some(READ_WAIT)).map_err(|e| e.to_string())?;
    Ok(socket)
}

/// The link plus its socket and timers. `step` does one round: read what arrived, run timers, send what's waiting.
pub struct Driver<H: Host, S: Store> {
    pub link: Link<H, S>,
    socket: Option<Socket>,
    backoff: i64,
    redial_at: i64,
    next_ping: i64,
    pong_due: Option<i64>,
}

impl<H: Host, S: Store> Driver<H, S> {
    pub fn new(link: Link<H, S>) -> Self { Self { link, socket: None, backoff: 1000, redial_at: 0, next_ping: 0, pong_due: None } }

    pub fn connected(&self) -> bool { self.link.ready }

    fn drop_socket(&mut self, why: &str) {
        if let Some(mut socket) = self.socket.take() { let _ = socket.close(None); let _ = socket.flush(); }
        self.link.lost();
        self.pong_due = None;
        // Up to half again as long, so many Macs dropped at once don't all come back in step.
        let jitter = {
            use crypto_box::aead::{rand_core::RngCore, OsRng};
            (OsRng.next_u32() % 1000) as i64 * self.backoff / 2000
        };
        self.redial_at = now_ms() + self.backoff + jitter;
        self.backoff = (self.backoff * 2).min(MAX_BACKOFF_MS);
        if !why.is_empty() { eprintln!("Dervo phone link: {why}"); }
    }

    /// Sends what the link queued. A failed write drops the socket; the relay still holds anything queued for later.
    fn flush(&mut self) {
        let frames: Vec<String> = self.link.out.drain(..).collect();
        let Some(socket) = self.socket.as_mut() else { return };
        for frame in frames {
            if let Err(error) = socket.write(Message::text(frame)) { self.drop_socket(&format!("send failed: {error}")); return; }
        }
        if let Err(error) = socket.flush() {
            if !matches!(&error, tungstenite::Error::Io(e) if e.kind() == std::io::ErrorKind::WouldBlock) { self.drop_socket(&format!("send failed: {error}")); }
        }
    }

    pub fn step(&mut self) {
        let now = now_ms();
        if self.link.halted.is_some() {
            if self.socket.is_some() { self.drop_socket(""); }
            std::thread::sleep(READ_WAIT);
            return;
        }
        if self.socket.is_none() {
            if now < self.redial_at { std::thread::sleep(READ_WAIT); return; }
            match dial(&format!("{}/v1/rooms/{}", self.link.state.relay, self.link.state.room)) {
                Ok(socket) => { self.socket = Some(socket); self.next_ping = now + PING_MS; }
                Err(error) => { self.drop_socket(&format!("couldn't reach the relay: {error}")); return; }
            }
        }
        match self.socket.as_mut().unwrap().read() {
            Ok(Message::Text(text)) => {
                let was_ready = self.link.ready;
                self.link.on_frame(&text);
                if std::mem::take(&mut self.link.ponged) { self.pong_due = None; }
                if std::mem::take(&mut self.link.refuse) { self.drop_socket("closed a socket whose relay asked for a proof it mustn't get"); return; }
                if !was_ready && self.link.ready { self.backoff = 1000; }
            }
            Ok(Message::Close(_)) => { self.drop_socket("the relay closed the connection"); return; }
            Ok(_) => {}
            Err(tungstenite::Error::Io(error)) if matches!(error.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
            Err(error) => { self.drop_socket(&format!("connection lost: {error}")); return; }
        }
        // A socket left open by sleep can look connected while nothing gets through: a ping every 25 s, and no pong in
        // 10 s closes it. The reconnect brings back what the relay held.
        let now = now_ms();
        if self.pong_due.is_some_and(|due| now >= due) { self.drop_socket("no answer from the relay"); return; }
        if self.link.ready && now >= self.next_ping {
            self.link.out.push(json!({"t": "ping"}).to_string());
            self.next_ping = now + PING_MS;
            if self.pong_due.is_none() { self.pong_due = Some(now + PONG_MS); }
        }
        self.link.tick();
        self.flush();
    }

    /// Before the socket goes: whatever the link queued last (a sealed removal) still goes out.
    pub fn close(mut self) {
        self.flush();
        if let Some(mut socket) = self.socket.take() { let _ = socket.close(None); let _ = socket.flush(); }
    }
}

// ---------- in the app ----------

/// The host as the window reaches it, through the same managed `startup::Host`. Every call waits its turn on the
/// host's one pipe, as the window's own requests do.
pub struct AppHost(pub tauri::AppHandle);
impl Host for AppHost {
    fn call(&self, operation: &str, args: Value) -> Result<Value, String> {
        use tauri::Manager;
        self.0.state::<crate::startup::Host>().call(operation, args)
    }
}

enum Command {
    SetRelay(Option<String>, mpsc::Sender<Result<(), String>>),
    Offer(mpsc::Sender<Result<Value, String>>),
    Allow(String, mpsc::Sender<bool>),
    Approvals(String, bool, mpsc::Sender<bool>),
    Deny,
    Unpair(String),
    #[allow(dead_code)]
    Nudge,
}

/// What the window shows: the relay, whether it's connected, the paired phones and the phone asking to pair.
#[derive(Clone, serde::Serialize, Default, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Status { pub relay: Option<String>, pub connected: bool, pub problem: Option<String>, pub phones: Vec<link::Phone>, pub waiting: Option<link::Asking> }

pub struct PhoneLink { commands: mpsc::Sender<Command>, status: Arc<Mutex<Status>> }

impl PhoneLink {
    pub fn status(&self) -> Status { self.status.lock().map(|s| s.clone()).unwrap_or_default() }
    pub fn has_phones(&self) -> bool { !self.status().phones.is_empty() }
    /// Read Home now: the host's state changed. For the state spine's push (b19/state-spine), which mvp1 doesn't have yet.
    #[allow(dead_code)]
    pub fn nudge(&self) { let _ = self.commands.send(Command::Nudge); }

    pub fn start(app: tauri::AppHandle, data: PathBuf, keychain: bool) -> Self {
        let (commands, inbox) = mpsc::channel();
        let status = Arc::new(Mutex::new(Status::default()));
        let shared = status.clone();
        std::thread::Builder::new().name("dervo-phone-link".into()).spawn(move || run(app, data, keychain, inbox, shared)).expect("the phone link's thread");
        Self { commands, status }
    }
}

fn secrets(data: &std::path::Path, keychain: bool) -> Box<dyn Secrets> {
    #[cfg(target_os = "macos")]
    if keychain {
        use sha2::Digest;
        let account: String = sha2::Sha256::digest(data.to_string_lossy().as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
        return Box::new(KeychainSecrets(account));
    }
    let _ = keychain;
    Box::new(FileSecrets(data.join("phone-link-keys.json")))
}

fn mac_name() -> String {
    let name = std::process::Command::new("scutil").args(["--get", "ComputerName"]).output().ok()
        .filter(|o| o.status.success()).and_then(|o| String::from_utf8(o.stdout).ok()).map(|s| s.trim().to_owned()).filter(|s| !s.is_empty());
    name.unwrap_or_else(|| "Your Mac".into()).chars().take(80).collect()
}

fn run(app: tauri::AppHandle, data: PathBuf, keychain: bool, inbox: mpsc::Receiver<Command>, status: Arc<Mutex<Status>>) {
    use tauri::Emitter;
    let store = || FileStore::new(data.join("phone-link.json"), secrets(&data, keychain));
    let mac = mac_name();
    let first = store();
    let saved = first.load().unwrap_or_else(|error| { eprintln!("Dervo phone link: couldn't read its saved state: {error}"); None });
    let start = |state: LinkState, store: FileStore| Driver::new(Link::new(AppHost(app.clone()), store, state, &mac, Box::new(now_ms)));
    let mut driver = saved.map(|state| start(state, first));
    let mut shown = Status::default();
    loop {
        // The window's commands first, then one round on the socket.
        loop {
            let command = if driver.is_some() { inbox.try_recv().ok() } else { inbox.recv_timeout(Duration::from_secs(1)).ok() };
            let Some(command) = command else { break };
            match command {
                Command::SetRelay(relay, reply) => {
                    if relay.as_deref().is_some_and(|r| !proto::safe_relay(r)) { let _ = reply.send(Err("Use a wss:// relay address.".into())); continue; }
                    if driver.as_ref().map(|d| d.link.state.relay.as_str()) == relay.as_deref() { let _ = reply.send(Ok(())); continue; }
                    // Every phone hears it's removed, held by the old room for phones that are away.
                    if let Some(mut old) = driver.take() { old.link.unpair_all(); old.close(); }
                    let store = store();
                    let result = match relay {
                        Some(relay) => { let state = LinkState::new(&relay); driver = Some(start(state, store)); Ok(()) }
                        None => store.clear(),
                    };
                    let _ = reply.send(result);
                }
                Command::Offer(reply) => { let _ = reply.send(driver.as_mut().map(|d| d.link.pairing_offer()).ok_or_else(|| "Set the relay first.".to_owned())); }
                Command::Allow(pk, reply) => { let _ = reply.send(driver.as_mut().is_some_and(|d| d.link.allow_phone(&pk))); }
                Command::Approvals(pk, on, reply) => {
                    let set = driver.as_mut().is_some_and(|d| { let set = d.link.set_approvals(&pk, on); d.flush(); set });
                    let _ = reply.send(set);
                }
                Command::Deny => if let Some(d) = driver.as_mut() { d.link.deny_phone(); },
                Command::Unpair(pk) => if let Some(d) = driver.as_mut() { d.link.unpair(&pk); },
                Command::Nudge => if let Some(d) = driver.as_mut() { d.link.nudge(); },
            }
        }
        if let Some(d) = driver.as_mut() { d.step(); }
        let now = match driver.as_mut() {
            Some(d) => Status { relay: Some(d.link.state.relay.clone()), connected: d.connected(), problem: d.link.halted.clone(), phones: d.link.phones().to_vec(), waiting: d.link.waiting() },
            None => Status::default(),
        };
        if now != shown {
            shown = now.clone();
            keep_awake(!now.phones.is_empty());
            if let Ok(mut status) = status.lock() { *status = now.clone(); }
            let _ = app.emit_to("main", EVENT, now);
        }
    }
}

/// While a phone is paired, macOS mustn't nap Dervo: the link's timers would stretch to minutes and Home would go stale.
/// Idle system sleep is still allowed.
#[cfg(target_os = "macos")]
fn keep_awake(on: bool) {
    use objc2::{msg_send, rc::Retained, runtime::{AnyClass, AnyObject}};
    use objc2_foundation::NSString;
    thread_local! { static ACTIVITY: std::cell::RefCell<Option<Retained<AnyObject>>> = const { std::cell::RefCell::new(None) }; }
    // NSActivityUserInitiatedAllowingIdleSystemSleep
    const OPTIONS: u64 = 0x00FF_FFFF & !(1 << 20);
    ACTIVITY.with(|activity| {
        let mut activity = activity.borrow_mut();
        let Some(class) = AnyClass::get(c"NSProcessInfo") else { return };
        let info: Retained<AnyObject> = unsafe { msg_send![class, processInfo] };
        match (on, activity.take()) {
            (true, None) => {
                let reason = NSString::from_str("Dervo keeps your phone up to date");
                *activity = Some(unsafe { msg_send![&*info, beginActivityWithOptions: OPTIONS, reason: &*reason] });
            }
            (false, Some(token)) => unsafe { let _: () = msg_send![&*info, endActivity: &*token]; },
            (_, token) => *activity = token,
        }
    });
}
#[cfg(not(target_os = "macos"))]
fn keep_awake(_on: bool) {}

// ---------- the window's commands ----------

fn ask<T>(state: &PhoneLink, make: impl FnOnce(mpsc::Sender<T>) -> Command) -> Result<T, String> {
    let (reply, answer) = mpsc::channel();
    state.commands.send(make(reply)).map_err(|_| "Dervo's phone link stopped. Quit and reopen Dervo.")?;
    answer.recv_timeout(Duration::from_secs(20)).map_err(|_| "Dervo's phone link didn't answer. Try again.".into())
}

#[tauri::command]
pub fn phone_link_status(state: tauri::State<'_, PhoneLink>) -> Status { state.status() }
#[tauri::command]
pub async fn phone_link_set_relay(state: tauri::State<'_, PhoneLink>, relay: Option<String>) -> Result<Status, String> {
    ask(&state, |reply| Command::SetRelay(relay.map(|r| r.trim().trim_end_matches('/').to_owned()).filter(|r| !r.is_empty()), reply))??;
    Ok(state.status())
}
#[tauri::command]
pub async fn phone_pairing_offer(state: tauri::State<'_, PhoneLink>) -> Result<Value, String> { ask(&state, Command::Offer)? }
#[tauri::command]
pub async fn phone_allow(state: tauri::State<'_, PhoneLink>, pk: String) -> Result<bool, String> { ask(&state, |reply| Command::Allow(pk, reply)) }
/// Let a paired phone approve scopes and accept results, or stop it (Settings, Phone). False when it isn't paired.
#[tauri::command]
pub async fn phone_set_approvals(state: tauri::State<'_, PhoneLink>, pk: String, on: bool) -> Result<bool, String> { ask(&state, |reply| Command::Approvals(pk, on, reply)) }
#[tauri::command]
pub fn phone_deny(state: tauri::State<'_, PhoneLink>) { let _ = state.commands.send(Command::Deny); }
#[tauri::command]
pub fn phone_unpair(state: tauri::State<'_, PhoneLink>, pk: String) { let _ = state.commands.send(Command::Unpair(pk)); }
