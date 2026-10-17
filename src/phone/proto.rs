//! The phone protocol as the Mac speaks it: the same bytes as companion/protocol/*.ts on the phone and the relay
//! (PR #11). Keys, nonces and ciphertext are base64url without padding, and a box is NaCl's (X25519, XSalsa20-Poly1305,
//! tag first), so tweetnacl on the phone opens what this seals. Nothing here does IO.
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use crypto_box::{aead::{rand_core::RngCore, Aead, AeadCore, OsRng}, PublicKey, SalsaBox, SecretKey};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha512};

pub const MAX_PLAINTEXT: usize = 256 * 1024;
/// What fits in one sealed message with room to spare (home.ts FIT_BYTES).
pub const FIT_BYTES: usize = 200 * 1024;
pub const HOME_CHANNEL: &str = "home";
pub const MAX_TEXT: usize = 20_000;
pub const MAX_HOME_TEXT: usize = 8000;
const MAX_MESSAGES: usize = 60;
pub const SEND_TTL_MS: i64 = 24 * 3600_000;
pub const STOP_TTL_MS: i64 = 2 * 60_000;
/// An answer in your own words reads up to this much (messages.ts MAX_ANSWER; the host's own limit is 8 KB).
pub const MAX_ANSWER: usize = 4000;
const MAX_SAFE: f64 = 9_007_199_254_740_991.0;

// ---------- base64url ----------

/// Strict: no padding, nothing but the url alphabet, and zero leftover bits, so each byte string has one spelling.
pub fn decode(text: &str) -> Option<Vec<u8>> { URL_SAFE_NO_PAD.decode(text).ok() }
pub fn encode(bytes: &[u8]) -> String { URL_SAFE_NO_PAD.encode(bytes) }

fn key32(text: &str) -> Result<[u8; 32], String> {
    decode(text).and_then(|bytes| bytes.try_into().ok()).ok_or_else(|| "Malformed key".into())
}
fn nonce24(text: &str) -> Option<[u8; 24]> { decode(text).and_then(|bytes| bytes.try_into().ok()) }

pub fn random_token(bytes: usize) -> String {
    let mut out = vec![0u8; bytes];
    OsRng.fill_bytes(&mut out);
    encode(&out)
}

// ---------- keys and boxes ----------

#[derive(Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct KeyPair { pub public_key: String, pub secret_key: String }

/// Never prints the secret key, so a log or a panic message can't carry it.
impl std::fmt::Debug for KeyPair {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.debug_struct("KeyPair").field("public_key", &self.public_key).finish_non_exhaustive() }
}

impl KeyPair {
    pub fn generate() -> Self {
        let secret = SecretKey::generate(&mut OsRng);
        Self { public_key: encode(secret.public_key().as_bytes()), secret_key: encode(&secret.to_bytes()) }
    }
    fn secret(&self) -> Result<SecretKey, String> { Ok(SecretKey::from(key32(&self.secret_key)?)) }
}

fn salsa(theirs: &str, mine: &KeyPair) -> Result<SalsaBox, String> {
    Ok(SalsaBox::new(&PublicKey::from(key32(theirs)?), &mine.secret()?))
}

/// Seal a JSON value for one recipient, with a fresh random nonce. `keep` and `wake` are the relay's mailbox hints.
#[cfg_attr(not(test), allow(dead_code))] // the tests seal as the phone does; the link uses seal_with
pub fn seal(value: &Value, mine: &KeyPair, theirs: &str, keep: Option<&str>, wake: bool) -> Result<Value, String> {
    seal_with(value, mine, theirs, keep, if wake { Push::Wake } else { Push::None })
}

/// The push the relay sends a phone that isn't connected, if any: `Wake` ("Something needs you.") or `News`
/// ("Something finished.", quieter). Neither says what or where, and the relay strips both before delivery.
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Push { None, Wake, News }

pub fn seal_with(value: &Value, mine: &KeyPair, theirs: &str, keep: Option<&str>, push: Push) -> Result<Value, String> {
    let plain = serde_json::to_vec(value).map_err(|e| e.to_string())?;
    if plain.len() > MAX_PLAINTEXT { return Err("Message too large to send to your phone".into()); }
    let nonce = SalsaBox::generate_nonce(&mut OsRng);
    let sealed = salsa(theirs, mine)?.encrypt(&nonce, plain.as_slice()).map_err(|_| "Couldn't seal the message")?;
    let mut env = json!({"v": 1, "from": mine.public_key, "to": theirs, "n": encode(&nonce), "c": encode(&sealed)});
    if let Some(keep) = keep { env["keep"] = json!(keep); }
    match push { Push::Wake => env["wake"] = json!(true), Push::News => env["news"] = json!(true), Push::None => {} }
    Ok(env)
}

/// The envelope's content if it's addressed to us from `from` and authenticates, else None. Callers drop those silently.
pub fn open(env: &Value, mine: &KeyPair, from: &str) -> Option<Value> {
    if !is_envelope(env) || env["to"] != mine.public_key.as_str() || env["from"] != from { return None; }
    let nonce = nonce24(env["n"].as_str()?)?;
    let sealed = decode(env["c"].as_str()?)?;
    let plain = salsa(from, mine).ok()?.decrypt(&nonce.into(), sealed.as_slice()).ok()?;
    // serde_json refuses malformed UTF-8 and lone surrogates, as fromUtf8 does on the phone.
    serde_json::from_slice(&plain).ok()
}

pub fn is_envelope(env: &Value) -> bool {
    let field = |name: &str| env[name].as_str().is_some_and(|s| !s.is_empty() && s.len() <= 512 * 1024);
    env.is_object() && env["v"] == 1 && ["from", "to", "n", "c"].iter().all(|name| field(name))
        && (env.get("keep").is_none() || env["keep"] == "q" || env["keep"] == "home")
        && (env.get("wake").is_none() || env["wake"].is_boolean())
        && (env.get("news").is_none() || env["news"].is_boolean())
}

const PROOF_LABEL: &str = "dervo-relay-proof/v2";

fn hmac_sha512(key: &[u8], message: &[u8]) -> [u8; 64] {
    let mut block = [0u8; 128];
    if key.len() > 128 { block[..64].copy_from_slice(&Sha512::digest(key)); } else { block[..key.len()].copy_from_slice(key); }
    let (mut inner, mut outer) = (Sha512::new(), Sha512::new());
    inner.update(block.map(|b| b ^ 0x36));
    inner.update(message);
    outer.update(block.map(|b| b ^ 0x5c));
    outer.update(inner.finalize());
    outer.finalize().into()
}

/// The relay sign-in answer, wire spec v2 (companion/protocol/crypto.ts on PR #11): an HMAC keyed from X25519 with the
/// relay's throwaway key through a labelled hash, over who this device says it is. It's never a box under an envelope
/// key, so no answer helps forge a sealed message whatever key and nonce a relay picks (security review, 8 Oct).
pub fn prove(challenge_pk: &str, challenge_n: &str, mine: &KeyPair, role: &str, room: &str) -> Result<String, String> {
    let theirs = curve25519_dalek::montgomery::MontgomeryPoint(key32(challenge_pk)?);
    let dh = theirs.mul_clamped(key32(&mine.secret_key)?).to_bytes();
    if dh.iter().all(|b| *b == 0) { return Err("Refused a relay challenge with a low-order key".into()); }
    let mut hasher = Sha512::new();
    hasher.update(PROOF_LABEL.as_bytes());
    hasher.update(dh);
    let key = hasher.finalize();
    let transcript = [PROOF_LABEL, role, room, &mine.public_key, challenge_pk, challenge_n].join("\n");
    Ok(encode(&hmac_sha512(&key[..32], transcript.as_bytes())[..32]))
}

/// Six digits both screens show while pairing, bound to the Mac, the phone and the one-time code (spec v2).
pub fn pairing_digits(mac_pk: &str, phone_pk: &str, code: &str) -> String {
    let hash = Sha512::digest(["dervo-pair/v2", mac_pk, phone_pk, code].join("\n").as_bytes());
    let number = u32::from_be_bytes([hash[0], hash[1], hash[2], hash[3]]) % 1_000_000;
    format!("{number:06}")
}

pub fn is_key(value: &Value) -> bool {
    value.as_str().is_some_and(|s| s.len() == 43 && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'))
}

/// Only wss, or ws on this machine's loopback for development, so a pairing code never sends a phone anywhere in the clear.
pub fn safe_relay(url: &str) -> bool {
    if url.len() > 512 || url.chars().any(|c| c.is_whitespace() || c.is_control()) { return false; }
    if let Some(rest) = url.strip_prefix("wss://") { return !rest.is_empty() && !rest.starts_with('/'); }
    let Some(rest) = url.strip_prefix("ws://") else { return false };
    let host = rest.split('/').next().unwrap_or("");
    let host = if host.starts_with('[') { host.split(']').next().map(|h| format!("{h}]")).unwrap_or_default() } else { host.split(':').next().unwrap_or("").to_string() };
    matches!(host.as_str(), "127.0.0.1" | "localhost" | "[::1]")
}

// ---------- frames from the relay ----------

/// `held`: how long the relay held an envelope before this delivery, in ms on the relay's clock (spec 6bd82ab on #11).
pub enum FromRelay { Challenge { pk: String, n: String }, Ready, Env { env: Value, held: Option<i64> }, Error(String), Pong, Other }

pub fn read_from_relay(raw: &str) -> FromRelay {
    let Ok(m) = serde_json::from_str::<Value>(raw) else { return FromRelay::Other };
    match m["t"].as_str() {
        Some("challenge") => {
            let c = &m["challenge"];
            let n_ok = c["n"].as_str().is_some_and(|n| n.len() == 32 && n.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'));
            if is_key(&c["pk"]) && n_ok { FromRelay::Challenge { pk: c["pk"].as_str().unwrap().into(), n: c["n"].as_str().unwrap().into() } } else { FromRelay::Other }
        }
        Some("ready") => FromRelay::Ready,
        Some("env") if is_envelope(&m["env"]) => FromRelay::Env { env: m["env"].clone(), held: m["held"].as_f64().filter(|h| *h >= 0.0 && h.fract() == 0.0 && *h <= 9_007_199_254_740_991.0).map(|h| h as i64) },
        Some("error") => FromRelay::Error(match &m["code"] { Value::String(s) => s.chars().take(40).collect(), other => other.to_string().chars().take(40).collect() }),
        Some("pong") => FromRelay::Pong,
        _ => FromRelay::Other,
    }
}

// ---------- messages from a phone ----------

pub enum Action { Send { project_id: String, channel_id: String, text: String }, Stop { project_id: String, channel_id: String }, Home { project_id: String, text: String },
    /// An answer to an agent's question (`task_question_answer`). `answer` is the wire's own shape, rebuilt from only
    /// its known fields: `{kind:'option',optionId}`, `{kind:'text',text}` or `{kind:'skip'}`.
    Answer { project_id: String, channel_id: String, task_id: String, question_id: String, answer: Value },
    /// Approve or decline a run's scope. Only from a phone you allowed to approve.
    Scope { project_id: String, channel_id: String, task_id: String, scope_version: i64, approve: bool },
    /// Accept a finished result, or ask for changes, with `text` saying what (rejected only). Only from a phone you allowed.
    Review { project_id: String, channel_id: String, task_id: String, attempt_number: i64, approved: bool, text: Option<String> } }
impl Action {
    /// messages.ts ACTION_TTL. An answer, approval or review tapped while the Mac sleeps still goes when it wakes: each
    /// names the exact question, scope version or attempt, and the Mac refuses it if that has moved on.
    pub fn ttl_ms(&self) -> i64 { if matches!(self, Action::Stop { .. }) { STOP_TTL_MS } else { SEND_TTL_MS } }
}
pub enum PhoneMessage { Pair { code: String, device: String }, Hello, Open { project_id: String, channel_id: String }, Close,
    /// `mac_at` (wire `macAt`) is when you tapped, on the Mac's clock as the phone last heard it (spec on #17): the Mac
    /// dates actions by it alone. `at` and `sent` are the phone's own clock, read but not used for age.
    Action { id: String, at: f64, sent: Option<f64>, mac_at: Option<i64>, action: Action }, Unpair }

/// JavaScript's string length (UTF-16 units), so limits match the phone's to the character.
fn js_len(s: &str) -> usize { s.encode_utf16().count() }
fn id(value: &Value, max: usize) -> Option<String> {
    value.as_str().filter(|s| !s.is_empty() && js_len(s) <= max).map(str::to_owned)
}

/// JavaScript's Number.isSafeInteger.
pub fn safe_int(value: &Value) -> Option<i64> {
    value.as_i64().filter(|n| (*n as f64).abs() <= MAX_SAFE)
        .or_else(|| value.as_f64().filter(|n| n.fract() == 0.0 && n.abs() <= MAX_SAFE).map(|n| n as i64))
}

/// Nothing left after JavaScript's String.prototype.trim, which also drops U+FEFF.
fn blank(text: &str) -> bool { text.chars().all(|c| c.is_whitespace() || c == '\u{feff}') }

fn read_answer(a: &Value) -> Option<Value> {
    if !a.is_object() { return None; }
    match a["kind"].as_str()? {
        "option" => Some(json!({"kind": "option", "optionId": id(&a["optionId"], 128)?})),
        "text" => a["text"].as_str().filter(|t| !blank(t) && js_len(t) <= MAX_ANSWER).map(|t| json!({"kind": "text", "text": t})),
        "skip" => Some(json!({"kind": "skip"})),
        _ => None,
    }
}

fn read_action(a: &Value) -> Option<Action> {
    if !a.is_object() { return None; }
    let (project_id, channel_id) = (id(&a["projectId"], 128)?, id(&a["channelId"], 128)?);
    let text = |max: usize| a["text"].as_str().filter(|t| !blank(t) && js_len(t) <= max).map(str::to_owned);
    match a["kind"].as_str()? {
        "send" => Some(Action::Send { project_id, channel_id, text: text(MAX_TEXT)? }),
        "stop" => Some(Action::Stop { project_id, channel_id }),
        "home" if channel_id == HOME_CHANNEL => Some(Action::Home { project_id, text: text(MAX_HOME_TEXT)? }),
        "answer" => Some(Action::Answer { project_id, channel_id, task_id: id(&a["taskId"], 128)?, question_id: id(&a["questionId"], 128)?, answer: read_answer(&a["answer"])? }),
        "scope" => {
            let approve = match a["decision"].as_str()? { "approve" => true, "deny" => false, _ => return None };
            Some(Action::Scope { project_id, channel_id, task_id: id(&a["taskId"], 128)?, scope_version: safe_int(&a["scopeVersion"])?, approve })
        }
        "review" => {
            let task_id = id(&a["taskId"], 128)?;
            let attempt_number = safe_int(&a["attemptNumber"])?;
            let approved = match a["decision"].as_str()? { "approved" => true, "rejected" => false, _ => return None };
            // Any text must be a string within MAX_TEXT, and it's kept only with a rejection that says something.
            let text = match a.get("text") { None => None, Some(Value::String(t)) if js_len(t) <= MAX_TEXT => Some(t.clone()), Some(_) => return None };
            Some(Action::Review { project_id, channel_id, task_id, attempt_number, approved, text: text.filter(|t| !approved && !blank(t)) })
        }
        _ => None,
    }
}

pub fn read_phone_message(m: &Value) -> Option<PhoneMessage> {
    match m.get("t")?.as_str()? {
        "pair" => {
            let device: String = m["device"].as_str()?.chars().take(80).collect();
            Some(PhoneMessage::Pair { code: id(&m["code"], 64)?, device: if device.is_empty() { "Phone".into() } else { device } })
        }
        "hello" => Some(PhoneMessage::Hello),
        "unpair" => Some(PhoneMessage::Unpair),
        "close" => Some(PhoneMessage::Close),
        "open" => Some(PhoneMessage::Open { project_id: id(&m["projectId"], 128)?, channel_id: id(&m["channelId"], 128)? }),
        "action" => {
            let at = m["at"].as_f64().filter(|at| at.is_finite())?;
            let sent = m["sent"].as_f64().filter(|sent| sent.is_finite());
            let mac_at = m["macAt"].as_f64().filter(|t| *t >= 0.0 && t.fract() == 0.0 && *t <= 9_007_199_254_740_991.0).map(|t| t as i64);
            Some(PhoneMessage::Action { id: id(&m["id"], 64)?, at, sent, mac_at, action: read_action(&m["action"])? })
        }
        _ => None,
    }
}

// ---------- Home ----------

fn text_of(value: &Value) -> Option<String> { value.as_str().map(str::trim).filter(|s| !s.is_empty()).map(str::to_owned) }
fn pick(values: &[&Value]) -> Option<String> { values.iter().find_map(|v| text_of(v)) }
fn num(value: &Value) -> Option<f64> { value.as_f64().filter(|n| n.is_finite()) }

pub fn project_name(project: &Value) -> String {
    if let Some(name) = project["name"].as_str().filter(|s| !s.is_empty()) { return name.into(); }
    project["cwd"].as_str().unwrap_or("").split(['/', '\\']).filter(|p| !p.is_empty()).last().unwrap_or("Project").into()
}

fn order(bucket: &str) -> u8 { match bucket { "needs" => 0, "unknown" => 1, "working" => 2, "finished" => 3, _ => 4 } }

/// home.ts lineOf: the one sentence for a row.
fn line_of(bucket: &str, a: &Value) -> String {
    match bucket {
        "unknown" => "Dervo lost sight of this run".into(),
        "needs" => pick(&[&a["pending_question"], &a["why"], &a["summary"]]).unwrap_or_else(|| "Waiting on you".into()),
        "working" => pick(&[&a["step"], &a["goal"]]).unwrap_or_else(|| "Working".into()),
        "finished" => pick(&[&a["summary"]]).unwrap_or_else(|| "Finished".into()),
        _ => pick(&[&a["summary"], &a["goal"]]).unwrap_or_default(),
    }
}

fn row(id: &Value, project_id: &Value, project: &str, name: &Value, bucket: &str, line: String, unread: i64, at: Option<f64>, question: &Value, stoppable: bool, quiet: bool) -> Value {
    json!({"id": id, "projectId": project_id, "project": project, "name": name.as_str().filter(|s| !s.is_empty()).unwrap_or("Untitled thread"),
        "bucket": bucket, "line": line, "step": null, "unread": unread, "at": at, "question": question.as_str(), "stoppable": stoppable, "quiet": quiet})
}

/// home.ts rowOfState: one state-spine record (thread_states) as a Home row.
pub fn row_of_state(r: &Value, project: &str) -> Value {
    let unread = r["unread"].as_i64().unwrap_or(0).max(0);
    let state = r["state"].as_str().unwrap_or("");
    let bucket = match state { "needs_you" => "needs", "working" | "quiet" => "working", "unknown" => "unknown", "finished" if unread > 0 => "finished", _ => "idle" };
    let mut line = line_of(bucket, r);
    if state == "quiet" { line = "No word from the agent for over a minute".into(); }
    if state == "stopped" { line = text_of(&r["summary"]).unwrap_or_else(|| "Stopped. Edits kept.".into()); }
    let stoppable = r["running"].as_bool().unwrap_or(bucket == "working" || bucket == "needs");
    row(&r["id"], &r["projectId"], project, &r["name"], bucket, line, unread, num(&r["at"]).or(num(&r["since"])), &r["pending_question"], stoppable, state == "quiet")
}

/// home.ts rowOf: the fallback for a host without the state spine, from `conversations` and `status`'s lost runs.
pub fn row_of_conversation(c: &Value, project: &str, lost: bool) -> Value {
    let a = &c["activity"];
    let has = a.is_object();
    let unread = a["unread"].as_i64().unwrap_or(0);
    let bucket = if lost || a["lost"] == true { "unknown" } else if !has { "idle" }
        else if a["status"] == "needs" || text_of(&a["pending_question"]).is_some() || a["attention"] == true { "needs" }
        else if a["running"] == true || a["status"] == "running" { "working" } else if unread > 0 { "finished" } else { "idle" };
    row(&c["id"], &c["project_id"], project, &c["name"], bucket, line_of(bucket, a), unread.max(0), num(&a["at"]).or(num(&a["since"])), &a["pending_question"], bucket == "working", false)
}

/// Rows sorted the way the phone shows them: needs you first, then unknown, working, finished, idle; newest first.
pub fn snapshot(mac: &str, now: i64, projects: &[Value], mut rows: Vec<Value>) -> Value {
    rows.sort_by(|a, b| {
        order(a["bucket"].as_str().unwrap_or("")).cmp(&order(b["bucket"].as_str().unwrap_or("")))
            .then(num(&b["at"]).unwrap_or(0.0).total_cmp(&num(&a["at"]).unwrap_or(0.0)))
            .then(a["name"].as_str().unwrap_or("").cmp(b["name"].as_str().unwrap_or("")))
    });
    let projects: Vec<Value> = projects.iter().map(|p| json!({"id": p["id"], "name": project_name(p)})).collect();
    json!({"at": now, "mac": mac, "projects": projects, "rows": rows})
}

/// home.ts stopTargets: the thread's tasks that are still working.
/// The runs a Stop tapped at `tapped` (ms, this Mac's clock) ends: working ones whose current run had started by then,
/// give or take 2 s, so a late Stop never stops the run after it. A run starts with your latest message to it; one with
/// none can't be dated, so it counts as started.
pub fn stop_targets(tasks: &Value, tapped: f64) -> Vec<String> {
    tasks.as_array().into_iter().flatten().filter(|t| t["state"] == "working")
        .filter(|t| {
            let started = t["messages"].as_array().into_iter().flatten().filter(|m| m["role"] == "user")
                .filter_map(|m| m["created_at"].as_f64()).filter(|c| c.is_finite()).fold(f64::NEG_INFINITY, f64::max);
            !started.is_finite() || started * 1000.0 <= tapped + 2000.0
        })
        .filter_map(|t| t["id"].as_str().map(str::to_owned)).collect()
}

fn message(id: Value, from: &str, text: &str, at: Option<f64>) -> Value { json!({"id": id, "from": from, "text": text, "at": at}) }

fn thread(project_id: &str, channel_id: &str, name: &str, now: i64, mut messages: Vec<Value>) -> Value {
    let skip = messages.len().saturating_sub(MAX_MESSAGES);
    messages.drain(..skip);
    json!({"projectId": project_id, "channelId": channel_id, "name": name, "messages": messages, "at": now})
}

/// home.ts threadOf: a thread's recent conversation, from its chat history and its coding tasks' messages.
pub fn thread_of(project_id: &str, channel_id: &str, name: &str, now: i64, history: &Value, tasks: &Value, you: &str) -> Value {
    let mut messages = Vec::new();
    for row in history.as_array().into_iter().flatten() {
        let Some(text) = row["content"].as_str().filter(|t| !t.trim().is_empty()) else { continue };
        messages.push(message(row["id"].clone(), if row["pubkey"] == you { "you" } else { "agent" }, text, num(&row["created_at"])));
    }
    for task in tasks.as_array().into_iter().flatten() {
        for (index, m) in task["messages"].as_array().into_iter().flatten().enumerate() {
            let Some(text) = m["text"].as_str().filter(|t| !t.trim().is_empty()) else { continue };
            // Tool and system rows are the run's plumbing, not conversation.
            let from = match m["role"].as_str() { Some("user") => "you", Some("assistant") => "agent", Some("reviewer") => "dervo", _ => continue };
            messages.push(message(json!(format!("{}:{index}", task["id"].as_str().unwrap_or(""))), from, text, num(&m["created_at"])));
        }
    }
    // Stable, like Array.prototype.sort, so equal times keep history before task messages.
    messages.sort_by(|a, b| num(&a["at"]).unwrap_or(0.0).total_cmp(&num(&b["at"]).unwrap_or(0.0)));
    thread(project_id, channel_id, name, now, messages)
}

/// home.ts homeChatOf: a project's Home chat with the coordinator, as a thread the phone can show.
pub fn home_chat_of(project_id: &str, name: &str, now: i64, entries: &Value) -> Value {
    let mut messages = Vec::new();
    for entry in entries.as_array().into_iter().flatten() {
        let at = num(&entry["created_at"]);
        match entry["kind"].as_str() {
            Some("you") => if let Some(text) = entry["text"].as_str().filter(|t| !t.trim().is_empty()) { messages.push(message(entry["id"].clone(), "you", text, at)); },
            Some("reply") => {
                if entry["state"] == "thinking" {
                    let mut m = message(entry["id"].clone(), "dervo", "Thinking…", at);
                    m["pending"] = json!(true);
                    messages.push(m);
                    continue;
                }
                let mut lines: Vec<String> = text_of(&entry["text"]).into_iter().collect();
                for part in entry["parts"].as_array().into_iter().flatten() {
                    if part["state"] == "sent" { if let Some(name) = part["name"].as_str().filter(|n| !n.is_empty()) { lines.push(format!("Sent to {name}.")); } }
                }
                if !lines.is_empty() { messages.push(message(entry["id"].clone(), "dervo", &lines.join("\n\n"), at)); }
            }
            _ => {}
        }
    }
    thread(project_id, HOME_CHANNEL, name, now, messages)
}

fn size(value: &Value) -> usize { serde_json::to_vec(value).map(|v| v.len()).unwrap_or(usize::MAX) }

/// home.ts fitThread: drops the oldest messages until the thread fits one sealed message, and says so.
pub fn fit_thread(mut thread: Value, budget: usize) -> Value {
    if size(&thread) <= budget { return thread; }
    let all = thread["messages"].as_array().cloned().unwrap_or_default();
    thread["earlier"] = json!(true);
    // Cut from the front by measured size, then confirm, so a long thread costs a few measures, not one per message.
    let mut start = 0;
    loop {
        thread["messages"] = Value::Array(all[start..].to_vec());
        if start >= all.len() || size(&thread) <= budget { break; }
        start += 1;
    }
    // One message alone can still be too long: keep its end. (home.ts checks the size after emptying the list, which
    // always fits, so there the thread comes out empty.)
    if start == all.len() && !all.is_empty() {
        if let Some(last) = all.last() {
            let text = last["text"].as_str().unwrap_or("");
            let keep = budget / 4;
            let tail: String = { let units: Vec<u16> = text.encode_utf16().collect(); String::from_utf16_lossy(&units[units.len().saturating_sub(keep)..]) };
            let mut m = last.clone();
            m["text"] = json!(format!("…{tail}"));
            thread["messages"] = json!([m]);
        }
    }
    thread
}

/// home.ts fitSnapshot: drops rows from the end (idle first, by the sort order) until Home fits, and counts them.
pub fn fit_snapshot(mut home: Value, budget: usize) -> Value {
    if size(&home) <= budget { return home; }
    let mut rows = home["rows"].as_array().cloned().unwrap_or_default();
    let mut hidden = 0;
    home["hidden"] = json!(0);
    // Each row is measured once; the total only shrinks, so the first length that fits is the longest that does.
    let mut total = size(&home);
    while !rows.is_empty() && total > budget {
        let gone = rows.pop().unwrap();
        total = total.saturating_sub(size(&gone) + usize::from(!rows.is_empty()));
        hidden += 1;
        // The count's own digits grow too: re-measure once it claims to fit.
        if total <= budget {
            home["rows"] = Value::Array(rows.clone());
            home["hidden"] = json!(hidden);
            total = size(&home);
        }
    }
    home["rows"] = Value::Array(rows);
    home["hidden"] = json!(hidden);
    home
}

// ---------- what a thread waits on you for ----------

/// Phases in which the host's own reviewers still drive a result (collaboration.REVIEW_OPEN).
const REVIEW_OPEN: [&str; 4] = ["pending", "initial", "correction", "final"];

/// JavaScript truthiness, for the host's optional fields.
fn truthy(value: &Value) -> bool {
    match value { Value::Null => false, Value::Bool(b) => *b, Value::Number(n) => n.as_f64().is_some_and(|n| n != 0.0), Value::String(s) => !s.is_empty(), _ => true }
}

/// home.ts scopeOnPhone: whether a scope is only its own files, the one thing the phone's card shows. Anything broader
/// (the whole project, extra folders, full access) is approved on the Mac, where its sheet shows it.
pub fn scope_on_phone(task: &Value) -> bool {
    task["project_access"] != true && task["full_access"] != true && !has_length(&task["folders"])
}

/// JavaScript's `value?.length` being truthy, for a list the host should send: anything present that isn't an empty
/// list (or an empty string) counts as having some, so a malformed field never lets the phone through.
fn has_length(value: &Value) -> bool {
    match value { Value::Null => false, Value::Array(a) => !a.is_empty(), Value::String(s) => !s.is_empty(), _ => true }
}

/// home.ts detailKey: what changes when a result waiting on review gets a new attempt or moves on, so its evidence is
/// read again.
pub fn detail_key(task: &Value) -> String {
    let len = |v: &Value| v.as_array().map(|a| json!(a.len())).unwrap_or(Value::Null);
    let or_null = |v: &Value| if v.is_null() { Value::Null } else { v.clone() };
    json!([task["state"], or_null(&task["review"]), or_null(&task["scope_version"]), or_null(&task["collaboration"]["review"]["phase"]),
        or_null(&task["completion"]["stale"]), len(&task["completions"]), len(&task["runs"])]).to_string()
}

/// home.ts taskWhat: the first line of the title, else the request, else your first message, up to 400 characters.
fn task_what(task: &Value) -> String {
    let first = task["messages"].as_array().into_iter().flatten().find(|m| m["role"] == "user").map(|m| &m["text"]);
    let text = [&task["title"], &task["text"]].into_iter().chain(first).find_map(|v| v.as_str().filter(|s| !s.is_empty())).unwrap_or("");
    let line = text.trim_matches(|c: char| c.is_whitespace() || c == '\u{feff}').split('\n').next().unwrap_or("");
    let units: Vec<u16> = line.encode_utf16().take(400).collect();
    String::from_utf16_lossy(&units)
}

/// home.ts asksOf: what a thread waits on you for, from its tasks (`tasks`) and, for results waiting on review, their
/// `task_detail`: open questions, a scope to approve, a result to accept. The same rules as the Mac's own buttons, and
/// the Mac checks again when you tap.
pub fn asks_of(tasks: &Value, details: &std::collections::HashMap<String, Value>) -> Vec<Value> {
    let mut asks = Vec::new();
    for task in tasks.as_array().into_iter().flatten() {
        let Some(task_id) = task["id"].as_str() else { continue };
        for q in task["questions"].as_array().into_iter().flatten() {
            if q["state"] != "pending" || !truthy(&q["canAnswer"]) { continue; }
            let options: Vec<Value> = q["options"].as_array().into_iter().flatten().map(|o| json!({"id": o["id"], "label": o["label"].as_str().unwrap_or(""), "detail": o["detail"].as_str().unwrap_or("")})).collect();
            asks.push(json!({"kind": "question", "taskId": task_id, "questionId": q["id"], "ask": q["ask"].as_str().unwrap_or(""), "canSkip": q["canSkip"] == true, "options": options}));
        }
        let what = task_what(task);
        if task["state"] == "needs_permission" {
            if let Some(version) = safe_int(&task["scope_version"]) {
                let files = task["files"].as_array().cloned().unwrap_or_default();
                asks.push(json!({"kind": "scope", "taskId": task_id, "scopeVersion": version, "what": what, "files": files, "phone": scope_on_phone(task)}));
            }
        }
        if task["state"] == "ready_for_review" {
            if let Some(ask) = review_ask(task, details.get(task_id), what) { asks.push(ask); }
        }
    }
    asks
}

/// The review ask for a result waiting on you, or None when it isn't a decision for anyone yet: the host's reviewers
/// still run, a correction was asked for, it went stale, or there's no evidence to decide on.
pub fn review_ask(task: &Value, detail: Option<&Value>, what: String) -> Option<Value> {
    let detail = detail?;
    let latest = detail["evidence"]["attempts"].as_array().and_then(|a| a.last())?;
    let phase = task["collaboration"]["review"]["phase"].as_str().unwrap_or("");
    if latest["kind"] == "restoration" || truthy(&detail["rebound"]) || truthy(&detail["retry"]["available"]) || truthy(&task["completion"]["stale"])
        || task["review"] == "rejected" || REVIEW_OPEN.contains(&phase) { return None; }
    let number = safe_int(&latest["number"])?;
    let manual = task["check_profile"] == "manual-review";
    let test = &latest["test"];
    let checked = !manual && latest["state"] == "verified" && test["exit_status"].as_f64() == Some(0.0) && !truthy(&test["stopped"]);
    let approvable = if manual {
        latest["kind"] == "manual-review" && latest["state"] == "unverified" && test.is_null() && !has_length(&latest["argv"])
    } else { checked };
    let unrecorded = has_length(&latest["unrecorded"]);
    let files = latest["diff"].as_object().map_or(0, |d| d.len());
    // With approval off for this request, an unfinished result needs a correction, not an approval.
    let phone = approvable && task["acceptance"]["required"] != false && !unrecorded && !truthy(&detail["carried"]);
    Some(json!({"kind": "review", "taskId": task["id"], "attemptNumber": number, "what": what, "files": files, "checked": checked, "phone": phone}))
}

pub fn with_at_zero(value: &Value) -> String {
    let mut copy = value.clone();
    copy["at"] = json!(0);
    copy.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test] fn base64url_is_strict_and_canonical() {
        assert_eq!(encode(&[0xfb, 0xff]), "-_8");
        assert_eq!(decode("-_8"), Some(vec![0xfb, 0xff]));
        // Non-zero leftover bits, padding, the standard alphabet and an impossible length are refused.
        for bad in ["-_9", "-_8=", "+/8", "A"] { assert_eq!(decode(bad), None, "{bad}"); }
    }

    #[test] fn a_sealed_message_opens_only_for_its_recipient_from_its_sender() {
        let (mac, phone, other) = (KeyPair::generate(), KeyPair::generate(), KeyPair::generate());
        let env = seal(&json!({"t": "hello"}), &mac, &phone.public_key, Some("q"), true).unwrap();
        assert_eq!((env["keep"].as_str(), env["wake"].as_bool()), (Some("q"), Some(true)));
        assert_eq!(open(&env, &phone, &mac.public_key), Some(json!({"t": "hello"})));
        assert_eq!(open(&env, &other, &mac.public_key), None);
        assert_eq!(open(&env, &phone, &other.public_key), None);
        let mut tampered = env.clone();
        let mut c = decode(env["c"].as_str().unwrap()).unwrap();
        c[20] ^= 1;
        tampered["c"] = json!(encode(&c));
        assert_eq!(open(&tampered, &phone, &mac.public_key), None);
    }

    #[test] fn too_large_a_message_is_refused_before_sealing() {
        let mac = KeyPair::generate();
        assert!(seal(&json!("x".repeat(MAX_PLAINTEXT)), &mac, &mac.public_key, None, false).is_err());
    }

    #[test] fn pairing_digits_are_six_and_bound_to_mac_phone_and_code() {
        let digits = pairing_digits("mac", "phone", "code");
        assert_eq!((digits.len(), digits.clone()), (6, pairing_digits("mac", "phone", "code")));
        assert_ne!(digits, pairing_digits("mac2", "phone", "code"));
    }

    #[test] fn hmac_matches_rfc_4231_case_2() {
        let mac = hmac_sha512(b"Jefe", b"what do ya want for nothing?");
        assert_eq!(mac[..8], [0x16, 0x4b, 0x7a, 0x7b, 0xfc, 0xf8, 0x19, 0xe2]);
    }

    #[test] fn relays_are_wss_or_loopback_ws() {
        for good in ["wss://relay.trydervo.com", "ws://127.0.0.1:8787", "ws://localhost:1/x", "ws://[::1]:9"] { assert!(safe_relay(good), "{good}"); }
        for bad in ["ws://relay.trydervo.com", "http://127.0.0.1", "ws://127.0.0.1.evil.com", "wss://", "ws://localhost@evil.com", "wss://a b"] { assert!(!safe_relay(bad), "{bad}"); }
    }

    #[test] fn phone_messages_are_read_strictly() {
        assert!(matches!(read_phone_message(&json!({"t": "action", "id": "a", "at": 1, "action": {"kind": "home", "projectId": "p", "channelId": "home", "text": "hi"}})), Some(PhoneMessage::Action { action: Action::Home { .. }, .. })));
        // Home's text has its own limit, and a home action must name the home channel.
        assert!(read_phone_message(&json!({"t": "action", "id": "a", "at": 1, "action": {"kind": "home", "projectId": "p", "channelId": "home", "text": "x".repeat(8001)}})).is_none());
        assert!(read_phone_message(&json!({"t": "action", "id": "a", "at": 1, "action": {"kind": "home", "projectId": "p", "channelId": "c1", "text": "hi"}})).is_none());
        assert!(read_phone_message(&json!({"t": "action", "id": "a", "at": 1, "action": {"kind": "send", "projectId": "p", "channelId": "c", "text": "  "}})).is_none());
        assert!(read_phone_message(&json!({"t": "approve"})).is_none());
        assert!(matches!(read_phone_message(&json!({"t": "pair", "code": "c", "device": ""})), Some(PhoneMessage::Pair { device, .. }) if device == "Phone"));
    }

    #[test] fn answers_approvals_and_reviews_are_read_strictly() {
        let action = |a: Value| read_phone_message(&json!({"t": "action", "id": "a", "at": 1, "macAt": 1, "action": a}));
        let base = json!({"projectId": "p", "channelId": "c", "taskId": "t"});
        let with = |extra: Value| { let mut a = base.clone(); for (k, v) in extra.as_object().unwrap() { a[k] = v.clone(); } a };
        assert!(matches!(action(with(json!({"kind": "answer", "questionId": "q", "answer": {"kind": "option", "optionId": "o", "extra": 1}}))),
            Some(PhoneMessage::Action { action: Action::Answer { answer, .. }, .. }) if answer == json!({"kind": "option", "optionId": "o"})));
        assert!(matches!(action(with(json!({"kind": "answer", "questionId": "q", "answer": {"kind": "skip"}}))), Some(PhoneMessage::Action { .. })));
        for bad in [json!({"kind": "text", "text": " \u{feff} "}), json!({"kind": "text", "text": "x".repeat(4001)}), json!({"kind": "option"}), json!({"kind": "grant"})] {
            assert!(action(with(json!({"kind": "answer", "questionId": "q", "answer": bad}))).is_none(), "{bad}");
        }
        assert!(matches!(action(with(json!({"kind": "scope", "scopeVersion": 3, "decision": "deny"}))),
            Some(PhoneMessage::Action { action: Action::Scope { scope_version: 3, approve: false, .. }, .. })));
        for bad in [json!({"kind": "scope", "scopeVersion": 3.5, "decision": "approve"}), json!({"kind": "scope", "scopeVersion": "3", "decision": "approve"}),
                    json!({"kind": "scope", "scopeVersion": 3, "decision": "yes"}), json!({"kind": "review", "attemptNumber": 1, "decision": "approved", "text": 5}),
                    json!({"kind": "review", "attemptNumber": 1, "decision": "approved", "text": null}), json!({"kind": "review", "attemptNumber": 1, "decision": "accept"})] {
            assert!(action(with(bad.clone())).is_none(), "{bad}");
        }
        // Text is kept only with a rejection that says something.
        let text = |a: Value| match action(with(a)) { Some(PhoneMessage::Action { action: Action::Review { text, .. }, .. }) => text, _ => panic!("a review") };
        assert_eq!(text(json!({"kind": "review", "attemptNumber": 1, "decision": "rejected", "text": "smaller"})), Some("smaller".into()));
        assert_eq!(text(json!({"kind": "review", "attemptNumber": 1, "decision": "approved", "text": "smaller"})), None);
        assert_eq!(text(json!({"kind": "review", "attemptNumber": 1, "decision": "rejected", "text": "  "})), None);
        assert!(!scope_on_phone(&json!({"folders": "src"})), "a malformed list never lets the phone through");
        assert!(scope_on_phone(&json!({"folders": []})) && scope_on_phone(&json!({"folders": null})));
        let mut long = base.clone();
        long["taskId"] = json!("t".repeat(129));
        long["kind"] = json!("review"); long["attemptNumber"] = json!(1); long["decision"] = json!("rejected");
        assert!(action(long).is_none());
    }

    #[test] fn a_result_is_offered_to_the_phone_only_when_the_mac_would_offer_it() {
        let details = |d: Value| std::collections::HashMap::from([("r".to_owned(), d)]);
        let attempt = json!({"number": 4, "state": "verified", "test": {"exit_status": 0, "stopped": false}, "diff": {"a": 1}});
        let task = json!({"id": "r", "state": "ready_for_review", "messages": [{"role": "user", "text": "  Fix it\nplease"}]});
        let ask = |task: &Value, detail: Value| asks_of(&json!([task]), &details(detail));
        assert_eq!(ask(&task, json!({"evidence": {"attempts": [attempt]}})), [json!({"kind": "review", "taskId": "r", "attemptNumber": 4, "what": "Fix it", "files": 1, "checked": true, "phone": true})]);
        // Not a decision for anyone yet.
        for detail in [json!({}), json!({"evidence": {"attempts": []}}), json!({"rebound": true, "evidence": {"attempts": [attempt]}}), json!({"retry": {"available": true}, "evidence": {"attempts": [attempt]}})] {
            assert_eq!(ask(&task, detail.clone()), Vec::<Value>::new(), "{detail}");
        }
        for extra in [json!({"review": "rejected"}), json!({"completion": {"stale": true}}), json!({"collaboration": {"review": {"phase": "correction"}}})] {
            let mut t = task.clone(); for (k, v) in extra.as_object().unwrap() { t[k] = v.clone(); }
            assert_eq!(ask(&t, json!({"evidence": {"attempts": [attempt]}})), Vec::<Value>::new(), "{extra}");
        }
        // Shown, but Accept needs the Mac's sheet.
        let phone = |t: &Value, d: Value| ask(t, d)[0]["phone"].clone();
        assert_eq!(phone(&task, json!({"carried": {"files": []}, "evidence": {"attempts": [attempt]}})), false);
        let mut failed = attempt.clone(); failed["test"]["exit_status"] = json!(1);
        assert_eq!((ask(&task, json!({"evidence": {"attempts": [failed]}}))[0]["checked"].clone(), phone(&task, json!({"evidence": {"attempts": [failed]}}))), (json!(false), json!(false)));
        let mut off = task.clone(); off["acceptance"] = json!({"required": false});
        assert_eq!(phone(&off, json!({"evidence": {"attempts": [attempt]}})), false);
        // A manual review is approvable with no checks, and never "checked".
        let mut manual = task.clone(); manual["check_profile"] = json!("manual-review");
        let by_hand = json!({"number": 1, "kind": "manual-review", "state": "unverified", "test": null, "argv": []});
        assert_eq!(ask(&manual, json!({"evidence": {"attempts": [by_hand]}}))[0]["checked"], false);
        assert_eq!(phone(&manual, json!({"evidence": {"attempts": [by_hand]}})), true);
        // The key that keeps its evidence changes with the task.
        let mut moved = task.clone(); moved["runs"] = json!([1]);
        assert_ne!(detail_key(&task), detail_key(&moved));
        assert_eq!(detail_key(&task), detail_key(&task.clone()));
    }

    #[test] fn a_news_envelope_says_so_and_wake_and_news_are_read_as_flags() {
        let mac = KeyPair::generate();
        let env = seal_with(&json!({"t": "snapshot"}), &mac, &mac.public_key, Some("home"), Push::News).unwrap();
        assert_eq!((env.get("wake"), env["news"].as_bool()), (None, Some(true)));
        assert!(is_envelope(&env));
        let mut bad = env.clone(); bad["news"] = json!("yes");
        assert!(!is_envelope(&bad));
    }

    #[test] fn rows_follow_the_spine_and_sort_needs_first() {
        let home = snapshot("Mac", 5, &[json!({"id": "p", "name": "Dervo"})], vec![
            row_of_state(&json!({"id": "a", "projectId": "p", "name": "A", "state": "working", "step": "Editing", "at": 3, "running": true}), "Dervo"),
            row_of_state(&json!({"id": "b", "projectId": "p", "name": "B", "state": "needs_you", "pending_question": "Which?", "at": 1, "running": false}), "Dervo"),
            row_of_state(&json!({"id": "c", "projectId": "p", "state": "quiet", "at": 2}), "Dervo"),
        ]);
        let rows = home["rows"].as_array().unwrap();
        assert_eq!(rows.iter().map(|r| r["id"].as_str().unwrap()).collect::<Vec<_>>(), ["b", "a", "c"]);
        assert_eq!((rows[0]["line"].as_str(), rows[0]["stoppable"].as_bool(), rows[0]["question"].as_str()), (Some("Which?"), Some(false), Some("Which?")));
        assert_eq!((rows[1]["line"].as_str(), rows[1]["stoppable"].as_bool()), (Some("Editing"), Some(true)));
        assert_eq!((rows[2]["name"].as_str(), rows[2]["quiet"].as_bool(), rows[2]["line"].as_str()), (Some("Untitled thread"), Some(true), Some("No word from the agent for over a minute")));
    }

    #[test] fn home_chat_shows_thinking_and_where_jobs_went() {
        let chat = home_chat_of("p", "Dervo", 1, &json!([
            {"id": "1", "kind": "you", "text": "fix it", "created_at": 1},
            {"id": "2", "kind": "reply", "state": "ready", "text": "On it.", "parts": [{"name": "Fix login", "state": "sent"}, {"name": "Other", "state": "offered"}], "created_at": 2},
            {"id": "3", "kind": "reply", "state": "thinking", "created_at": 3},
            {"id": "4", "kind": "arrival", "text": "x"},
        ]));
        let messages = chat["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 3);
        assert_eq!(messages[1]["text"], "On it.\n\nSent to Fix login.");
        assert_eq!(messages[2]["pending"], true);
        assert_eq!(chat["channelId"], "home");
    }

    #[test] fn a_long_thread_keeps_its_newest_end_and_says_so() {
        let messages: Vec<Value> = (0..50).map(|i| message(json!(i.to_string()), "agent", &"x".repeat(10_000), Some(i as f64))).collect();
        let fitted = fit_thread(thread("p", "c", "T", 0, messages), FIT_BYTES);
        assert!(size(&fitted) <= FIT_BYTES);
        assert_eq!(fitted["earlier"], true);
        assert_eq!(fitted["messages"].as_array().unwrap().last().unwrap()["id"], "49");
        let one = fit_thread(thread("p", "c", "T", 0, vec![message(json!("1"), "agent", &"y".repeat(300_000), None)]), FIT_BYTES);
        assert!(size(&one) <= FIT_BYTES && one["messages"][0]["text"].as_str().unwrap().starts_with('…'));
    }

    #[test] fn a_big_home_drops_rows_from_the_end_and_counts_them() {
        let rows: Vec<Value> = (0..400).map(|i| row_of_state(&json!({"id": format!("t{i}"), "projectId": "p", "name": "n".repeat(600), "state": "idle", "at": i}), "P")).collect();
        let home = fit_snapshot(snapshot("Mac", 0, &[], rows), FIT_BYTES);
        assert!(size(&home) <= FIT_BYTES);
        let (kept, hidden) = (home["rows"].as_array().unwrap().len(), home["hidden"].as_u64().unwrap() as usize);
        assert_eq!(kept + hidden, 400);
        // The longest that fits: one more row wouldn't.
        let mut more = home.clone();
        more["rows"].as_array_mut().unwrap().push(home["rows"][0].clone());
        assert!(size(&more) > FIT_BYTES);
        // Newest kept: the sort put the highest `at` first.
        assert_eq!(home["rows"][0]["id"], "t399");
    }
}
