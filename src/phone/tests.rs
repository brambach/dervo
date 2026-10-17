//! The phone link against a phone: in memory with a stand-in host (`link_*`), and end to end with the real Python host,
//! the companion's dev relay and the companion's own PhoneLink (`link_e2e`, ignored by default: it needs Node and a
//! companion folder with its node_modules, named by DERVO_COMPANION).
use super::link::{Host, Link, LinkState, Phone, Store, ACTIVE_MS, HEARTBEAT_MS, IDLE_POLL_MS, SEEN_MS};
use super::proto::{self, KeyPair};
use super::{Driver, FileSecrets, FileStore};
use serde_json::{json, Value};
use std::sync::{atomic::{AtomicI64, Ordering}, Arc, Mutex};

// ---------- in memory ----------

#[derive(Clone, Default)]
struct FakeHost {
    calls: Arc<Mutex<Vec<(String, Value)>>>, threads: Arc<Mutex<Vec<Value>>>, running: Arc<Mutex<bool>>,
    /// When set, what `tasks` returns instead.
    tasks: Arc<Mutex<Option<Value>>>,
    /// The store's last save, so a host call can see what was saved before it ran.
    saved: Arc<Mutex<Option<LinkState>>>,
    /// How many actions the store showed as running at each `task_stop`.
    running_at_stop: Arc<Mutex<Vec<usize>>>,
    /// What `keep_awake_status` returns; None answers as a host without it.
    awake: Arc<Mutex<Option<Value>>>,
    /// Answers for any other operation, by name.
    replies: Arc<Mutex<std::collections::HashMap<String, Value>>>,
}
impl Host for FakeHost {
    fn call(&self, operation: &str, args: Value) -> Result<Value, String> {
        self.calls.lock().unwrap().push((operation.into(), args.clone()));
        match operation {
            "status" => Ok(json!({"actor": "me"})),
            "projects" => Ok(json!([{"id": "p1", "name": "Dervo", "profile_id": "prof"}])),
            "thread_states" => Ok(json!({"threads": *self.threads.lock().unwrap()})),
            "tasks" => Ok(if let Some(tasks) = self.tasks.lock().unwrap().clone() { tasks } else if *self.running.lock().unwrap() { json!([{"id": "t1", "state": "working"}]) } else { json!([]) }),
            "task_stop" => {
                let running = self.saved.lock().unwrap().as_ref().map_or(0, |s| s.running.len());
                self.running_at_stop.lock().unwrap().push(running);
                Ok(json!({}))
            }
            "coding_request" => Ok(json!({"id": "r1", "state": "working"})),
            "keep_awake_status" => self.awake.lock().unwrap().clone().ok_or_else(|| "Unsupported operation keep_awake_status".into()),
            "orchestrator_send" | "orchestrator_list" => Ok(json!({"entries": []})),
            "conversation_feed" if args["channelId"] == "a" => Ok(json!({"snapshot": {"history": []}})),
            "conversation_feed" => Err("No such conversation.".into()),
            other => self.replies.lock().unwrap().get(other).cloned().ok_or_else(|| format!("Unsupported operation {other}")),
        }
    }
}
impl FakeHost {
    fn count(&self, operation: &str) -> usize { self.calls.lock().unwrap().iter().filter(|(op, _)| op == operation).count() }
    fn args(&self, operation: &str) -> Option<Value> { self.calls.lock().unwrap().iter().rev().find(|(op, _)| op == operation).map(|(_, a)| a.clone()) }
    fn reply(&self, operation: &str, value: Value) { self.replies.lock().unwrap().insert(operation.into(), value); }
}

#[derive(Clone, Default)]
struct MemoryStore(Arc<Mutex<Option<LinkState>>>);
impl Store for MemoryStore { fn save(&self, state: &LinkState) -> Result<(), String> { *self.0.lock().unwrap() = Some(state.clone()); Ok(()) } }

struct World { link: Link<FakeHost, MemoryStore>, host: FakeHost, store: MemoryStore, clock: Arc<AtomicI64>, phone: KeyPair }

fn thread(id: &str, state: &str) -> Value { json!({"id": id, "projectId": "p1", "name": id, "state": state, "at": 1}) }

impl World {
    /// A Mac link the relay has signed in, with one phone that paired and was allowed.
    fn paired() -> Self {
        let host = FakeHost::default();
        host.threads.lock().unwrap().push(thread("a", "working"));
        let clock = Arc::new(AtomicI64::new(1_000_000_000));
        let time = clock.clone();
        let store = MemoryStore::default();
        let host = FakeHost { saved: store.0.clone(), ..host };
        let mut link = Link::new(host.clone(), store.clone(), LinkState::new("ws://127.0.0.1:1"), "Test Mac", Box::new(move || time.load(Ordering::SeqCst)));
        link.on_frame(r#"{"t":"ready","macOnline":true,"macSeen":null,"allowed":true}"#);
        let offer = link.pairing_offer();
        let phone = KeyPair::generate();
        let mut world = World { link, host, store, clock, phone };
        world.from_phone(json!({"t": "pair", "code": offer["offer"]["code"], "device": "Test iPhone"}));
        let asking = world.link.waiting().expect("asking to pair");
        assert_eq!(asking.digits, proto::pairing_digits(&world.link.state.keys.public_key, &world.phone.public_key, offer["offer"]["code"].as_str().unwrap()));
        assert!(world.link.phones().is_empty(), "nothing until you allow it");
        assert!(world.link.allow_phone(&world.phone.public_key));
        world
    }
    fn envelope(&self, message: Value) -> Value { proto::seal(&message, &self.phone, &self.link.state.keys.public_key, Some("q"), false).unwrap() }
    fn send_env(&mut self, env: &Value) -> Vec<Value> {
        self.link.out.clear();
        self.link.on_frame(&json!({"t": "env", "env": env}).to_string());
        self.sent()
    }
    fn from_phone(&mut self, message: Value) -> Vec<Value> { let env = self.envelope(message); self.send_env(&env) }
    /// An envelope the relay held for `held` ms before this delivery.
    fn from_phone_held(&mut self, message: Value, held: i64) -> Vec<Value> {
        let env = self.envelope(message);
        self.link.out.clear();
        self.link.on_frame(&json!({"t": "env", "env": env, "held": held}).to_string());
        self.sent()
    }
    /// Pairs and allows another phone, returning its keys.
    fn pair_another(&mut self, device: &str) -> KeyPair {
        let offer = self.link.pairing_offer();
        let other = KeyPair::generate();
        let env = proto::seal(&json!({"t": "pair", "code": offer["offer"]["code"], "device": device}), &other, &self.link.state.keys.public_key, None, false).unwrap();
        self.link.on_frame(&json!({"t": "env", "env": env}).to_string());
        assert!(self.link.allow_phone(&other.public_key));
        other
    }
    /// What the link sent, with sealed messages opened as the phone would.
    fn sent(&mut self) -> Vec<Value> {
        self.link.out.drain(..).map(|frame| {
            let mut frame: Value = serde_json::from_str(&frame).unwrap();
            if frame["t"] == "send" {
                let opened = proto::open(&frame["env"], &self.phone, &self.link.state.keys.public_key).expect("sealed to the phone");
                frame["message"] = opened;
            }
            frame
        }).collect()
    }
    fn messages(frames: &[Value], t: &str) -> Vec<Value> { frames.iter().filter(|f| f["message"]["t"] == t).cloned().collect() }
    fn advance(&self, ms: i64) { self.clock.fetch_add(ms, Ordering::SeqCst); }
    fn now(&self) -> i64 { self.clock.load(Ordering::SeqCst) }
}

#[test]
fn link_pairs_only_after_allow_and_sends_home_with_keep() {
    let mut world = World::paired();
    let frames = world.sent();
    assert!(frames.iter().any(|f| f["t"] == "allow" && f["phones"] == json!([world.phone.public_key])));
    assert_eq!(World::messages(&frames, "paired")[0]["message"]["mac"], "Test Mac");
    let home = World::messages(&frames, "snapshot");
    assert_eq!(home.len(), 1);
    assert_eq!((home[0]["env"]["keep"].as_str(), home[0]["message"]["home"]["rows"][0]["id"].as_str()), (Some("home"), Some("a")));
    assert_eq!(world.store.0.lock().unwrap().as_ref().unwrap().phones.len(), 1, "saved");
}

#[test]
fn link_tells_the_phone_whether_the_mac_is_staying_awake() {
    let mut world = World::paired();
    let first = World::messages(&world.sent(), "snapshot");
    assert!(first[0]["message"]["home"].get("awake").is_none(), "a host that can't say leaves it out");
    *world.host.awake.lock().unwrap() = Some(json!({"enabled": true, "holding": true, "supported": true, "line": "x"}));
    world.advance(world.link.poll_ms);
    world.link.tick();
    let holding = World::messages(&world.sent(), "snapshot");
    assert_eq!(holding[0]["message"]["home"]["awake"], json!({"enabled": true, "holding": true}), "a change is sent");
    *world.host.awake.lock().unwrap() = Some(json!({"enabled": false, "holding": false, "supported": true, "line": "x"}));
    world.advance(world.link.poll_ms);
    world.link.tick();
    assert_eq!(World::messages(&world.sent(), "snapshot")[0]["message"]["home"]["awake"], json!({"enabled": false, "holding": false}));
    *world.host.awake.lock().unwrap() = Some(json!({"enabled": true, "holding": false, "supported": false, "line": "x"}));
    world.advance(world.link.poll_ms);
    world.link.tick();
    assert!(World::messages(&world.sent(), "snapshot")[0]["message"]["home"].get("awake").is_none(), "nothing to say where it can't hold");
}

#[test]
fn link_a_wrong_code_learns_nothing_and_a_stranger_hears_unpaired() {
    let mut world = World::paired();
    let stranger = KeyPair::generate();
    let env = proto::seal(&json!({"t": "pair", "code": "guess", "device": "x"}), &stranger, &world.link.state.keys.public_key, None, false).unwrap();
    let frames = world.send_env(&env);
    assert!(world.link.waiting().is_none());
    assert_eq!(frames.iter().filter(|f| f["t"] == "send").count(), 0, "a guess gets no answer");
    let hello = proto::seal(&json!({"t": "hello"}), &stranger, &world.link.state.keys.public_key, None, false).unwrap();
    world.link.on_frame(&json!({"t": "env", "env": hello}).to_string());
    let out: Vec<Value> = world.link.out.drain(..).map(|f| serde_json::from_str(&f).unwrap()).collect();
    let sealed = out.iter().find(|f| f["t"] == "send").expect("an answer for the removed phone");
    assert_eq!(proto::open(&sealed["env"], &stranger, &world.link.state.keys.public_key), Some(json!({"t": "unpaired"})));
}

#[test]
fn link_runs_an_action_once_however_often_it_arrives() {
    let mut world = World::paired();
    *world.host.running.lock().unwrap() = true;
    let action = json!({"t": "action", "id": "x1", "at": world.now(), "macAt": world.now(), "action": {"kind": "stop", "projectId": "p1", "channelId": "a"}});
    let env = world.envelope(action.clone());
    let first = world.send_env(&env);
    assert_eq!(World::messages(&first, "ack")[0]["message"]["state"], "done");
    assert_eq!(World::messages(&first, "ack")[0]["env"]["keep"], "q");
    assert!(first.iter().any(|f| f["t"] == "got"), "a receipt after it's recorded");
    let replay = world.send_env(&env);
    // The relay delivering it again (Dervo closed before the receipt) gets the recorded answer, not a second run.
    assert_eq!(World::messages(&replay, "ack")[0]["message"]["state"], "done");
    assert_eq!(replay.last().unwrap()["t"], "got");
    let again = world.from_phone(action);
    assert_eq!(World::messages(&again, "ack")[0]["message"]["state"], "done", "the same id answers the same");
    assert_eq!(world.host.count("task_stop"), 1);
}

#[test]
fn link_a_late_stop_is_expired_and_a_send_queues_through_the_host() {
    let mut world = World::paired();
    *world.host.running.lock().unwrap() = true;
    let late = world.from_phone(json!({"t": "action", "id": "s1", "at": world.now(), "macAt": world.now() - 3 * 60_000, "action": {"kind": "stop", "projectId": "p1", "channelId": "a"}}));
    assert_eq!(World::messages(&late, "ack")[0]["message"]["state"], "expired");
    assert_eq!(world.host.count("task_stop"), 0);
    world.from_phone(json!({"t": "action", "id": "s2", "at": world.now(), "macAt": world.now(), "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "hi"}}));
    world.from_phone(json!({"t": "action", "id": "h1", "at": world.now(), "macAt": world.now(), "action": {"kind": "home", "projectId": "p1", "channelId": "home", "text": "start it"}}));
    let calls = world.host.calls.lock().unwrap().clone();
    let request = calls.iter().find(|(op, _)| op == "coding_request").unwrap();
    assert_eq!(request.1, json!({"clientId": "phone-s2", "text": "hi", "projectId": "p1", "channelId": "a", "queueIfBusy": true}));
    let home = calls.iter().find(|(op, _)| op == "orchestrator_send").unwrap();
    assert_eq!(home.1, json!({"projectId": "p1", "profileId": "prof", "text": "start it", "clientId": "phone-h1"}));
}

#[test]
fn link_reads_fast_while_a_phone_speaks_and_heartbeats_live_only() {
    let mut world = World::paired();
    world.sent();
    // Just allowed, so active: an unchanged Home isn't sent again until the heartbeat.
    world.advance(world.link.poll_ms);
    world.link.tick();
    assert!(World::messages(&world.sent(), "snapshot").is_empty());
    world.advance(HEARTBEAT_MS);
    world.link.tick();
    let beat = World::messages(&world.sent(), "snapshot");
    assert_eq!(beat.len(), 1);
    assert!(beat[0]["env"].get("keep").is_none(), "a heartbeat is live only");
    // A thread that starts waiting on you: kept, and it wakes the phone.
    world.host.threads.lock().unwrap().push(thread("b", "needs_you"));
    world.advance(world.link.poll_ms);
    world.link.tick();
    let changed = World::messages(&world.sent(), "snapshot");
    assert_eq!((changed[0]["env"]["keep"].as_str(), changed[0]["env"]["wake"].as_bool()), (Some("home"), Some(true)));
    // Quiet for 90 s: slow reads, and no heartbeat.
    world.advance(ACTIVE_MS + 1);
    world.link.tick();
    world.sent();
    let reads = world.host.count("projects");
    world.advance(world.link.poll_ms);
    world.link.tick();
    assert_eq!(world.host.count("projects"), reads, "no fast read with no phone open");
    world.advance(IDLE_POLL_MS);
    world.link.tick();
    assert!(world.host.count("projects") > reads);
    assert!(World::messages(&world.sent(), "snapshot").is_empty(), "no heartbeat while no phone is open");
    // A hello switches to the fast read at once and sends Home again.
    let hello = world.from_phone(json!({"t": "hello"}));
    assert_eq!(World::messages(&hello, "snapshot").len(), 1);
}

#[test]
fn link_unpair_all_tells_each_phone_held_and_leaves_the_relays_list() {
    let mut world = World::paired();
    world.sent();
    world.link.unpair_all();
    let frames = world.sent();
    assert!(frames.iter().all(|f| f["t"] != "allow"), "the old room's list stays, so the relay keeps the removal");
    let gone = World::messages(&frames, "unpaired");
    assert_eq!((gone.len(), gone[0]["env"]["keep"].as_str()), (1, Some("q")));
    assert!(world.link.phones().is_empty());
}

fn challenge(pk: &str) -> String {
    json!({"t": "challenge", "challenge": {"pk": pk, "n": proto::random_token(24)}}).to_string()
}
fn auths(link: &mut Link<FakeHost, MemoryStore>) -> usize {
    link.out.drain(..).filter(|f| f.contains("\"auth\"")).count()
}

#[test]
fn link_signs_in_once_per_socket_and_never_proves_a_phones_key() {
    // Security review, 8 Oct, High: the proof is a box under the key the Mac shares with `pk`, so a relay that picked a
    // phone's key could forge that phone's messages.
    let mut world = World::paired();
    let stranger = KeyPair::generate().public_key;
    world.link.on_frame(&challenge(&stranger));
    assert_eq!(auths(&mut world.link), 0, "not while signed in");
    world.link.lost();
    world.link.on_frame(&challenge(&world.phone.public_key.clone()));
    assert_eq!(auths(&mut world.link), 0, "never for a paired phone's key");
    assert!(std::mem::take(&mut world.link.refuse), "and the socket closes unanswered");
    let own = world.link.state.keys.public_key.clone();
    world.link.on_frame(&challenge(&own));
    assert_eq!(auths(&mut world.link), 0, "never for the Mac's own key");
    world.link.on_frame(&challenge(&stranger));
    assert_eq!(auths(&mut world.link), 1);
    world.link.on_frame(&challenge(&KeyPair::generate().public_key));
    assert_eq!(auths(&mut world.link), 0, "once per socket");
    world.link.lost();
    world.link.on_frame(&challenge(&stranger));
    assert_eq!(auths(&mut world.link), 1, "a new socket signs in again");
}

#[test]
fn link_rekeys_after_ready_and_with_each_code_and_tells_a_removed_phone_when_back() {
    let mut world = World::paired();
    world.sent();
    let before = world.link.state.room_key.clone();
    let offer = world.link.pairing_offer();
    let frames = world.sent();
    assert_ne!(world.link.state.room_key, before);
    assert_eq!(offer["offer"]["key"], world.link.state.room_key.as_str());
    assert!(frames.iter().any(|f| f["t"] == "rekey" && f["key"] == world.link.state.room_key.as_str()));
    // Removed while the relay was away: told after the next ready, after allow and rekey.
    world.link.lost();
    let pk = world.phone.public_key.clone();
    world.link.unpair(&pk);
    assert_eq!(world.link.state.notices, vec![pk.clone()]);
    world.link.on_frame(r#"{"t":"ready","allowed":true}"#);
    let frames = world.sent();
    let order: Vec<&str> = frames.iter().map(|f| f["t"].as_str().unwrap()).collect();
    assert_eq!(&order[..3], ["allow", "rekey", "send"]);
    assert_eq!((frames[2]["message"]["t"].as_str(), frames[2]["env"]["keep"].as_str()), (Some("unpaired"), Some("q")));
    assert!(world.link.state.notices.is_empty());
}

#[test]
fn link_keeps_a_bounded_record_and_saves_a_hello_soon_not_at_once() {
    let mut world = World::paired();
    let saved = || world_store_seen(&world.store);
    let before = saved();
    world.from_phone(json!({"t": "hello"}));
    assert_eq!(world_store_seen(&world.store), before, "a hello's nonce waits for the next save");
    world.advance(1000);
    world.link.tick();
    assert_eq!(world_store_seen(&world.store), before + 1);
    for _ in 0..5100 { world.link.state.seen.insert(proto::random_token(24), world.now()); }
    world.from_phone(json!({"t": "action", "id": "a1", "at": world.now(), "macAt": world.now(), "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "x"}}));
    assert!(world_store_seen(&world.store) <= 5000, "capped");
}
fn world_store_seen(store: &MemoryStore) -> usize { store.0.lock().unwrap().as_ref().map(|s| s.seen.len()).unwrap_or(0) }

#[test]
fn link_another_mac_on_the_room_stops_this_one() {
    let mut world = World::paired();
    world.link.on_frame(r#"{"t":"error","code":"replaced","detail":"x"}"#);
    assert_eq!(world.link.halted.as_deref(), Some("replaced"));
}

#[test]
fn store_keeps_secrets_apart_and_reloads() {
    let dir = std::env::temp_dir().join(format!("dervo-phone-store-{}", proto::random_token(6)));
    std::fs::create_dir_all(&dir).unwrap();
    let store = FileStore::new(dir.join("phone-link.json"), Box::new(FileSecrets(dir.join("keys.json"))));
    let state = LinkState::new("wss://relay.example");
    store.save(&state).unwrap();
    let public = std::fs::read_to_string(dir.join("phone-link.json")).unwrap();
    assert!(!public.contains(&state.keys.secret_key) && !public.contains(&state.room_key), "no secret in the state file");
    #[cfg(unix)]
    { use std::os::unix::fs::PermissionsExt; assert_eq!(std::fs::metadata(dir.join("keys.json")).unwrap().permissions().mode() & 0o777, 0o600); }
    let again = FileStore::new(dir.join("phone-link.json"), Box::new(FileSecrets(dir.join("keys.json"))));
    assert_eq!(again.load().unwrap(), Some(state));
    // Secrets for another key pair (a different Keychain, say): start fresh rather than half-load.
    std::fs::write(dir.join("keys.json"), json!({"publicKey": "x", "secretKey": "y", "roomKey": "z"}).to_string()).unwrap();
    assert_eq!(again.load().unwrap(), None);
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn link_paired_is_held_and_a_paired_phone_asking_again_hears_it_again() {
    let mut world = World::paired();
    let frames = world.sent();
    assert_eq!(World::messages(&frames, "paired")[0]["env"]["keep"], "q", "held for a phone that locked meanwhile");
    let again = world.from_phone(json!({"t": "pair", "code": "used", "device": "Test iPhone"}));
    let paired = World::messages(&again, "paired");
    assert_eq!((paired.len(), paired[0]["env"]["keep"].as_str()), (1, Some("q")));
    assert!(world.link.waiting().is_none(), "no new request for a paired phone");
}

#[test]
fn link_dates_an_action_by_the_mac_s_clock_alone() {
    let mut world = World::paired();
    *world.host.running.lock().unwrap() = true;
    let now = world.now();
    let stop = |id: &str, at: i64, mac_at: Value| json!({"t": "action", "id": id, "at": at, "sent": at, "macAt": mac_at, "action": {"kind": "stop", "projectId": "p1", "channelId": "a"}});
    let send = |id: &str, at: i64, mac_at: i64| json!({"t": "action", "id": id, "at": at, "sent": at, "macAt": mac_at, "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "hi"}});
    // A phone 3 days fast or 2 hours slow sends as usual: only its macAt counts.
    assert_eq!(World::messages(&world.from_phone(send("f1", now + 3 * 86_400_000, now)), "ack")[0]["message"]["state"], "done");
    assert_eq!(World::messages(&world.from_phone(send("s1", now - 7_200_000, now)), "ack")[0]["message"]["state"], "done");
    // No macAt: refused, with how to fix it.
    let none = world.from_phone(stop("n1", now, Value::Null));
    let ack = &World::messages(&none, "ack")[0]["message"];
    assert_eq!(ack["state"], "refused");
    assert!(ack["detail"].as_str().unwrap().starts_with("Dervo on your phone is too old"));
    // A macAt more than 5 minutes past now: refused.
    let ahead = world.from_phone(stop("a1", now, json!(now + 6 * 60_000)));
    assert!(World::messages(&ahead, "ack")[0]["message"]["detail"].as_str().unwrap().starts_with("This phone's time changed"));
    // Older than Stop's 2 minutes on the Mac's clock: expired, whatever the phone's clock or the relay say.
    let late = world.from_phone_held(stop("l1", now, json!(now - 3 * 60_000)), 0);
    assert_eq!(World::messages(&late, "ack")[0]["message"]["state"], "expired");
    assert_eq!(world.host.count("task_stop"), 0);
    assert_eq!(world.host.count("coding_request"), 2);
}

#[test]
fn link_a_withheld_action_from_a_fast_phone_is_expired_and_a_replay_after_pruning_too() {
    let mut world = World::paired();
    let now = world.now();
    // 3 days fast, withheld by the relay and delivered 25 h later: its macAt says it's a day old.
    let at = now + 3 * 86_400_000;
    let env = world.envelope(json!({"t": "action", "id": "w1", "at": at, "sent": at, "macAt": now, "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "hi"}}));
    world.advance(25 * 3_600_000);
    assert_eq!(World::messages(&world.send_env(&env), "ack")[0]["message"]["state"], "expired");
    // A fresh one, answered, then replayed once its records are pruned: expired.
    let fresh = world.envelope(json!({"t": "action", "id": "w2", "at": 0, "macAt": world.now(), "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "hi"}}));
    assert_eq!(World::messages(&world.send_env(&fresh), "ack")[0]["message"]["state"], "done");
    world.advance(SEEN_MS + 1);
    world.from_phone(json!({"t": "hello"}));
    world.advance(1500);
    world.link.tick();
    assert!(!world.link.state.seen.contains_key(fresh["n"].as_str().unwrap()), "pruned");
    assert_eq!(World::messages(&world.send_env(&fresh), "ack")[0]["message"]["state"], "expired");
    assert_eq!(world.host.count("coding_request"), 1);
}

#[test]
fn link_keeps_an_answer_s_records_past_a_macat_ahead_of_now_in_one_save() {
    let mut world = World::paired();
    let mac_at = world.now() + 4 * 60_000; // within the 5 minutes
    let env = world.envelope(json!({"t": "action", "id": "k1", "at": 0, "macAt": mac_at, "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "hi"}}));
    world.send_env(&env);
    let saved = world.store.0.lock().unwrap().clone().unwrap();
    assert_eq!(saved.done[&format!("{}:k1", world.phone.public_key)].at, mac_at);
    assert_eq!(saved.seen[env["n"].as_str().unwrap()], mac_at);
}

#[test]
fn link_forgets_a_removed_phone_s_answers() {
    let mut world = World::paired();
    world.from_phone(json!({"t": "action", "id": "u1", "at": 0, "macAt": world.now(), "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "hi"}}));
    let other = world.pair_another("Other");
    let key = proto::seal(&json!({"t": "action", "id": "u2", "at": 0, "macAt": world.now(), "action": {"kind": "send", "projectId": "p1", "channelId": "a", "text": "hi"}}),
        &other, &world.link.state.keys.public_key, Some("q"), false).unwrap();
    world.link.on_frame(&json!({"t": "env", "env": key}).to_string());
    assert_eq!(world.link.state.done.len(), 2);
    world.link.unpair(&world.phone.public_key.clone());
    assert_eq!(world.link.state.done.keys().cloned().collect::<Vec<_>>(), [format!("{}:u2", other.public_key)], "only the removed phone's");
    assert!(world.store.0.lock().unwrap().as_ref().unwrap().done.len() == 1, "saved");
}

#[test]
fn link_trims_a_saved_state_with_too_many_phones_and_tells_the_oldest() {
    let mut state = LinkState::new("ws://127.0.0.1:1");
    let keys: Vec<KeyPair> = (0..22).map(|_| KeyPair::generate()).collect();
    state.phones = keys.iter().enumerate().map(|(i, k)| Phone { pk: k.public_key.clone(), device: format!("Phone {i}"), paired_at: 1000 + i as i64, approvals: false }).rev().collect();
    let host = FakeHost::default();
    let link = Link::new(host, MemoryStore::default(), state, "Test Mac", Box::new(|| 1_000_000_000));
    assert_eq!(link.phones().len(), 20);
    assert_eq!(link.phones()[0].pk, keys[2].public_key, "the newest stay");
    assert_eq!(link.state.notices, [keys[0].public_key.clone(), keys[1].public_key.clone()], "told on the next connect");
}

#[test]
fn link_answers_a_replayed_envelope_that_was_running_as_unknown() {
    let mut world = World::paired();
    *world.host.running.lock().unwrap() = true;
    let env = world.envelope(json!({"t": "action", "id": "z1", "at": world.now(), "macAt": world.now(), "action": {"kind": "stop", "projectId": "p1", "channelId": "a"}}));
    // Seen and marked running, then Dervo closed before the answer was recorded.
    let key = format!("{}:z1", world.phone.public_key);
    world.link.state.seen.insert(env["n"].as_str().unwrap().into(), world.now());
    world.link.state.running.insert(key.clone(), world.now());
    let frames = world.send_env(&env);
    assert_eq!(World::messages(&frames, "ack")[0]["message"]["state"], "refused");
    assert_eq!(world.host.count("task_stop"), 0);
    assert!(!world.link.state.running.contains_key(&key));
}

#[test]
fn link_never_reruns_an_action_that_was_running_when_dervo_closed() {
    let mut world = World::paired();
    *world.host.running.lock().unwrap() = true;
    world.from_phone(json!({"t": "action", "id": "r1", "at": world.now(), "macAt": world.now(), "action": {"kind": "stop", "projectId": "p1", "channelId": "a"}}));
    assert_eq!(*world.host.running_at_stop.lock().unwrap(), [1], "saved as running before the host ran it");
    assert!(world.store.0.lock().unwrap().as_ref().unwrap().running.is_empty(), "and cleared after");
    // A restart with an action still marked running: it's answered, never run again.
    let key = format!("{}:r2", world.phone.public_key);
    world.link.state.running.insert(key.clone(), world.now());
    let frames = world.from_phone(json!({"t": "action", "id": "r2", "at": world.now(), "macAt": world.now(), "action": {"kind": "stop", "projectId": "p1", "channelId": "a"}}));
    let ack = &World::messages(&frames, "ack")[0]["message"];
    assert_eq!(ack["state"], "refused");
    assert!(ack["detail"].as_str().unwrap().starts_with("Dervo closed while doing this"));
    assert_eq!(world.host.count("task_stop"), 1);
    assert!(!world.link.state.running.contains_key(&key));
    assert_eq!(world.link.state.done[&key].state, "refused", "and the answer is kept for a repeat");
}

#[test]
fn link_a_stop_only_ends_runs_that_had_started_when_you_tapped() {
    let mut world = World::paired();
    let tapped = world.now();
    let secs = |ms: i64| ms as f64 / 1000.0;
    let user = |ms: i64| json!({"role": "user", "created_at": secs(ms)});
    let agent = |ms: i64| json!({"role": "assistant", "created_at": secs(ms)});
    *world.host.tasks.lock().unwrap() = Some(json!([
        // Dated by your latest message: its current run started before the tap, whatever the agent said since.
        {"id": "before", "state": "working", "messages": [user(tapped - 600_000), user(tapped - 60_000), agent(tapped + 90_000)]},
        {"id": "slack", "state": "working", "messages": [user(tapped + 1_500)]},
        {"id": "late", "state": "working", "messages": [user(tapped + 3_000)]},
        // A new message after the tap started a new run, which this Stop wasn't for.
        {"id": "after", "state": "working", "messages": [user(tapped - 600_000), user(tapped + 60_000)]},
        {"id": "undated", "state": "working", "messages": [agent(tapped + 60_000)]},
        {"id": "done", "state": "stopped", "messages": []},
    ]));
    // Tapped a minute ago by the Mac's clock; the phone's own clock is 3 minutes behind and doesn't count.
    world.advance(60_000);
    let at = tapped - 3 * 60_000;
    world.from_phone(json!({"t": "action", "id": "k1", "at": at, "sent": at, "macAt": tapped, "action": {"kind": "stop", "projectId": "p1", "channelId": "a"}}));
    let calls = world.host.calls.lock().unwrap().clone();
    let stopped: Vec<&str> = calls.iter().filter(|(op, _)| op == "task_stop").map(|(_, args)| args["taskId"].as_str().unwrap()).collect();
    assert_eq!(stopped, ["before", "slack", "undated"]);
}

#[test]
fn link_the_21st_phone_removes_the_longest_paired_one_and_tells_it() {
    let mut world = World::paired();
    let first = world.phone.public_key.clone();
    let mut others = Vec::new();
    for i in 0..19 { world.advance(1000); others.push(world.pair_another(&format!("Phone {i}"))); }
    assert_eq!(world.link.phones().len(), 20);
    world.link.out.clear();
    world.advance(1000);
    world.pair_another("Phone 20");
    assert_eq!(world.link.phones().len(), 20);
    assert!(!world.link.phones().iter().any(|p| p.pk == first), "the longest-paired one made room");
    let frames: Vec<Value> = world.link.out.drain(..).map(|f| serde_json::from_str(&f).unwrap()).collect();
    let told = frames.iter().position(|f| f["t"] == "send" && f["env"]["to"] == first).expect("the removed phone is told");
    assert_eq!(frames[told]["env"]["keep"], "q");
    assert_eq!(proto::open(&frames[told]["env"], &world.phone, &world.link.state.keys.public_key), Some(json!({"t": "unpaired"})));
    let allow = frames.iter().position(|f| f["t"] == "allow").unwrap();
    assert!(told < allow, "told before the allow that drops it");
    assert_eq!(frames[allow]["phones"].as_array().unwrap().len(), 20);
    assert!(!frames[allow]["phones"].as_array().unwrap().iter().any(|p| *p == json!(first)));
}

#[test]
fn link_says_a_thread_it_never_listed_is_missing_and_nothing_for_other_failures() {
    let mut world = World::paired();
    world.host.threads.lock().unwrap().push(thread("b", "idle"));
    let mut unnamed = thread("c", "idle");
    unnamed["name"] = json!("");
    world.host.threads.lock().unwrap().push(unnamed);
    world.from_phone(json!({"t": "hello"}));
    let gone = world.from_phone(json!({"t": "open", "projectId": "p1", "channelId": "gone"}));
    let thread = &World::messages(&gone, "thread")[0]["message"]["thread"];
    assert_eq!((thread["missing"].as_bool(), thread["messages"].as_array().map(Vec::len), thread["channelId"].as_str()), (Some(true), Some(0), Some("gone")));
    // "b" is listed, so its failed read is a hiccup, not a missing thread: the phone says it couldn't load it.
    let broken = world.from_phone(json!({"t": "open", "projectId": "p1", "channelId": "b"}));
    assert!(World::messages(&broken, "thread").is_empty());
    // Listed without a name still counts as listed.
    let unnamed = world.from_phone(json!({"t": "open", "projectId": "p1", "channelId": "c"}));
    assert!(World::messages(&unnamed, "thread").is_empty());
}

// ---------- answers, approvals and finished pushes (spec rust-link-spec-approvals-20261009, link.test.ts) ----------

/// The four things a thread can wait on you for, as companion/mac/link.test.ts sets them up.
fn asking_tasks(scope: &str, review: &str) -> Value {
    json!([
        {"id": "q", "state": "working", "questions": [{"id": "q1", "ask": "Should an archived idea keep its threads?", "state": "pending", "canAnswer": true, "canSkip": true,
            "options": [{"id": "o1", "label": "Keep them", "detail": "They stay listed"}, {"id": "o2", "label": "Archive them too", "detail": ""}]}]},
        {"id": "s", "state": scope, "scope_version": 3, "title": "Move ideas into SQLite", "files": ["backend/ideas.py"]},
        {"id": "r", "state": review, "title": "Fix the live board", "check_profile": "node-test"},
        {"id": "w", "state": "needs_permission", "scope_version": 1, "title": "Tidy the whole project", "files": [], "project_access": true},
    ])
}
fn detail(unrecorded: Value) -> Value {
    json!({"evidence": {"attempts": [{"number": 2, "state": "verified", "test": {"exit_status": 0}, "diff": {"a.ts": "", "b.ts": ""}, "unrecorded": unrecorded}]}})
}

impl World {
    fn act(&mut self, id: &str, action: Value) -> Value {
        let frames = self.from_phone(json!({"t": "action", "id": id, "at": self.now(), "macAt": self.now(), "action": action}));
        World::messages(&frames, "ack").first().expect("an answer")["message"].clone()
    }
    fn opened(&mut self) -> Value {
        let frames = self.from_phone(json!({"t": "open", "projectId": "p1", "channelId": "a"}));
        World::messages(&frames, "thread").last().expect("the thread")["message"]["thread"].clone()
    }
}

#[test]
fn link_the_phone_answers_a_question_and_approves_and_accepts_only_once_you_allow_it() {
    let mut world = World::paired();
    *world.host.tasks.lock().unwrap() = Some(asking_tasks("needs_permission", "ready_for_review"));
    world.host.reply("task_detail", detail(json!([])));
    world.host.reply("task_question_answer", json!({"delivery": "sent"}));
    world.host.reply("task_approve", json!({}));
    world.host.reply("task_deny", json!({}));
    world.host.reply("task_review", json!({"state": "idle", "commit": {"sha": "abc"}}));
    let thread = world.opened();
    let asks = thread["asks"].as_array().unwrap();
    assert_eq!(asks.iter().map(|a| a["kind"].as_str().unwrap()).collect::<Vec<_>>(), ["question", "scope", "review", "scope"]);
    assert_eq!(asks.iter().filter(|a| a["kind"] == "scope").map(|a| a["phone"].as_bool().unwrap()).collect::<Vec<_>>(), [true, false], "a scope beyond its own files is approved on the Mac");
    assert_eq!(asks[0], json!({"kind": "question", "taskId": "q", "questionId": "q1", "ask": "Should an archived idea keep its threads?", "canSkip": true,
        "options": [{"id": "o1", "label": "Keep them", "detail": "They stay listed"}, {"id": "o2", "label": "Archive them too", "detail": ""}]}));
    assert_eq!(asks[1], json!({"kind": "scope", "taskId": "s", "scopeVersion": 3, "what": "Move ideas into SQLite", "files": ["backend/ideas.py"], "phone": true}));
    assert_eq!(asks[2], json!({"kind": "review", "taskId": "r", "attemptNumber": 2, "what": "Fix the live board", "files": 2, "checked": true, "phone": true}));
    assert!(thread.get("approvals").is_none(), "approvals are off until you turn them on");

    let answered = world.act("x1", json!({"kind": "answer", "projectId": "p1", "channelId": "a", "taskId": "q", "questionId": "q1", "answer": {"kind": "option", "optionId": "o1"}}));
    assert_eq!((answered["state"].as_str(), answered["detail"].as_str()), (Some("done"), Some("Answered.")));
    assert_eq!(world.host.args("task_question_answer"), Some(json!({"projectId": "p1", "channelId": "a", "taskId": "q", "questionId": "q1", "clientId": "phone-x1", "answer": {"kind": "option", "optionId": "o1"}})));

    let barred = world.act("x2", json!({"kind": "scope", "projectId": "p1", "channelId": "a", "taskId": "s", "scopeVersion": 3, "decision": "approve"}));
    assert_eq!((barred["state"].as_str(), barred["detail"].as_str()), (Some("refused"), Some("This phone can't approve yet. On your Mac, open Dervo, then Settings, Phone, and turn on approvals for it.")));
    assert_eq!(world.host.count("task_approve"), 0);

    let reads = world.host.count("task_detail");
    world.link.out.clear();
    assert!(world.link.set_approvals(&world.phone.public_key.clone(), true));
    assert!(!world.link.set_approvals("not-a-phone", true));
    assert!(world.link.phones()[0].approvals);
    let resent = World::messages(&world.sent(), "thread");
    assert_eq!(resent.last().expect("the open thread goes again at once")["message"]["thread"]["approvals"], true);
    assert_eq!(world.host.count("task_detail"), reads, "a result that looks the same isn't read again");

    let broad = world.act("x3", json!({"kind": "scope", "projectId": "p1", "channelId": "a", "taskId": "w", "scopeVersion": 1, "decision": "approve"}));
    assert_eq!((broad["state"].as_str(), broad["detail"].as_str()), (Some("refused"), Some("This run asks for more than its own files, so approve it on your Mac, where you can see what it covers.")));
    let stale = world.act("x4", json!({"kind": "scope", "projectId": "p1", "channelId": "a", "taskId": "s", "scopeVersion": 2, "decision": "deny"}));
    assert_eq!(stale["state"], "refused", "a decision about an older scope never touches the current one");
    assert_eq!((world.host.count("task_approve"), world.host.count("task_deny")), (0, 0));
    let approved = world.act("x5", json!({"kind": "scope", "projectId": "p1", "channelId": "a", "taskId": "s", "scopeVersion": 3, "decision": "approve"}));
    assert_eq!((approved["state"].as_str(), approved["detail"].as_str()), (Some("done"), Some("Approved. The run is starting.")));
    assert_eq!(world.host.args("task_approve"), Some(json!({"projectId": "p1", "channelId": "a", "taskId": "s", "scopeVersion": 3})));
    *world.host.tasks.lock().unwrap() = Some(asking_tasks("working", "ready_for_review"));

    world.host.reply("task_detail", detail(json!(["c.ts"])));
    let sheet = world.act("x6", json!({"kind": "review", "projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 2, "decision": "approved"}));
    assert_eq!((sheet["state"].as_str(), sheet["detail"].as_str()), (Some("refused"), Some("This result needs the review sheet on your Mac to accept it.")), "the Mac checks Accept itself, from evidence read fresh");
    let older = world.act("x7", json!({"kind": "review", "projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 1, "decision": "rejected"}));
    assert_eq!((older["state"].as_str(), older["detail"].as_str()), (Some("refused"), Some("This result changed on your Mac since you looked. Check the thread.")));
    assert_eq!(world.host.count("task_review"), 0);
    world.host.reply("task_detail", detail(json!([])));
    let accepted = world.act("x8", json!({"kind": "review", "projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 2, "decision": "approved"}));
    assert_eq!((accepted["state"].as_str(), accepted["detail"].as_str()), (Some("done"), Some("Accepted and committed.")));
    assert_eq!(world.host.args("task_review"), Some(json!({"projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 2, "decision": "approved"})));
    *world.host.tasks.lock().unwrap() = Some(asking_tasks("working", "idle"));
    let again = world.act("x9", json!({"kind": "review", "projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 2, "decision": "approved"}));
    assert_eq!((again["state"].as_str(), again["detail"].as_str()), (Some("refused"), Some("This result was already decided. Check the thread for where it is now.")));
    assert_eq!(world.host.count("task_review"), 1);
}

#[test]
fn link_asking_for_changes_sends_your_note_and_a_failed_note_keeps_the_decision() {
    let mut world = World::paired();
    let pk = world.phone.public_key.clone();
    world.link.set_approvals(&pk, true);
    *world.host.tasks.lock().unwrap() = Some(asking_tasks("working", "ready_for_review"));
    world.host.reply("task_detail", detail(json!(["c.ts"])));
    world.host.reply("task_review", json!({"state": "ready_for_review", "review": "rejected"}));
    world.host.reply("task_continue", json!({}));
    // Asking for changes doesn't need the review sheet, even where Accept does.
    let sent = world.act("c1", json!({"kind": "review", "projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 2, "decision": "rejected", "text": "Keep the old colours"}));
    assert_eq!((sent["state"].as_str(), sent["detail"].as_str()), (Some("done"), Some("Sent back with your changes.")));
    assert_eq!(world.host.args("task_continue"), Some(json!({"projectId": "p1", "channelId": "a", "taskId": "r", "text": "Keep the old colours"})));
    world.host.replies.lock().unwrap().remove("task_continue");
    let failed = world.act("c2", json!({"kind": "review", "projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 2, "decision": "rejected", "text": "Smaller"}));
    assert_eq!(failed["detail"], "Marked for changes, but your note didn't send. Say what to change in the thread.");
    let bare = world.act("c3", json!({"kind": "review", "projectId": "p1", "channelId": "a", "taskId": "r", "attemptNumber": 2, "decision": "rejected", "text": "  "}));
    assert_eq!(bare["detail"], "Marked for changes. Say what to change in the thread.");
    assert_eq!(world.host.count("task_continue"), 2);
    // Answers don't need approvals, and their delivery decides the words.
    world.link.set_approvals(&pk, false);
    world.host.reply("task_question_answer", json!({"delivery": "queued"}));
    let queued = world.act("c4", json!({"kind": "answer", "projectId": "p1", "channelId": "a", "taskId": "q", "questionId": "q1", "answer": {"kind": "text", "text": "Keep them"}}));
    assert_eq!((queued["state"].as_str(), queued["detail"].as_str()), (Some("queued"), Some("Answer saved. It goes in when the agent is free.")));
    world.host.reply("task_question_answer", json!({"delivery": "not_sent"}));
    let lost = world.act("c5", json!({"kind": "answer", "projectId": "p1", "channelId": "a", "taskId": "q", "questionId": "q1", "answer": {"kind": "skip"}}));
    assert_eq!((lost["state"].as_str(), lost["detail"].as_str()), (Some("done"), Some("Skipped.")));
    let unsent = world.act("c6", json!({"kind": "answer", "projectId": "p1", "channelId": "a", "taskId": "q", "questionId": "q1", "answer": {"kind": "option", "optionId": "o2"}}));
    assert_eq!(unsent["state"], "refused");
    assert_eq!(unsent["detail"], "Your answer is saved on your Mac, but it didn't reach the agent. Open the thread there to send it.");
}

#[test]
fn link_a_finished_thread_sends_a_quieter_push_and_needing_you_wins() {
    let mut world = World::paired();
    let push = |frames: &[Value]| World::messages(frames, "snapshot").first().map(|f| (f["env"].get("wake").cloned(), f["env"].get("news").cloned()));
    world.link.out.clear();
    *world.host.threads.lock().unwrap() = vec![json!({"id": "a", "projectId": "p1", "name": "a", "state": "finished", "unread": 1, "at": 2})];
    world.link.publish();
    assert_eq!(push(&world.sent()), Some((None, Some(json!(true)))), "the thread that just finished");
    *world.host.threads.lock().unwrap() = vec![json!({"id": "a", "projectId": "p1", "name": "a", "state": "finished", "unread": 2, "at": 3})];
    world.link.publish();
    assert_eq!(push(&world.sent()), Some((None, None)), "nothing newly finished");
    *world.host.threads.lock().unwrap() = vec![thread("b", "needs_you"), json!({"id": "c", "projectId": "p1", "name": "c", "state": "finished", "unread": 1, "at": 4})];
    world.link.publish();
    assert_eq!(push(&world.sent()), Some((Some(json!(true)), None)), "one push only, and needing you wins");
}

#[test]
fn link_the_first_home_after_start_never_says_finished() {
    let host = FakeHost::default();
    host.threads.lock().unwrap().push(json!({"id": "a", "projectId": "p1", "name": "a", "state": "finished", "unread": 1, "at": 1}));
    let mut state = LinkState::new("ws://127.0.0.1:1");
    let phone = KeyPair::generate();
    state.phones.push(Phone { pk: phone.public_key.clone(), device: "Old iPhone".into(), paired_at: 1, approvals: false });
    let mut link = Link::new(host, MemoryStore::default(), state, "Mac", Box::new(|| 1_000_000_000));
    link.on_frame(r#"{"t":"ready"}"#);
    let snapshot: Value = link.out.iter().map(|f| serde_json::from_str::<Value>(f).unwrap()).find(|f| f["t"] == "send").expect("Home");
    assert!(snapshot["env"].get("news").is_none() && snapshot["env"].get("wake").is_none());
}

#[test]
fn link_a_phone_saved_before_approvals_reads_as_off() {
    let phone: Phone = serde_json::from_value(json!({"pk": "k", "device": "iPhone", "pairedAt": 1})).unwrap();
    assert!(!phone.approvals);
    assert_eq!(serde_json::to_value(&phone).unwrap()["approvals"], false);
}

// ---------- end to end ----------

/// backend/host.py over its stdin and stdout, one request at a time, as the bridge drives it.
struct PyHost { child: Mutex<(std::process::ChildStdin, std::io::BufReader<std::process::ChildStdout>)>, calls: Mutex<Vec<String>>, _process: std::process::Child }
impl PyHost {
    fn start(root: &std::path::Path, data: &std::path::Path) -> Self {
        let mut process = std::process::Command::new("python3").args(["-E", "-s", "-B"]).arg(root.join("backend/host.py")).arg(data)
            .env_clear().env("PATH", std::env::var("PATH").unwrap()).env("HOME", data)
            .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null()).spawn().unwrap();
        let pipes = (process.stdin.take().unwrap(), std::io::BufReader::new(process.stdout.take().unwrap()));
        Self { child: Mutex::new(pipes), calls: Mutex::new(Vec::new()), _process: process }
    }
    fn count(&self, operation: &str) -> usize { self.calls.lock().unwrap().iter().filter(|op| *op == operation).count() }
}
impl Host for Arc<PyHost> {
    fn call(&self, operation: &str, args: Value) -> Result<Value, String> {
        use std::io::{BufRead, Write};
        self.calls.lock().unwrap().push(operation.into());
        let mut pipes = self.child.lock().unwrap();
        writeln!(pipes.0, "{}", json!({"operation": operation, "args": args})).map_err(|e| e.to_string())?;
        let mut line = String::new();
        pipes.1.read_line(&mut line).map_err(|e| e.to_string())?;
        let reply: Value = serde_json::from_str(&line).map_err(|e| format!("{e}: {line}"))?;
        match reply.get("error") { Some(Value::String(error)) => Err(error.clone()), _ => Ok(reply["result"].clone()) }
    }
}

#[test]
#[ignore = "needs Node and DERVO_COMPANION, a companion folder with node_modules"]
fn link_e2e() {
    use std::io::BufRead;
    let companion = std::path::PathBuf::from(std::env::var("DERVO_COMPANION").expect("DERVO_COMPANION"));
    let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let data = std::env::temp_dir().join(format!("dervo-phone-e2e-{}", proto::random_token(6)));
    std::fs::create_dir_all(&data).unwrap();

    // The companion's dev relay on a free port.
    let mut relay = std::process::Command::new("node").args(["--experimental-strip-types", "--no-warnings"]).arg(companion.join("relay/dev-server.ts")).arg("0")
        .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::inherit()).spawn().unwrap();
    let mut first_line = String::new();
    std::io::BufReader::new(relay.stdout.take().unwrap()).read_line(&mut first_line).unwrap();
    let relay_url = first_line.trim().rsplit(' ').next().unwrap().to_owned();
    assert!(relay_url.starts_with("ws://127.0.0.1:"), "{first_line}");

    // The real host, seeded as companion/mac/link.test.ts seeds it.
    let host = Arc::new(PyHost::start(&root, &data));
    let profile = host.call("add_profile", json!({"provider": "codex", "identity": "phone-e2e@example.test", "category": "personal"})).unwrap();
    let project = host.call("_picked_project", json!({"path": root, "profile_id": profile["id"], "model": "unverified-phone-test"})).unwrap();
    let first = host.call("create_conversation", json!({"projectId": project["id"], "name": "Export fix", "private": false})).unwrap();
    host.call("create_conversation", json!({"projectId": project["id"], "name": "Pricing copy", "private": false})).unwrap();
    host.call("queue", json!({"clientId": "seed-1", "text": "Can you look at the export?", "parentId": null, "projectId": project["id"], "channelId": first["id"]})).unwrap();

    let store = FileStore::new(data.join("phone-link.json"), Box::new(FileSecrets(data.join("phone-link-keys.json"))));
    let mut link = Link::new(host.clone(), store, LinkState::new(&relay_url), "Test Mac", Box::new(super::now_ms));
    link.poll_ms = 200;
    let mut driver = Driver::new(link);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(90);
    while !driver.connected() { assert!(std::time::Instant::now() < deadline, "the Mac never got on the relay"); driver.step(); }
    let offer = driver.link.pairing_offer();

    let mut phone = std::process::Command::new("node").args(["--experimental-strip-types", "--no-warnings"]).arg(root.join("src/phone/e2e/phone.ts"))
        .arg(offer["link"].as_str().unwrap()).env("DERVO_COMPANION", &companion)
        .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::inherit()).spawn().unwrap();
    let (lines_tx, lines) = std::sync::mpsc::channel::<Value>();
    let stdout = phone.stdout.take().unwrap();
    std::thread::spawn(move || for line in std::io::BufReader::new(stdout).lines().map_while(Result::ok) {
        if let Ok(value) = serde_json::from_str::<Value>(&line) { let _ = lines_tx.send(value); }
    });

    let mut events: std::collections::BTreeMap<String, Value> = Default::default();
    let mut digits = None;
    while !events.contains_key("unpaired") && !events.contains_key("error") {
        assert!(std::time::Instant::now() < deadline, "timed out; so far {events:?}");
        driver.step();
        if let Some(asking) = driver.link.waiting() {
            if let Some(digits) = &digits { assert_eq!(&asking.digits, digits, "both screens show the same six digits"); }
            assert_eq!(asking.device, "Rust test iPhone");
            assert!(driver.link.allow_phone(&asking.pk));
        }
        while let Ok(event) = lines.try_recv() {
            let name = event["event"].as_str().unwrap().to_owned();
            if name == "digits" { digits = event["digits"].as_str().map(str::to_owned); }
            if name == "replayed" { let pk = driver.link.phones()[0].pk.clone(); driver.link.unpair(&pk); }
            events.insert(name, event);
        }
    }
    let _ = phone.wait();
    // The relay goes away and comes back on the same address: the Mac notices, waits its backoff and signs in again.
    let port = relay_url.rsplit(':').next().unwrap().to_owned();
    let _ = relay.kill();
    let _ = relay.wait();
    while driver.connected() { assert!(std::time::Instant::now() < deadline, "never noticed the relay was gone"); driver.step(); }
    let mut relay = std::process::Command::new("node").args(["--experimental-strip-types", "--no-warnings"]).arg(companion.join("relay/dev-server.ts")).arg(&port)
        .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::inherit()).spawn().unwrap();
    while !driver.connected() { assert!(std::time::Instant::now() < deadline, "never signed in again"); driver.step(); }
    let _ = relay.kill();
    assert!(!events.contains_key("error"), "{:?}", events.get("error"));
    assert_eq!(events["home"]["mac"], "Test Mac");
    assert_eq!(events["home"]["names"], json!(["Export fix", "Pricing copy"]));
    assert_eq!(events["thread"]["messages"], json!([["you", "Can you look at the export?"]]));
    // This container has no signed-in provider, so the real host refuses the send in its own words.
    assert_eq!(events["send"]["state"], "refused", "{:?}", events["send"]);
    assert_eq!(host.count("coding_request"), 1);
    assert_eq!(host.count("orchestrator_send"), 1);
    let home_send = &events["home-send"];
    assert!(home_send["state"] == "refused" || home_send["shown"] == true, "{home_send:?}");
    assert_eq!((events["stop"]["state"].as_str(), events["stop"]["detail"].as_str()), (Some("refused"), Some("Nothing is running in this thread now.")));
    // Nothing was running, so no Stop reached a task. The replay's once-only handling is link_runs_an_action_once_however_often_it_arrives.
    assert_eq!(host.count("task_stop"), 0);
    assert!(driver.link.phones().is_empty());
    driver.close();
    std::fs::remove_dir_all(&data).ok();
}
