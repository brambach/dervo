//! Voice for Home: the app starts `python -m dervo_voice` as its own process, next to the host and never through the
//! host's request queue (bridge.rs), passes the window's commands to it and its events to the window, and restarts it
//! when it dies.
//!
//! The process speaks JSON lines both ways (backend/dervo_voice/__main__.py). Events go to the main window as
//! `dervo-voice` with the line's JSON as the payload; the supervisor adds its own
//! `{"type":"supervisor","state":"running"|"restarting"|"stopped"|"failed",...}`. Commands from the window are checked
//! against COMMANDS before they're written, so the page can't send the process anything it doesn't expect.
//!
//! Restarts back off from 1 s to 30 s and reset after a minute of running. A clean exit isn't restarted, and after
//! GIVE_UP quick failures in a row the supervisor stops and says `failed` rather than reloading the models forever.
//! Each start is a generation: a stop ends it and waits for its thread, so a quick off and on can't leave two
//! processes (and two microphones) running. Utterances survive a restart: the process
//! keeps a journal (backend/dervo_voice/journal.py) and replays what it hadn't delivered, with the same ids, which Home
//! takes as the same message.
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

pub const EVENT: &str = "dervo-voice";
/// What the window may send. Mirrors VoiceLoop.command in backend/dervo_voice/loop.py.
pub const COMMANDS: [&str; 15] = ["ptt_down", "ptt_up", "mode", "mute", "unmute", "undo", "confirm", "delivered",
    "failed", "speak", "stop_speaking", "settings", "status", "scope", "drop"];
pub const MODES: [&str; 3] = ["push_to_talk", "wake", "conversation"];
const HEALTHY: Duration = Duration::from_secs(60);
/// Quick failures in a row before the supervisor gives up (mic denied, a broken install): each restart reloads ~1 GB.
pub const GIVE_UP: u32 = 5;

/// Voice's libraries (runtime/voice.json) and its audio helper are built for macOS 14 and later.
pub const MINIMUM_MACOS: u32 = 14;

/// The major version in SystemVersion.plist's text, or None when it can't be read.
pub fn macos_major(plist: &str) -> Option<u32> {
    let after = &plist[plist.find("<key>ProductVersion</key>")?..];
    let start = after.find("<string>")? + "<string>".len();
    after[start..].split(['.', '<']).next()?.trim().parse().ok()
}

/// How long to wait before restart number `attempt` (0-based): 1, 2, 4, 8, 16, then 30 s.
pub fn backoff(attempt: u32) -> Duration {
    Duration::from_secs(if attempt >= 5 { 30 } else { 1u64 << attempt })
}

/// The command line, with nothing from the app's own environment (the same rule as launch::host_command).
pub fn command(python: &Path, backend: &Path, models: &Path, journal: &Path, helper: Option<&Path>, site: Option<&Path>, mode: &str) -> Command {
    let mut command = Command::new(python);
    command.args(["-E", "-s", "-B", "-m", "dervo_voice", "--models"]).arg(models).arg("--journal").arg(journal)
        .args(["--mode", mode]);
    if let Some(helper) = helper { command.arg("--helper").arg(helper); }
    if let Some(site) = site { command.arg("--site").arg(site); }
    command.current_dir(backend).env_clear().stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    crate::launch::host_environment(&mut command);
    command
}

/// The model download (backend/dervo_voice/setup.py). Only ever started by the window's "Download voice models" click,
/// which is the consent: nothing downloads at install or launch.
pub fn setup_command(python: &Path, backend: &Path, models: &Path) -> Command {
    let mut command = Command::new(python);
    command.args(["-E", "-s", "-B", "-m", "dervo_voice.setup", "--target"]).arg(models)
        .current_dir(backend).env_clear().stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    crate::launch::host_environment(&mut command);
    command
}

/// One command from the window, checked: a known type, a known mode, and nothing larger than a reply to speak.
pub fn checked(message: &serde_json::Value) -> Result<String, String> {
    let kind = message.get("type").and_then(|value| value.as_str()).ok_or("A voice command needs a type.")?;
    if !COMMANDS.contains(&kind) { return Err(format!("Voice doesn't take {kind:?}.")); }
    if kind == "mode" && !message.get("mode").and_then(|value| value.as_str()).is_some_and(|mode| MODES.contains(&mode)) {
        return Err("Voice mode is push_to_talk, wake or conversation.".into());
    }
    let line = serde_json::to_string(message).map_err(|error| error.to_string())?;
    if line.len() > 16 * 1024 { return Err("That's too long to speak.".into()); }
    Ok(line)
}

struct Running { child: Child, stdin: ChildStdin }

/// The supervised process. `emit` gets every event line, already parsed, plus the supervisor's own.
pub struct Supervisor {
    running: Arc<Mutex<Option<Running>>>,
    /// The generation that may run. Starting takes a new one; stopping moves past it, which ends that loop.
    generation: Arc<AtomicU64>,
    wanted: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl Default for Supervisor {
    fn default() -> Self {
        Supervisor { running: Arc::new(Mutex::new(None)), generation: Arc::new(AtomicU64::new(0)), wanted: Arc::new(AtomicBool::new(false)),
            thread: Mutex::new(None) }
    }
}

impl Supervisor {
    pub fn is_running(&self) -> bool {
        self.wanted.load(Ordering::SeqCst) && self.running.lock().map(|running| running.is_some()).unwrap_or(false)
    }

    /// Start (or keep) the process. `make` builds a fresh command for each start, so a changed mode or models folder
    /// takes effect on the next restart.
    pub fn start(&self, make: impl Fn() -> Command + Send + 'static, emit: impl Fn(serde_json::Value) + Send + Sync + 'static) {
        let mut thread = self.thread.lock().unwrap();
        if self.wanted.load(Ordering::SeqCst) && thread.as_ref().is_some_and(|handle| !handle.is_finished()) { return; }
        if let Some(old) = thread.take() { let _ = old.join(); }
        let mine = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        self.wanted.store(true, Ordering::SeqCst);
        let (running, generation, wanted) = (self.running.clone(), self.generation.clone(), self.wanted.clone());
        let still = wanted.clone();
        let current = move || still.load(Ordering::SeqCst) && generation.load(Ordering::SeqCst) == mine;
        let emit = Arc::new(emit);
        *thread = Some(std::thread::Builder::new().name("dervo-voice".into()).spawn(move || {
            let mut attempt = 0u32;
            while current() {
                let started = Instant::now();
                match make().spawn() {
                    Ok(mut child) => {
                        let stdin = child.stdin.take();
                        let stdout = child.stdout.take();
                        let stderr = child.stderr.take();
                        match stdin {
                            Some(stdin) => *running.lock().unwrap() = Some(Running { child, stdin }),
                            None => { let _ = child.kill(); let _ = child.wait(); break; }
                        }
                        // A stop that landed between the spawn and the line above couldn't see this child: end it now.
                        if !current() { if let Some(mut gone) = running.lock().unwrap().take() { let _ = gone.child.kill(); let _ = gone.child.wait(); } break; }
                        emit(serde_json::json!({"type": "supervisor", "state": "running", "attempt": attempt}));
                        if let Some(stderr) = stderr {
                            // Python's own tracebacks: the last lines go to the window as one error, never to a log
                            // with transcripts in it.
                            let emit = emit.clone();
                            std::thread::spawn(move || {
                                let mut tail: Vec<String> = Vec::new();
                                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                                    tail.push(line);
                                    if tail.len() > 12 { tail.remove(0); }
                                }
                                if !tail.is_empty() { emit(serde_json::json!({"type": "supervisor", "state": "stderr", "detail": tail.join("\n")})); }
                            });
                        }
                        if let Some(stdout) = stdout {
                            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                                match serde_json::from_str::<serde_json::Value>(&line) {
                                    Ok(event) if event.is_object() => emit(event),
                                    _ => {}
                                }
                            }
                        }
                        // Taken out first, so a slow exit never holds the lock that send and stop need.
                        let gone = running.lock().unwrap().take();
                        let code = gone.and_then(|mut gone| gone.child.wait().ok()).and_then(|status| status.code());
                        if !current() { break; }
                        if code == Some(0) { break; }  // it chose to stop (its stdin closed, or it was asked to)
                        if started.elapsed() >= HEALTHY { attempt = 0; }
                        if attempt + 1 >= GIVE_UP {
                            emit(serde_json::json!({"type": "supervisor", "state": "failed", "code": code, "detail": "Voice kept stopping, so it's off."}));
                            break;
                        }
                        let wait = backoff(attempt);
                        emit(serde_json::json!({"type": "supervisor", "state": "restarting", "code": code, "in_ms": wait.as_millis() as u64}));
                        attempt += 1;
                        sleep_unless_stopped(&current, wait);
                    }
                    Err(error) => {
                        emit(serde_json::json!({"type": "supervisor", "state": "failed", "detail": error.to_string()}));
                        break;  // it can't be started at all (no Python); retrying won't change that
                    }
                }
            }
            if current() { wanted.store(false, Ordering::SeqCst); }
            emit(serde_json::json!({"type": "supervisor", "state": "stopped"}));
        }).expect("voice supervisor thread"));
    }

    pub fn send(&self, line: &str) -> Result<(), String> {
        let mut running = self.running.lock().map_err(|_| "Voice is restarting.".to_string())?;
        let process = running.as_mut().ok_or("Voice isn't running.")?;
        process.stdin.write_all(line.as_bytes()).and_then(|()| process.stdin.write_all(b"\n")).and_then(|()| process.stdin.flush())
            .map_err(|error| format!("Voice didn't take that: {error}"))
    }

    /// Stop for good: no restart. The process gets `shutdown` and a moment to settle its journal, then is killed, and
    /// the supervisor thread is waited for, so a start right after this begins from nothing.
    pub fn stop(&self) {
        self.wanted.store(false, Ordering::SeqCst);
        self.generation.fetch_add(1, Ordering::SeqCst);
        let _ = self.send(r#"{"type":"shutdown"}"#);
        let deadline = Instant::now() + Duration::from_millis(800);
        loop {
            let exited = match self.running.lock().unwrap().as_mut() {
                None => true,
                Some(process) => matches!(process.child.try_wait(), Ok(Some(_))),
            };
            if exited || Instant::now() >= deadline { break; }
            std::thread::sleep(Duration::from_millis(20));
        }
        if let Some(process) = self.running.lock().unwrap().as_mut() { let _ = process.child.kill(); }
        let mut thread = self.thread.lock().unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while thread.as_ref().is_some_and(|handle| !handle.is_finished()) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        if thread.as_ref().is_some_and(|handle| handle.is_finished()) { if let Some(done) = thread.take() { let _ = done.join(); } }
    }
}

fn sleep_unless_stopped(current: &impl Fn() -> bool, wait: Duration) {
    let until = Instant::now() + wait;
    while Instant::now() < until && current() { std::thread::sleep(Duration::from_millis(50)); }
}

/// Where voice keeps its models, journal and libraries, all under the app's own data folder.
pub struct Places { pub python: PathBuf, pub backend: PathBuf, pub models: PathBuf, pub journal: PathBuf, pub helper: Option<PathBuf>, pub site: Option<PathBuf> }

mod app {
    use super::*;
    use tauri::{AppHandle, Emitter, Manager};

    pub struct Voice { pub supervisor: Supervisor, pub mode: Mutex<String> }
    impl Default for Voice { fn default() -> Self { Voice { supervisor: Supervisor::default(), mode: Mutex::new("push_to_talk".into()) } } }

    fn places(app: &AppHandle) -> Result<Places, String> {
        let resources = app.path().resource_dir().map_err(|error| error.to_string())?;
        let data = app.path().app_data_dir().map_err(|error| error.to_string())?;
        let python = crate::launch::resolve_app_python(std::env::var_os(crate::launch::PYTHON_VARIABLE).as_deref())?;
        let helper = resources.join("voice/dervo-voice-audio");
        // Voice's libraries ship in the app (scripts/voice_runtime.py, runtime/voice.json). A source run can put its own
        // in the data folder instead.
        let site = [resources.join("voice/site-packages"), data.join("voice/site-packages")].into_iter().find(|site| site.is_dir());
        Ok(Places { python, backend: resources.join("backend"), models: data.join("voice/models"), journal: data.join("voice/journal.jsonl"),
            helper: helper.exists().then_some(helper), site })
    }

    /// Voice runs on macOS 14 and later. A version that can't be read counts as unsupported, not as fine.
    fn supported() -> Result<(), String> {
        #[cfg(target_os = "macos")]
        {
            let major = std::fs::read_to_string("/System/Library/CoreServices/SystemVersion.plist").ok().as_deref().and_then(macos_major);
            if !major.is_some_and(|major| major >= MINIMUM_MACOS) { return Err(format!("Voice needs macOS {MINIMUM_MACOS} or later.")); }
        }
        Ok(())
    }

    /// Turn voice on in a mode. Starting twice is harmless; changing mode while running goes to the process directly.
    #[tauri::command]
    pub fn voice_start(app: AppHandle, voice: tauri::State<Voice>, mode: String) -> Result<(), String> {
        if !MODES.contains(&mode.as_str()) { return Err("Voice mode is push_to_talk, wake or conversation.".into()); }
        supported()?;
        *voice.mode.lock().unwrap() = mode.clone();
        if voice.supervisor.is_running() {
            return voice.supervisor.send(&serde_json::json!({"type": "mode", "mode": mode}).to_string());
        }
        let found = places(&app)?;
        let handle = app.clone();
        let emitter = app.clone();
        voice.supervisor.start(move || {
            let mode = handle.state::<Voice>().mode.lock().unwrap().clone();
            command(&found.python, &found.backend, &found.models, &found.journal, found.helper.as_deref(), found.site.as_deref(), &mode)
        }, move |event| { let _ = emitter.emit_to("main", EVENT, event); });
        Ok(())
    }

    /// Download the voice models, with progress as `dervo-voice` events (`{"type":"setup",...}`).
    #[tauri::command]
    pub fn voice_install(app: AppHandle) -> Result<(), String> {
        supported()?;  // no 830 MB download for a Mac that can't run it
        // One download at a time: a second click while it runs is ignored, not a second 830 MB race.
        static INSTALLING: AtomicBool = AtomicBool::new(false);
        if INSTALLING.swap(true, Ordering::SeqCst) { return Ok(()); }
        let started = places(&app).and_then(|found| setup_command(&found.python, &found.backend, &found.models).spawn()
            .map_err(|error| format!("The voice download couldn't start: {error}")));
        let mut child = match started { Ok(child) => child, Err(error) => { INSTALLING.store(false, Ordering::SeqCst); return Err(error); } };
        let Some(stdout) = child.stdout.take() else { INSTALLING.store(false, Ordering::SeqCst); return Err("The voice download couldn't start.".into()); };
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Ok(event) = serde_json::from_str::<serde_json::Value>(&line) { let _ = app.emit_to("main", EVENT, event); }
            }
            let code = child.wait().ok().and_then(|status| status.code());
            INSTALLING.store(false, Ordering::SeqCst);
            let _ = app.emit_to("main", EVENT, serde_json::json!({"type": "setup", "state": "exited", "code": code}));
        });
        Ok(())
    }

    #[tauri::command]
    pub fn voice_stop(voice: tauri::State<Voice>) { voice.supervisor.stop(); }

    #[tauri::command]
    pub fn voice_command(voice: tauri::State<Voice>, message: serde_json::Value) -> Result<(), String> {
        voice.supervisor.send(&checked(&message)?)
    }
}
pub use app::*;

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::mpsc;

    #[test] fn backoff_doubles_then_caps() {
        let waits: Vec<u64> = (0..8).map(|attempt| backoff(attempt).as_secs()).collect();
        assert_eq!(waits, [1, 2, 4, 8, 16, 30, 30, 30]);
    }

    #[test] fn command_runs_the_package_with_a_cleared_environment() {
        let built = command(Path::new("/fixture/python3"), Path::new("/fixture/Resources/backend"), Path::new("/data/voice/models"),
            Path::new("/data/voice/journal.jsonl"), Some(Path::new("/fixture/Resources/voice/dervo-voice-audio")), None, "wake");
        let args: Vec<String> = built.get_args().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args, ["-E", "-s", "-B", "-m", "dervo_voice", "--models", "/data/voice/models", "--journal", "/data/voice/journal.jsonl",
            "--mode", "wake", "--helper", "/fixture/Resources/voice/dervo-voice-audio"]);
        assert_eq!(built.get_current_dir(), Some(Path::new("/fixture/Resources/backend")));
        // Only what host_environment adds: nothing on a Mac, the desktop session's variables on Linux (src/launch.rs).
        assert_eq!(built.get_envs().count(), crate::launch::host_environment(&mut Command::new("x")).get_envs().count());
    }

    #[test] fn setup_runs_the_download_module_only() {
        let built = setup_command(Path::new("/fixture/python3"), Path::new("/fixture/Resources/backend"), Path::new("/data/voice/models"));
        let args: Vec<String> = built.get_args().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(args, ["-E", "-s", "-B", "-m", "dervo_voice.setup", "--target", "/data/voice/models"]);
        // Only what host_environment adds: nothing on a Mac, the desktop session's variables on Linux (src/launch.rs).
        assert_eq!(built.get_envs().count(), crate::launch::host_environment(&mut Command::new("x")).get_envs().count());
    }

    #[test] fn only_known_commands_pass() {
        assert!(checked(&serde_json::json!({"type": "ptt_down"})).is_ok());
        assert!(checked(&serde_json::json!({"type": "mode", "mode": "wake"})).is_ok());
        assert!(checked(&serde_json::json!({"type": "mode", "mode": "always_record"})).is_err());
        assert!(checked(&serde_json::json!({"type": "shutdown"})).is_err(), "only the supervisor stops it");
        assert!(checked(&serde_json::json!({"text": "no type"})).is_err());
        assert!(checked(&serde_json::json!({"type": "speak", "text": "x".repeat(20_000)})).is_err());
    }

    fn stub(name: &str, body: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("dervo-voice-test-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("stub.sh");
        std::fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    fn collect(rx: &mpsc::Receiver<serde_json::Value>, until: impl Fn(&[serde_json::Value]) -> bool) -> Vec<serde_json::Value> {
        let mut seen = Vec::new();
        let deadline = Instant::now() + Duration::from_secs(8);
        while Instant::now() < deadline && !until(&seen) {
            if let Ok(event) = rx.recv_timeout(Duration::from_millis(100)) { seen.push(event); }
        }
        seen
    }

    #[test] fn events_pass_through_and_commands_reach_the_process() {
        // The stub echoes each command line back as an event, the way the real process answers `status`.
        let script = stub("echo", r#"echo '{"type":"ready"}'; while read line; do echo "{\"type\":\"echo\",\"got\":$line}"; done"#);
        let supervisor = Supervisor::default();
        let (tx, rx) = mpsc::channel();
        let tx = Mutex::new(tx);
        supervisor.start(move || piped(&script),
            move |event| { let _ = tx.lock().unwrap().send(event); });
        // The process's first line and the supervisor's "running" come from different threads, in either order.
        let ready = collect(&rx, |seen| seen.iter().any(|event| event["type"] == "ready")
            && seen.iter().any(|event| event["type"] == "supervisor" && event["state"] == "running"));
        assert!(ready.iter().any(|event| event["type"] == "ready"), "{ready:?}");
        assert!(ready.iter().any(|event| event["type"] == "supervisor" && event["state"] == "running"), "{ready:?}");
        supervisor.send(&checked(&serde_json::json!({"type": "status"})).unwrap()).unwrap();
        let echoed = collect(&rx, |seen| seen.iter().any(|event| event["type"] == "echo"));
        assert_eq!(echoed.iter().find(|event| event["type"] == "echo").unwrap()["got"]["type"], "status");
        supervisor.stop();
        let stopped = collect(&rx, |seen| seen.iter().any(|event| event["state"] == "stopped"));
        assert!(stopped.iter().any(|event| event["state"] == "stopped"));
        assert!(!stopped.iter().any(|event| event["state"] == "restarting"), "a stop is not a crash");
    }

    #[test] fn a_crash_is_restarted() {
        let script = stub("crash", r#"echo '{"type":"ready"}'; exit 3"#);
        let supervisor = Supervisor::default();
        let (tx, rx) = mpsc::channel();
        let tx = Mutex::new(tx);
        supervisor.start(move || piped(&script),
            move |event| { let _ = tx.lock().unwrap().send(event); });
        let seen = collect(&rx, |seen| seen.iter().filter(|event| event["type"] == "ready").count() >= 2);
        supervisor.stop();
        let restarting: Vec<_> = seen.iter().filter(|event| event["state"] == "restarting").collect();
        assert!(!restarting.is_empty(), "{seen:?}");
        assert_eq!(restarting[0]["code"], 3);
        assert_eq!(restarting[0]["in_ms"], 1000);
        assert!(seen.iter().filter(|event| event["type"] == "ready").count() >= 2, "{seen:?}");
    }

    #[test] fn a_clean_exit_is_not_restarted() {
        let script = stub("clean", r#"echo '{"type":"ready"}'; exit 0"#);
        let supervisor = Supervisor::default();
        let (tx, rx) = mpsc::channel();
        let tx = Mutex::new(tx);
        supervisor.start(move || piped(&script), move |event| { let _ = tx.lock().unwrap().send(event); });
        let seen = collect(&rx, |seen| seen.iter().any(|event| event["state"] == "stopped"));
        assert!(!seen.iter().any(|event| event["state"] == "restarting"), "{seen:?}");
        assert!(!supervisor.is_running());
    }

    #[test] fn it_gives_up_after_repeated_quick_failures() {
        let script = stub("giveup", r#"exit 3"#);
        let supervisor = Supervisor::default();
        let (tx, rx) = mpsc::channel();
        let tx = Mutex::new(tx);
        // The real backoff would make this test take 15 s; the count is what's checked.
        supervisor.start(move || piped(&script), move |event| { let _ = tx.lock().unwrap().send(event); });
        let mut seen = Vec::new();
        let deadline = Instant::now() + Duration::from_secs(25);
        while Instant::now() < deadline && !seen.iter().any(|event: &serde_json::Value| event["state"] == "stopped") {
            if let Ok(event) = rx.recv_timeout(Duration::from_millis(100)) { seen.push(event); }
        }
        let starts = seen.iter().filter(|event| event["state"] == "running").count() as u32;
        assert_eq!(starts, GIVE_UP, "{seen:?}");
        assert!(seen.iter().any(|event| event["state"] == "failed"), "{seen:?}");
    }

    #[test] fn a_quick_stop_and_start_leaves_one_process() {
        // Each start writes a line to a shared file and stays up; after stop + start exactly one must be alive.
        let dir = std::env::temp_dir().join(format!("dervo-voice-test-gen-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let alive = dir.join("alive");
        let _ = std::fs::remove_file(&alive);
        let script = stub("gen", &format!(r#"echo $$ >> {path}; echo '{{"type":"ready"}}'; while read line; do :; done"#, path = alive.display()));
        let supervisor = Supervisor::default();
        let (tx, rx) = mpsc::channel();
        let tx = Arc::new(Mutex::new(tx));
        let (first, again) = (script.clone(), script.clone());
        let sender = tx.clone();
        supervisor.start(move || piped(&first), move |event| { let _ = sender.lock().unwrap().send(event); });
        collect(&rx, |seen| seen.iter().any(|event| event["type"] == "ready"));
        supervisor.stop();
        let sender = tx.clone();
        supervisor.start(move || piped(&again), move |event| { let _ = sender.lock().unwrap().send(event); });
        collect(&rx, |seen| seen.iter().any(|event| event["type"] == "ready"));
        std::thread::sleep(Duration::from_millis(300));
        let pids: Vec<String> = std::fs::read_to_string(&alive).unwrap().lines().map(str::to_owned).collect();
        // Signal 0 only checks the process exists. macOS has no /proc, so this works on both platforms.
        let living = pids.iter().filter(|pid| unsafe { libc::kill(pid.parse::<libc::pid_t>().unwrap(), 0) } == 0).count();
        supervisor.stop();
        assert_eq!(pids.len(), 2, "{pids:?}");
        assert_eq!(living, 1, "{pids:?}");
        assert!(!supervisor.is_running());
    }

    #[test] fn the_macos_version_is_read_from_system_version() {
        let plist = "<dict>\n\t<key>ProductName</key>\n\t<string>macOS</string>\n\t<key>ProductVersion</key>\n\t<string>14.6.1</string>\n</dict>";
        assert_eq!(macos_major(plist), Some(14));
        assert_eq!(macos_major("<key>ProductVersion</key><string>11.7</string>"), Some(11));
        assert_eq!(macos_major("nothing here"), None);
    }

    #[test] fn sending_while_stopped_says_so() {
        assert_eq!(Supervisor::default().send("{}").unwrap_err(), "Voice isn't running.");
    }

    fn piped(script: &Path) -> Command {
        let mut command = Command::new(script);
        command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        command
    }
}
