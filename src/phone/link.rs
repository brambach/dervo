//! The Mac side of Dervo on your phone, as companion/mac/link.ts does it (PR #11), moved out of the window so it runs
//! while the window is closed. It reads Home through the host's own operations, seals it for each paired phone, and runs
//! the few things a phone may ask for (send to a thread, message Home, stop a thread's runs) through those same
//! operations. This file is the state machine only: frames in, frames out, a clock and a host. `mod.rs` owns the socket.
use super::proto::{self, Action, FromRelay, KeyPair, PhoneMessage, Push, FIT_BYTES, HOME_CHANNEL};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};

pub const PAIRING_MS: i64 = 10 * 60_000;
pub const SEEN_MS: i64 = proto::SEND_TTL_MS + 3600_000;
const CLOCK_SKEW_MS: i64 = 5 * 60_000;
/// An unchanged Home goes at most this often, live only, while a phone is open.
pub const HEARTBEAT_MS: i64 = 30_000;
/// A phone that said anything within this long is open on someone's screen. The phone says hello every 50 s.
pub const ACTIVE_MS: i64 = 90_000;
/// How often Home is read while no phone is open: often enough to wake a phone when a thread starts waiting on you.
pub const IDLE_POLL_MS: i64 = 30_000;
pub const POLL_MS: i64 = 2000;
/// The fallback path's lost runs (`status`) are read at most this often.
const STATUS_MS: i64 = 30_000;
/// The relay keeps at most 20 phones on a room's list, and refuses a longer one.
pub const MAX_PHONES: usize = 20;
/// Envelope nonces and action answers kept at most, oldest dropped first, so a chatty phone can't grow the file forever.
const MAX_SEEN: usize = 5000;
/// A change only the receipt depends on (a hello's nonce, say) is saved at most this often.
const SAVE_MS: i64 = 1000;
/// Keys whose proof the relay may not ask for: each recent sender stays refused this long after its last envelope.
const MAX_SENDERS: usize = 64;
/// A result's `task_detail` is kept at most this long while its task looks the same (home.ts detailKey).
const DETAIL_MS: i64 = 60_000;
const CANT_APPROVE: &str = "This phone can't approve yet. On your Mac, open Dervo, then Settings, Phone, and turn on approvals for it.";

pub trait Host { fn call(&self, operation: &str, args: Value) -> Result<Value, String>; }
pub trait Store { fn save(&self, state: &LinkState) -> Result<(), String>; }

#[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Phone { pub pk: String, pub device: String, pub paired_at: i64,
    /// You let this phone approve scopes and accept results (Settings, Phone, on the Mac). Off until you turn it on.
    #[serde(default)]
    pub approvals: bool }
#[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
pub struct Pairing { pub code: String, pub expires: i64 }
#[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
pub struct Done { pub at: i64, pub state: String, pub detail: String }

/// What survives a restart. The secret key and room key go to the Keychain (`mod.rs`), the rest to a file.
#[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
#[serde(rename_all = "camelCase")]
pub struct LinkState {
    pub keys: KeyPair, pub room: String, pub room_key: String, pub relay: String, pub phones: Vec<Phone>,
    pub pairing: Option<Pairing>,
    /// Envelope nonces and `${phonePk}:${actionId}`s already handled, kept for SEEN_MS.
    pub seen: BTreeMap<String, i64>, pub done: BTreeMap<String, Done>,
    /// Removed phones still to be told, when the relay wasn't reachable at the time. Sent after the next ready.
    #[serde(default)]
    pub notices: Vec<String>,
    /// Actions started on the host and not yet recorded as done, by key, with when. One still here after a restart may
    /// or may not have run, so it's answered as unknown and never run again.
    #[serde(default)]
    pub running: BTreeMap<String, i64>,
}
impl LinkState {
    pub fn new(relay: &str) -> Self {
        Self { keys: KeyPair::generate(), room: proto::random_token(18), room_key: proto::random_token(32), relay: relay.into(),
            phones: Vec::new(), pairing: None, seen: BTreeMap::new(), done: BTreeMap::new(), notices: Vec::new(), running: BTreeMap::new() }
    }
}

/// A phone that sent the right code and waits for you to allow it on the Mac. `digits` show on both screens.
#[derive(Clone, Serialize, PartialEq, Debug)]
pub struct Asking { pub pk: String, pub device: String, pub digits: String, pub expires: i64 }

pub struct Link<H: Host, S: Store> {
    pub host: H, store: S, pub state: LinkState, mac: String, now: Box<dyn Fn() -> i64 + Send>,
    /// Frames for the relay, in order. The socket owner sends them.
    pub out: Vec<String>,
    pub ready: bool,
    /// Set when the relay says another connection owns this room; this one stops instead of fighting it.
    pub halted: Option<String>,
    /// Set whenever something the window shows changed (phones, the asking phone, connection).
    pub changed: bool,
    pub poll_ms: i64,
    /// Set when the relay answered a ping. The socket owner clears it.
    pub ponged: bool,
    /// Set when this socket must close without another word (a challenge it mustn't answer). The socket owner clears it.
    pub refuse: bool,
    /// This socket's sign-in answer went out. One per socket, before ready (security review, 8 Oct).
    answered: bool,
    /// Senders of recent envelopes, whose proof is never given (see `on_frame`).
    senders: Vec<String>,
    unsaved: bool,
    saved_at: i64,
    projects_read: std::cell::RefCell<Option<(i64, Value)>>,
    asking: Option<Asking>, last_home: String, last_needs: HashSet<String>,
    /// Threads finished at the last Home sent, or None before the first one, when every finished thread is "new".
    last_finished: Option<HashSet<String>>,
    /// `task_detail` for results waiting on review, by task, with the `detail_key` and time it was read: kept while the
    /// task looks the same and for at most DETAIL_MS, so an open thread doesn't read a whole result's evidence every
    /// few seconds. Accepting always reads it fresh.
    details: std::cell::RefCell<HashMap<String, (String, i64, Value)>>,
    you: String, names: HashMap<String, String>,
    /// Every thread id a Home read has listed, named or not.
    known: HashSet<String>,
    open_threads: HashMap<String, (String, String)>, last_thread: HashMap<String, String>, last_sent: i64,
    active_at: HashMap<String, i64>, lost_runs: Option<(i64, Value)>, spine: Option<bool>, next_poll: i64,
}

impl<H: Host, S: Store> Link<H, S> {
    pub fn new(host: H, store: S, state: LinkState, mac: &str, now: Box<dyn Fn() -> i64 + Send>) -> Self {
        let next_poll = now() + IDLE_POLL_MS;
        let you = host.call("status", json!({})).ok().and_then(|s| s["actor"].as_str().map(str::to_owned)).unwrap_or_else(|| "local-user".into());
        let mut link = Self { host, store, state, mac: mac.into(), now, out: Vec::new(), ready: false, halted: None, changed: true, poll_ms: POLL_MS, ponged: false, refuse: false, answered: false, senders: Vec::new(), unsaved: false, saved_at: 0, projects_read: Default::default(),
            asking: None, last_home: String::new(), last_needs: HashSet::new(), last_finished: None, details: Default::default(), you, names: HashMap::new(), known: HashSet::new(), open_threads: HashMap::new(),
            last_thread: HashMap::new(), last_sent: 0, active_at: HashMap::new(), lost_runs: None, spine: None, next_poll };
        // Older saves could hold more phones than the relay lists: the newest stay, the rest are told.
        link.state.phones.sort_by_key(|p| p.paired_at);
        while link.state.phones.len() > MAX_PHONES { let old = link.state.phones.remove(0); link.evict(&old.pk, &old.device); }
        link.persist();
        link
    }

    fn log(&self, line: &str) { eprintln!("Dervo phone link: {line}"); }

    pub fn phones(&self) -> &[Phone] { &self.state.phones }
    pub fn waiting(&mut self) -> Option<Asking> {
        if self.asking.as_ref().is_some_and(|a| a.expires < (self.now)()) { self.asking = None; }
        self.asking.clone()
    }

    // ---------- the window's controls ----------

    /// Allow the phone that's asking. Only after this can it read Home or act.
    pub fn allow_phone(&mut self, pk: &str) -> bool {
        let Some(asking) = self.waiting().filter(|a| a.pk == pk) else { return false };
        self.asking = None;
        let now = (self.now)();
        self.state.phones.retain(|p| p.pk != pk);
        self.state.phones.sort_by_key(|p| p.paired_at);
        // The relay names at most MAX_PHONES phones. Past that, the longest-paired one makes room and is told so, sealed
        // and held, before the allow that drops it.
        while self.state.phones.len() >= MAX_PHONES { let old = self.state.phones.remove(0); self.evict(&old.pk, &old.device); }
        self.state.phones.push(Phone { pk: pk.into(), device: asking.device.clone(), paired_at: now, approvals: false });
        self.mark_active(pk);
        self.persist();
        self.allow();
        // Held, so a phone that locked while you walked to the Mac still learns it's paired when it's back.
        self.seal_to(pk, json!({"t": "paired", "mac": self.mac}), Some("q"), false);
        self.last_home.clear();
        self.publish();
        self.changed = true;
        self.log(&format!("paired {}", asking.device));
        true
    }

    /// Drops a phone (already out of `phones`) to make room, and tells it, held, so it learns when it's back.
    fn evict(&mut self, pk: &str, device: &str) {
        self.forget_answers(pk);
        if self.ready { self.seal_to(pk, json!({"t": "unpaired"}), Some("q"), false); }
        else if !self.state.notices.iter().any(|n| n == pk) { self.state.notices.push(pk.into()); }
        self.open_threads.remove(pk);
        self.active_at.remove(pk);
        self.last_thread.remove(pk);
        self.log(&format!("removed {device}, the longest-paired phone, to make room for a new one"));
    }

    /// A removed phone's recorded answers go with it. Safe since actions are dated by `macAt` on this Mac's clock, and
    /// pairing again makes the phone a new key, so nothing of its can come back under these.
    fn forget_answers(&mut self, pk: &str) {
        let prefix = format!("{pk}:");
        self.state.done.retain(|key, _| !key.starts_with(&prefix));
    }

    /// Let a paired phone approve scopes and accept results, or stop it. Answers and messages never need this. Its open
    /// thread goes again at once, so its buttons match. False when that phone isn't paired.
    pub fn set_approvals(&mut self, pk: &str, on: bool) -> bool {
        let Some(phone) = self.state.phones.iter_mut().find(|p| p.pk == pk) else { return false };
        phone.approvals = on;
        self.persist();
        self.last_thread.remove(pk);
        if let Some((project_id, channel_id)) = self.open_threads.get(pk).cloned() { self.open_thread(pk, &project_id, &channel_id); }
        self.changed = true;
        true
    }

    fn approvals(&self, pk: &str) -> bool { self.state.phones.iter().any(|p| p.pk == pk && p.approvals) }

    pub fn deny_phone(&mut self) {
        if let Some(asking) = self.asking.take() { self.seal_to(&asking.pk, json!({"t": "unpaired"}), None, false); }
        self.changed = true;
    }

    /// Before the relay changes: tell every phone it's removed. Held for phones that are away. The old room's list stays
    /// as it is, so the relay keeps the sealed removal for them; telling it to drop them would delete that very message.
    pub fn unpair_all(&mut self) {
        for pk in self.state.phones.iter().map(|p| p.pk.clone()).collect::<Vec<_>>() { self.seal_to(&pk, json!({"t": "unpaired"}), Some("q"), false); }
        self.state.phones.clear();
        self.open_threads.clear();
        self.state.done.clear();
        self.persist();
        self.changed = true;
    }

    /// A fresh one-time code for the QR on the Mac. Showing a new one cancels the last.
    pub fn pairing_offer(&mut self) -> Value {
        let code = proto::random_token(32);
        let expires = (self.now)() + PAIRING_MS;
        self.state.pairing = Some(Pairing { code: code.clone(), expires });
        // A new room key with each code: only someone who sees this QR can come in to pair, and older QRs stop working.
        self.state.room_key = proto::random_token(32);
        self.persist();
        if self.ready { let frame = json!({"t": "rekey", "key": self.state.room_key}); self.send(frame); }
        let offer = json!({"v": 1, "relay": self.state.relay, "room": self.state.room, "key": self.state.room_key, "mac": self.state.keys.public_key,
            "code": code, "name": self.mac, "expires": expires});
        json!({"link": format!("dervo://pair?d={}", proto::encode(offer.to_string().as_bytes())), "offer": offer})
    }

    pub fn unpair(&mut self, pk: &str) {
        if !self.state.phones.iter().any(|p| p.pk == pk) { return; }
        self.state.phones.retain(|p| p.pk != pk);
        self.forget_answers(pk);
        // Sealed before the allow that drops it, and held by the relay for a phone that's away. Offline, it waits for
        // the next ready.
        if self.ready { self.seal_to(pk, json!({"t": "unpaired"}), Some("q"), false); }
        else if !self.state.notices.iter().any(|n| n == pk) { self.state.notices.push(pk.into()); }
        self.open_threads.remove(pk);
        self.last_thread.remove(pk);
        self.persist();
        self.allow();
        self.changed = true;
    }

    // ---------- the relay ----------

    fn send(&mut self, frame: Value) { self.out.push(frame.to_string()); }
    fn allow(&mut self) {
        if self.ready { let skip = self.state.phones.len().saturating_sub(MAX_PHONES); let phones: Vec<&str> = self.state.phones[skip..].iter().map(|p| p.pk.as_str()).collect(); let frame = json!({"t": "allow", "phones": phones}); self.send(frame); }
    }
    fn seal_to(&mut self, pk: &str, message: Value, keep: Option<&str>, wake: bool) {
        self.seal_push(pk, message, keep, if wake { Push::Wake } else { Push::None });
    }
    fn seal_push(&mut self, pk: &str, message: Value, keep: Option<&str>, push: Push) {
        if !self.ready { return; }
        match proto::seal_with(&message, &self.state.keys, pk, keep, push) {
            Ok(env) => self.send(json!({"t": "send", "env": env})),
            Err(error) => self.log(&format!("couldn't seal: {error}")),
        }
    }

    /// The socket dropped: nothing is sent until the relay says ready again.
    pub fn lost(&mut self) {
        self.answered = false;
        if self.ready { self.ready = false; self.changed = true; }
    }

    /// Frames from one socket only: the driver (`mod.rs`) reads the current socket and drops a replaced one before it
    /// dials again, so a last frame from an old socket (a "replaced" error, say) never reaches here (spec 6bd82ab item 6).
    pub fn on_frame(&mut self, raw: &str) {
        let env = match proto::read_from_relay(raw) {
            FromRelay::Challenge { pk, n } => {
                // Proof v2 (spec on PR #11): answered once per socket, before ready, and never for a key of this pairing.
                // A relay asking again, or naming one of those keys, isn't a sign-in: the socket closes unanswered.
                let ours = pk == self.state.keys.public_key || self.state.phones.iter().any(|p| p.pk == pk)
                    || self.asking.as_ref().is_some_and(|a| a.pk == pk) || self.senders.contains(&pk);
                if self.answered || self.ready || ours {
                    self.log("refused a relay challenge");
                    self.refuse = true;
                    return;
                }
                self.answered = true;
                match proto::prove(&pk, &n, &self.state.keys, "mac", &self.state.room) {
                    Ok(proof) => { let frame = json!({"t": "auth", "role": "mac", "pk": self.state.keys.public_key, "key": self.state.room_key, "proof": proof}); self.send(frame); }
                    Err(error) => { self.log(&error); self.refuse = true; }
                }
                return;
            }
            FromRelay::Error(code) => {
                self.log(&format!("relay: {code}"));
                // Another Dervo signed in to this room with this Mac's key. Stop rather than take turns.
                if code == "replaced" || code == "other_mac" { self.halted = Some(code); self.changed = true; }
                return;
            }
            FromRelay::Ready => {
                self.ready = true;
                // In this order: who's allowed, a fresh room key, then removals the relay hasn't heard yet.
                self.allow();
                let frame = json!({"t": "rekey", "key": self.state.room_key});
                self.send(frame);
                if !self.state.notices.is_empty() {
                    for pk in std::mem::take(&mut self.state.notices) { self.seal_to(&pk, json!({"t": "unpaired"}), Some("q"), false); }
                    self.persist();
                }
                self.last_home.clear(); // the relay's copy may be older than ours: send it again
                self.changed = true;
                self.publish();
                return;
            }
            // `held` is still read, but an action is dated by its `macAt` alone (spec on #17).
            FromRelay::Env { env, .. } => env,
            FromRelay::Pong => { self.ponged = true; return; }
            FromRelay::Other => return,
        };
        let (Some(n), Some(from)) = (env["n"].as_str().map(str::to_owned), env["from"].as_str().map(str::to_owned)) else { return };
        // The receipt goes after the envelope is handled and recorded, so a crash in between leaves the relay's copy.
        let got = json!({"t": "got", "n": [n]});
        if self.state.seen.contains_key(&n) {
            // A copy the relay delivers again (Dervo closed before its receipt) still gets an answer: the recorded one,
            // or "can't tell" for one that was running. Never a second run.
            if self.state.phones.iter().any(|p| p.pk == from) {
                if let Some(PhoneMessage::Action { id, mac_at, action, .. }) = proto::open(&env, &self.state.keys, &from).and_then(|m| proto::read_phone_message(&m)) {
                    let key = format!("{from}:{id}");
                    if self.state.done.contains_key(&key) || self.state.running.contains_key(&key) { self.act(&from, &id, &n, mac_at, action); }
                }
            }
            self.send(got);
            return;
        }
        let Some(message) = proto::open(&env, &self.state.keys, &from).and_then(|m| proto::read_phone_message(&m)) else { self.send(got); return };
        self.state.seen.insert(n, (self.now)());
        self.senders.retain(|pk| *pk != from);
        self.senders.push(from.clone());
        if self.senders.len() > MAX_SENDERS { self.senders.remove(0); }
        // Saved before the receipt when the message changed something that must survive (an action's answer, a
        // pairing); a hello's or an open's nonce only needs saving soon, since running one twice changes nothing.
        let lasting = matches!(message, PhoneMessage::Action { .. } | PhoneMessage::Pair { .. } | PhoneMessage::Unpair);
        if !self.state.phones.iter().any(|p| p.pk == from) {
            match message {
                PhoneMessage::Pair { code, device } => self.pair(&from, &code, &device),
                // A phone removed while it was away still thinks it's paired. Tell it, sealed, so it can trust it.
                PhoneMessage::Hello | PhoneMessage::Action { .. } | PhoneMessage::Open { .. } => self.seal_to(&from, json!({"t": "unpaired"}), None, false),
                _ => {}
            }
            if lasting { self.persist(); } else { self.unsaved = true; }
            self.send(got);
            return;
        }
        // A phone coming back after a while gets Home again even if nothing changed: its copy may be older.
        let returning = !self.active_at.contains_key(&from);
        self.mark_active(&from);
        match message {
            // A paired phone still asking to pair missed the word: say it again.
            PhoneMessage::Pair { .. } => { self.seal_to(&from, json!({"t": "paired", "mac": self.mac}), Some("q"), false); self.last_home.clear(); self.publish(); }
            PhoneMessage::Hello => { if returning { self.last_home.clear(); } self.publish(); }
            PhoneMessage::Unpair => self.unpair(&from),
            PhoneMessage::Open { project_id, channel_id } => {
                self.open_threads.insert(from.clone(), (project_id.clone(), channel_id.clone()));
                self.last_thread.remove(&from);
                self.open_thread(&from, &project_id, &channel_id);
            }
            PhoneMessage::Close => { self.open_threads.remove(&from); self.last_thread.remove(&from); }
            PhoneMessage::Action { id, mac_at, action, .. } => {
                let n = env["n"].as_str().unwrap_or_default().to_owned();
                self.act(&from, &id, &n, mac_at, action);
            }
        }
        if lasting { self.persist(); } else { self.unsaved = true; }
        self.send(got);
    }

    /// The right code starts a request you allow on the Mac. Whoever sends the code first holds it.
    fn pair(&mut self, pk: &str, code: &str, device: &str) {
        let now = (self.now)();
        if !self.state.pairing.as_ref().is_some_and(|p| p.expires >= now && p.code == code) { return; } // silent: a guess learns nothing
        self.state.pairing = None;
        self.asking = Some(Asking { pk: pk.into(), device: device.into(), digits: proto::pairing_digits(&self.state.keys.public_key, pk, code), expires: now + PAIRING_MS });
        self.persist();
        self.changed = true;
        self.log(&format!("asking to pair {device}"));
    }

    // ---------- Home ----------

    fn active(&mut self) -> bool {
        let cutoff = (self.now)() - ACTIVE_MS;
        let gone: Vec<String> = self.active_at.iter().filter(|(_, at)| **at < cutoff).map(|(pk, _)| pk.clone()).collect();
        for pk in gone { self.active_at.remove(&pk); self.open_threads.remove(&pk); self.last_thread.remove(&pk); }
        !self.active_at.is_empty()
    }

    /// Every phone message counts. A phone that just became active switches the read to the fast rate at once.
    fn mark_active(&mut self, pk: &str) {
        let was_idle = !self.active();
        let now = (self.now)();
        self.active_at.insert(pk.into(), now);
        if was_idle { self.next_poll = self.next_poll.min(now + self.poll_ms); }
    }

    /// Read Home on schedule: fast while a phone is open, slow otherwise, so a phone in a pocket costs almost nothing.
    pub fn tick(&mut self) {
        let now = (self.now)();
        if self.unsaved && now - self.saved_at >= SAVE_MS { self.persist(); }
        if now < self.next_poll { return; }
        self.publish();
        let interval = if self.active() { self.poll_ms } else { IDLE_POLL_MS };
        self.next_poll = (self.now)() + interval;
    }

    /// Read Home now instead of at the next poll (the host's state changed).
    pub fn nudge(&mut self) { if self.ready && !self.state.phones.is_empty() { self.next_poll = (self.now)(); } }

    /// Whether the Mac is keeping itself awake for the runs Home shows (keep_awake.py): ``{"enabled", "holding"}``, so the
    /// phone can say it's staying awake, or that it may sleep. Left out when the host can't say.
    fn add_awake(&self, home: &mut Value) {
        if let Ok(awake) = self.host.call("keep_awake_status", json!({})) {
            if awake["supported"] == true {
                home["awake"] = json!({"enabled": awake["enabled"] == true, "holding": awake["holding"] == true});
            }
        }
    }

    fn read_snapshot(&mut self) -> Result<Value, String> {
        let projects = self.host.call("projects", json!({}))?;
        let projects = projects.as_array().cloned().unwrap_or_default();
        let names: HashMap<String, String> = projects.iter().filter_map(|p| Some((p["id"].as_str()?.to_owned(), proto::project_name(p)))).collect();
        let now = (self.now)();
        if self.spine != Some(false) {
            match self.host.call("thread_states", json!({"since": 0})) {
                Ok(result) => {
                    self.spine = Some(true);
                    let threads = result["threads"].as_array().cloned().unwrap_or_default();
                    for t in &threads {
                        let Some(id) = t["id"].as_str() else { continue };
                        self.known.insert(id.into());
                        if let Some(name) = t["name"].as_str().filter(|n| !n.is_empty()) { self.names.insert(id.into(), name.into()); }
                    }
                    let rows = threads.iter().filter(|t| t["gone"] != true && t["archived"] != true)
                        .filter_map(|t| names.get(t["projectId"].as_str()?).map(|project| proto::row_of_state(t, project))).collect();
                    return Ok(proto::snapshot(&self.mac, now, &projects, rows));
                }
                // Only an older host's refusal switches to the fallback; anything else is a real failure to report.
                Err(error) if self.spine.is_none() && error.contains("Unsupported") => self.spine = Some(false),
                Err(error) => return Err(error),
            }
        }
        if self.lost_runs.as_ref().is_none_or(|(at, _)| now - at >= STATUS_MS) {
            let runs = self.host.call("status", json!({})).ok().map(|s| s["lostRuns"].clone()).filter(Value::is_array).unwrap_or(json!([]));
            self.lost_runs = Some((now, runs));
        }
        let lost: HashSet<String> = self.lost_runs.as_ref().unwrap().1.as_array().into_iter().flatten().filter_map(|r| r["channelId"].as_str().map(str::to_owned)).collect();
        let mut rows = Vec::new();
        for project in &projects {
            let Some(id) = project["id"].as_str() else { continue };
            // An archived project, say, reads as empty.
            let list = self.host.call("conversations", json!({"projectId": id})).unwrap_or(json!([]));
            for c in list.as_array().into_iter().flatten() {
                if let Some(cid) = c["id"].as_str() { self.known.insert(cid.into()); }
                if let (Some(cid), Some(name)) = (c["id"].as_str(), c["name"].as_str()) { self.names.insert(cid.into(), name.into()); }
                rows.push(proto::row_of_conversation(c, names.get(id).map(String::as_str).unwrap_or("Project"), c["id"].as_str().is_some_and(|c| lost.contains(c))));
            }
        }
        Ok(proto::snapshot(&self.mac, now, &projects, rows))
    }

    /// Sends Home to every paired phone when it changed. A thread that just started waiting on you also wakes a phone
    /// that's away, with a notice that says nothing about which thread.
    pub fn publish(&mut self) {
        if !self.ready || self.state.phones.is_empty() { return; }
        let home = match self.read_snapshot() {
            Ok(mut home) => { self.add_awake(&mut home); proto::fit_snapshot(home, FIT_BYTES) }
            Err(error) => { self.log(&format!("snapshot failed: {error}")); return; }
        };
        let key = proto::with_at_zero(&home);
        let pks: Vec<String> = self.state.phones.iter().map(|p| p.pk.clone()).collect();
        let message = json!({"t": "snapshot", "home": home});
        if key == self.last_home {
            let active = self.active();
            // Home's chat is read on every poll, because its reply lands without moving any thread's row.
            if active { self.refresh_threads(true); }
            // Unchanged: a heartbeat while a phone is open, sent live only, so the relay stores nothing for it.
            if active && (self.now)() - self.last_sent >= HEARTBEAT_MS {
                self.last_sent = (self.now)();
                for pk in &pks { self.seal_to(pk, message.clone(), None, false); }
            }
            return;
        }
        self.last_home = key;
        self.last_sent = (self.now)();
        let needs: HashSet<String> = home["rows"].as_array().into_iter().flatten().filter(|r| r["bucket"] == "needs").filter_map(|r| r["id"].as_str().map(str::to_owned)).collect();
        let wake = needs.iter().any(|id| !self.last_needs.contains(id));
        self.last_needs = needs;
        // A thread that just finished gets a quieter push. Not on the first Home since start, when every finished thread
        // is "new". Needing you wins: one push only.
        let finished: HashSet<String> = home["rows"].as_array().into_iter().flatten().filter(|r| r["bucket"] == "finished").filter_map(|r| r["id"].as_str().map(str::to_owned)).collect();
        let news = self.last_finished.as_ref().is_some_and(|last| finished.iter().any(|id| !last.contains(id)));
        self.last_finished = Some(finished);
        let push = if wake { Push::Wake } else if news { Push::News } else { Push::None };
        for pk in &pks { self.seal_push(pk, message.clone(), Some("home"), push); }
        self.refresh_threads(false);
    }

    fn refresh_threads(&mut self, home_only: bool) {
        let open: Vec<(String, (String, String))> = self.open_threads.iter().map(|(pk, t)| (pk.clone(), t.clone())).collect();
        for (pk, (project_id, channel_id)) in open {
            if !home_only || channel_id == HOME_CHANNEL { self.open_thread(&pk, &project_id, &channel_id); }
        }
    }

    fn open_thread(&mut self, pk: &str, project_id: &str, channel_id: &str) {
        let read = if channel_id == HOME_CHANNEL { self.read_home(project_id) } else { self.read_thread(pk, project_id, channel_id) };
        match read {
            Ok(thread) => {
                let thread = proto::fit_thread(thread, FIT_BYTES);
                let key = proto::with_at_zero(&thread);
                if self.last_thread.get(pk) == Some(&key) { return; }
                self.last_thread.insert(pk.into(), key);
                self.seal_to(pk, json!({"t": "thread", "thread": thread}), None, false);
            }
            Err(error) => {
                self.log(&format!("thread read failed: {error}"));
                // A thread this Mac has never listed isn't here, and the phone says so. Any other failure sends nothing,
                // and the phone says it couldn't load the conversation.
                if channel_id != HOME_CHANNEL && !self.known.is_empty() && !self.known.contains(channel_id) {
                    let thread = json!({"projectId": project_id, "channelId": channel_id, "name": "", "messages": [], "at": (self.now)(), "missing": true});
                    let key = proto::with_at_zero(&thread);
                    if self.last_thread.get(pk) == Some(&key) { return; }
                    self.last_thread.insert(pk.into(), key);
                    self.seal_to(pk, json!({"t": "thread", "thread": thread}), None, false);
                }
            }
        }
    }

    fn read_thread(&self, pk: &str, project_id: &str, channel_id: &str) -> Result<Value, String> {
        let feed = self.host.call("conversation_feed", json!({"projectId": project_id, "channelId": channel_id, "cursor": null, "force": true}))?;
        let tasks = self.host.call("tasks", json!({"projectId": project_id, "channelId": channel_id}))?;
        let name = self.names.get(channel_id).map(String::as_str).unwrap_or("Thread");
        let mut thread = proto::thread_of(project_id, channel_id, name, (self.now)(), &feed["snapshot"]["history"], &tasks, &self.you);
        // A result waiting on review needs its evidence to tell whether the phone can accept it.
        let now = (self.now)();
        let mut details = HashMap::new();
        {
            let mut kept = self.details.borrow_mut();
            for task in tasks.as_array().into_iter().flatten().filter(|t| t["state"] == "ready_for_review") {
                let Some(task_id) = task["id"].as_str() else { continue };
                let key = proto::detail_key(task);
                let detail = match kept.get(task_id) {
                    Some((was, at, detail)) if *was == key && now - at < DETAIL_MS => detail.clone(),
                    _ => {
                        let detail = self.host.call("task_detail", json!({"projectId": project_id, "channelId": channel_id, "taskId": task_id}))?;
                        kept.insert(task_id.into(), (key, now, detail.clone()));
                        detail
                    }
                };
                details.insert(task_id.to_owned(), detail);
            }
            kept.retain(|_, (_, at, _)| now - *at < DETAIL_MS);
        }
        // All of them, as link.ts sends them: the phone drops any it can't read, then keeps MAX_ASKS.
        let asks = proto::asks_of(&tasks, &details);
        if !asks.is_empty() { thread["asks"] = Value::Array(asks); }
        if self.approvals(pk) { thread["approvals"] = json!(true); }
        Ok(thread)
    }

    /// The project's Home scope: its id and the profile it runs under.
    fn home_scope(&self, project_id: &str) -> Result<(Value, Value), String> {
        // Read at most every 30 s while Home's chat is open; a project's account rarely changes.
        let now = (self.now)();
        let mut cache = self.projects_read.borrow_mut();
        let stale = cache.as_ref().is_none_or(|(at, list)| now - at >= STATUS_MS || !list.as_array().into_iter().flatten().any(|p| p["id"] == project_id));
        if stale { *cache = Some((now, self.host.call("projects", json!({}))?)); }
        let project = cache.as_ref().unwrap().1.as_array().into_iter().flatten().find(|p| p["id"] == project_id).cloned();
        let profile = project.as_ref().and_then(|p| p["profile_id"].as_str().filter(|s| !s.is_empty()).map(str::to_owned));
        match (project, profile) {
            (Some(project), Some(profile)) => Ok((project, json!({"projectId": project_id, "profileId": profile}))),
            _ => Err("This project isn't on this Mac any more.".into()),
        }
    }

    fn read_home(&self, project_id: &str) -> Result<Value, String> {
        let (project, scope) = self.home_scope(project_id)?;
        let listing = self.host.call("orchestrator_list", scope)?;
        Ok(proto::home_chat_of(project_id, &proto::project_name(&project), (self.now)(), &listing["entries"]))
    }

    // ---------- actions ----------

    /// `age` is how long ago you tapped it, in ms.
    /// `n` is the envelope's nonce. `mac_at` is when you tapped, on this Mac's clock as the phone last heard it (spec on
    /// #17): an action is dated by it alone, so neither clock nor a relay holding an action back can make it look fresh.
    fn act(&mut self, pk: &str, id: &str, n: &str, mac_at: Option<i64>, action: Action) {
        // Keyed by phone too, so one phone's id can never answer for another's.
        let key = format!("{pk}:{id}");
        if let Some(done) = self.state.done.get(&key).cloned() {
            self.seal_to(pk, json!({"t": "ack", "id": id, "state": done.state, "detail": done.detail}), Some("q"), false);
            return;
        }
        let now = (self.now)();
        let refused = |detail: &str| ("refused".to_owned(), detail.to_owned());
        let (state, detail) = if self.state.running.contains_key(&key) {
            // Dervo closed while this was running on the host. It may have gone through, so it's never run again.
            refused("Dervo closed while doing this, so it can't tell whether it went through. Check the thread before trying again.")
        } else if let Some(mac_at) = mac_at.filter(|mac_at| *mac_at - now <= CLOCK_SKEW_MS && now - *mac_at <= action.ttl_ms()) {
            self.state.running.insert(key.clone(), now);
            self.persist();
            self.run(action, id, mac_at as f64, pk)
        } else {
            match mac_at {
                None => refused("Dervo on your phone is too old to send this, so it wasn't run. Update Dervo on your phone, then try again."),
                Some(mac_at) if mac_at - now > CLOCK_SKEW_MS => refused("This phone's time changed while it waited, so this wasn't run. Try again."),
                Some(_) => ("expired".to_owned(), if matches!(action, Action::Stop { .. }) { "Your Mac was away, so this Stop wasn't run. Check the thread and stop it again if it's still going." } else { "This message waited too long for your Mac, so it wasn't sent." }.to_owned()),
            }
        };
        self.state.running.remove(&key);
        // Kept SEEN_MS past the later of now and `macAt`, so by the time they go, `macAt` alone says late. In the same
        // save as the answer, so a crash can't leave them shorter.
        let kept = mac_at.unwrap_or(now).max((self.now)());
        if let Some(seen) = self.state.seen.get_mut(n) { *seen = (*seen).max(kept); }
        // Recorded before the ack goes out: a phone that asks again gets the same answer, and nothing runs twice.
        let at = self.state.done.get(&key).map_or(kept, |done| done.at.max(kept));
        self.state.done.insert(key, Done { at, state: state.clone(), detail: detail.clone() });
        self.persist();
        self.seal_to(pk, json!({"t": "ack", "id": id, "state": state, "detail": detail}), Some("q"), false);
        self.last_home.clear();
        self.publish();
    }

    /// `tapped` is when you tapped it, on this Mac's clock.
    fn run(&mut self, action: Action, id: &str, tapped: f64, pk: &str) -> (String, String) {
        let done = |state: &str, detail: &str| (state.to_owned(), detail.to_owned());
        let client_id = format!("phone-{id}");
        let result = (|| -> Result<(String, String), String> {
            match action {
                Action::Home { project_id, text } => {
                    let (_, mut scope) = self.home_scope(&project_id)?;
                    scope["text"] = json!(text);
                    scope["clientId"] = json!(client_id);
                    // Idempotent by client id on the host too: a repeat returns the first record and never asks twice.
                    self.host.call("orchestrator_send", scope)?;
                    Ok(done("done", "Sent to Dervo."))
                }
                Action::Send { project_id, channel_id, text } => {
                    // The action id is the request's client id, so the host itself refuses a second copy too.
                    let result = self.host.call("coding_request", json!({"clientId": client_id, "text": text, "projectId": project_id, "channelId": channel_id, "queueIfBusy": true}))?;
                    if result.is_null() || result["id"].is_null() {
                        return Ok(done("refused", result["detail"].as_str().filter(|d| !d.is_empty()).unwrap_or("Dervo didn't take this message.")));
                    }
                    Ok(if result["state"] == "queued" { done("queued", "Queued. It goes in when the current run ends.") } else { done("done", "Sent.") })
                }
                Action::Answer { project_id, channel_id, task_id, question_id, answer } => {
                    // Only your words go to the agent; an answer never grants anything (task_questions.py). The action id
                    // is the client id, so the host returns its saved answer to a second copy rather than sending it twice.
                    let skip = answer["kind"] == "skip";
                    let view = self.host.call("task_question_answer", json!({"projectId": project_id, "channelId": channel_id, "taskId": task_id,
                        "questionId": question_id, "clientId": client_id, "answer": answer}))?;
                    if skip { return Ok(done("done", "Skipped.")); }
                    Ok(match view["delivery"].as_str() {
                        Some("queued") => done("queued", "Answer saved. It goes in when the agent is free."),
                        Some("not_sent" | "unknown") => done("refused", view["detail"].as_str().filter(|d| !d.is_empty())
                            .unwrap_or("Your answer is saved on your Mac, but it didn't reach the agent. Open the thread there to send it.")),
                        _ => done("done", "Answered."),
                    })
                }
                Action::Scope { project_id, channel_id, task_id, scope_version, approve } => {
                    if !self.approvals(pk) { return Ok(done("refused", CANT_APPROVE)); }
                    let scope = json!({"projectId": project_id, "channelId": channel_id});
                    let tasks = self.host.call("tasks", scope.clone())?;
                    let current = tasks.as_array().into_iter().flatten().find(|t| t["id"] == task_id.as_str()).cloned().unwrap_or(Value::Null);
                    // The host's deny takes no version, so a decision held up in the mail can't touch a scope that changed since.
                    if current["state"] != "needs_permission" || proto::safe_int(&current["scope_version"]) != Some(scope_version) {
                        return Ok(done("refused", "This scope changed or was already decided on your Mac, so nothing was done. Check the thread."));
                    }
                    // Declining is fine from anywhere; approving more than the run's own files needs the Mac's sheet, which shows it.
                    if approve && !proto::scope_on_phone(&current) {
                        return Ok(done("refused", "This run asks for more than its own files, so approve it on your Mac, where you can see what it covers."));
                    }
                    if !approve {
                        self.host.call("task_deny", json!({"projectId": project_id, "channelId": channel_id, "taskId": task_id}))?;
                        return Ok(done("done", "Declined. Nothing runs."));
                    }
                    self.host.call("task_approve", json!({"projectId": project_id, "channelId": channel_id, "taskId": task_id, "scopeVersion": scope_version}))?;
                    Ok(done("done", "Approved. The run is starting."))
                }
                Action::Review { project_id, channel_id, task_id, attempt_number, approved, text } => {
                    if !self.approvals(pk) { return Ok(done("refused", CANT_APPROVE)); }
                    let tasks = self.host.call("tasks", json!({"projectId": project_id, "channelId": channel_id}))?;
                    let current = tasks.as_array().into_iter().flatten().find(|t| t["id"] == task_id.as_str()).cloned().unwrap_or(Value::Null);
                    // A result that's no longer waiting (decided on the Mac, or by another phone) is answered here, and the
                    // host refuses a second decision too. The same rules that showed the card decide again now: Accept only
                    // where the phone offered it, on the attempt you saw, from evidence read fresh.
                    if current["state"] != "ready_for_review" || current["review"] == "rejected" {
                        return Ok(done("refused", "This result was already decided. Check the thread for where it is now."));
                    }
                    let detail = self.host.call("task_detail", json!({"projectId": project_id, "channelId": channel_id, "taskId": task_id}))?;
                    let ask = proto::review_ask(&current, Some(&detail), String::new());
                    if ask.as_ref().and_then(|a| a["attemptNumber"].as_i64()) != Some(attempt_number) {
                        return Ok(done("refused", "This result changed on your Mac since you looked. Check the thread."));
                    }
                    if approved && ask.as_ref().is_some_and(|a| a["phone"] != true) {
                        return Ok(done("refused", "This result needs the review sheet on your Mac to accept it."));
                    }
                    let task = self.host.call("task_review", json!({"projectId": project_id, "channelId": channel_id, "taskId": task_id,
                        "attemptNumber": attempt_number, "decision": if approved { "approved" } else { "rejected" }}))?;
                    if !approved {
                        // The rejection is recorded by now, so a note that fails doesn't turn it into a failed decision.
                        let Some(text) = text else { return Ok(done("done", "Marked for changes. Say what to change in the thread.")) };
                        return Ok(match self.host.call("task_continue", json!({"projectId": project_id, "channelId": channel_id, "taskId": task_id, "text": text})) {
                            Ok(_) => done("done", "Sent back with your changes."),
                            Err(_) => done("done", "Marked for changes, but your note didn't send. Say what to change in the thread."),
                        });
                    }
                    let commit = &task["commit"];
                    Ok(if commit["sha"].as_str().is_some_and(|s| !s.is_empty()) { done("done", "Accepted and committed.") }
                       else if let Some(error) = commit["error"].as_str().filter(|e| !e.is_empty()) { done("done", &format!("Accepted. The commit didn't go through: {error}")) }
                       else { done("done", "Accepted.") })
                }
                Action::Stop { project_id, channel_id } => {
                    // Only runs that had started when you tapped Stop: a late Stop never stops the run after it.
                    let targets = proto::stop_targets(&self.host.call("tasks", json!({"projectId": project_id, "channelId": channel_id}))?, tapped);
                    if targets.is_empty() { return Ok(done("refused", "Nothing is running in this thread now.")); }
                    let failures: Vec<String> = targets.iter()
                        .filter_map(|task| self.host.call("task_stop", json!({"projectId": project_id, "channelId": channel_id, "taskId": task})).err()).collect();
                    Ok(if failures.is_empty() { done("done", "Stopping. Edits are kept.") } else { done("refused", &failures.join(" ")) })
                }
            }
        })();
        result.unwrap_or_else(|error| ("refused".into(), error))
    }

    /// Saved after anything that must survive a restart, with nonces, answers and a pairing code past their time dropped.
    fn persist(&mut self) {
        let now = (self.now)();
        let cutoff = now - SEEN_MS;
        self.state.seen.retain(|_, at| *at >= cutoff);
        self.state.done.retain(|_, done| done.at >= cutoff);
        self.state.running.retain(|_, at| *at >= cutoff);
        // Oldest first, ties by key, so exactly MAX_SEEN stay however many share a time.
        if self.state.seen.len() > MAX_SEEN {
            let mut oldest: Vec<(i64, String)> = self.state.seen.iter().map(|(n, at)| (*at, n.clone())).collect();
            oldest.sort_unstable();
            for (_, n) in &oldest[..oldest.len() - MAX_SEEN] { self.state.seen.remove(n); }
        }
        if self.state.done.len() > MAX_SEEN {
            let mut oldest: Vec<(i64, String)> = self.state.done.iter().map(|(k, d)| (d.at, k.clone())).collect();
            oldest.sort_unstable();
            for (_, k) in &oldest[..oldest.len() - MAX_SEEN] { self.state.done.remove(k); }
        }
        self.unsaved = false;
        self.saved_at = now;
        if self.state.pairing.as_ref().is_some_and(|p| p.expires < now) { self.state.pairing = None; }
        if let Err(error) = self.store.save(&self.state) { self.log(&format!("couldn't save: {error}")); }
    }
}
