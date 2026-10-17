//! A user-confirmed native restart. Renderer arguments never select paths or commands.
use serde_json::{json, Value};
use std::{io::{BufRead, BufReader, Write}, path::Path, process::{Child, ChildStdin, Command, Stdio}, sync::mpsc, time::Duration};

/// On Windows the update helper opens the new Dervo itself once the installer has run (backend/windows_update.py), so it
/// gets what a Windows app needs to find the system, the user's folders and WebView2. Same list as LAUNCH_ENVIRONMENT
/// there. The host never gets these.
#[cfg(windows)]
const WINDOWS_LAUNCH_ENVIRONMENT: [&str; 21] = ["SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "USERPROFILE", "USERNAME",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "TEMP", "TMP", "PATH", "PATHEXT", "COMPUTERNAME", "OS", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE"];

/// Where a new Dervo writes that it started, when the Windows update helper opened it (backend/windows_install.py).
/// A Mac helper passes a pipe in DERVO_HANDOFF_FD instead.
const HANDOFF_FILE: &str = "DERVO_HANDOFF_FILE";

pub struct Handoff { child: Child, input: ChildStdin, output: mpsc::Receiver<String>, committed: bool }
impl Handoff {
    pub fn prepare(python: &Path, script: &Path, plan: Value) -> Result<Self, String> {
        let mut command = Command::new(python);
        crate::launch::host_environment(command.args(["-E", "-s", "-B"]).arg(script).env_clear());
        #[cfg(windows)]
        for name in WINDOWS_LAUNCH_ENVIRONMENT {
            if let Some(value) = std::env::var_os(name) { command.env(name, value); }
        }
        let mut child = command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
            .spawn().map_err(|e|format!("Couldn't prepare the version switch: {e}"))?;
        let input = child.stdin.take().ok_or("The version switch couldn't open its input")?;
        let stdout = child.stdout.take().ok_or("The version switch couldn't open its output")?;
        let (sender, output) = mpsc::sync_channel(2);
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line { Ok(line) if line.len() <= 131072 => { if sender.send(line).is_err() { break; } }, _ => break }
            }
        });
        let mut handoff = Self { child, input, output, committed: false };
        handoff.send(plan)?;
        handoff.expect("prepared")?;
        Ok(handoff)
    }
    fn send(&mut self, value: Value) -> Result<(), String> {
        writeln!(self.input, "{value}").and_then(|_|self.input.flush())
            .map_err(|e|format!("The version switch stopped: {e}"))
    }
    fn expect(&self, state: &str) -> Result<Value, String> {
        let line = self.output.recv_timeout(Duration::from_secs(120))
            .map_err(|_|"The version switch didn't finish. Your current app and workspace haven't been replaced.".to_string())?;
        let value: Value = serde_json::from_str(&line).map_err(|_|"The version switch returned an unreadable result")?;
        if let Some(error) = value.get("error").and_then(Value::as_str) { return Err(error.into()); }
        if value.get("state").and_then(Value::as_str) != Some(state) { return Err("The version switch stopped before the expected step".into()); }
        Ok(value)
    }
    pub fn backup(&mut self) -> Result<Value, String> {
        self.send(json!({"action":"backup"}))?;
        self.expect("backed_up")
    }
    pub fn commit(mut self) -> Result<(), String> {
        self.send(json!({"action":"launch"}))?;
        self.committed = true;
        Ok(())
    }
}
impl Drop for Handoff {
    fn drop(&mut self) {
        if !self.committed { let _ = self.child.kill(); let _ = self.child.wait(); }
    }
}

/// A saved selection was created by an explicit successful version switch.
/// Older retained app bundles stay unchanged; supported launchers forward to it.
pub fn redirect_selected(script:&Path, default_dir:Result<std::path::PathBuf,String>)->Result<bool,String> {
    if std::env::var_os("DERVO_HANDOFF_FD").is_some() || std::env::var_os(HANDOFF_FILE).is_some()
        || std::env::var("DERVO_OPEN_THIS_VERSION").as_deref()==Ok("1") {return Ok(false);}
    let data_override=std::env::var_os(crate::launch::DATA_DIR_VARIABLE);
    let Ok(data)=crate::launch::resolve_data_dir(data_override.as_deref(),default_dir) else {return Ok(false)};
    let Ok(python)=crate::launch::resolve_app_python(std::env::var_os(crate::launch::PYTHON_VARIABLE).as_deref()) else {return Ok(false)};
    let current=std::env::current_exe().map_err(|e|e.to_string())?;
    let mut probe=crate::launch::host_environment(Command::new(&python).args(["-E","-s","-B"]).arg(script.with_file_name("release_selection.py"))
        .env_clear()).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|e|e.to_string())?;
    let request=json!({"workspace":data,"currentExecutable":current,"dataOverride":data_override.is_some()});
    writeln!(probe.stdin.take().ok_or("Couldn't inspect the selected version")?,"{request}").map_err(|e|e.to_string())?;
    let result=probe.wait_with_output().map_err(|e|e.to_string())?;
    let value:Value=serde_json::from_slice(&result.stdout).map_err(|_|"The selected version couldn't be inspected")?;
    if let Some(error)=value.get("error").and_then(Value::as_str) {return Err(error.into());}
    if !result.status.success() {return Err("The selected version check stopped".into());}
    let Some(target)=value.get("executable").and_then(Value::as_str) else {return Ok(false)};
    let mut launch=Command::new(target);
    launch.env_clear().env("HOME",std::env::var_os("HOME").unwrap_or_default())
        .env("PATH","/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin");
    forward_python_override(&mut launch,std::env::var_os(crate::launch::PYTHON_VARIABLE));
    if data_override.is_some() {launch.env(crate::launch::DATA_DIR_VARIABLE,data);}
    launch.spawn().map_err(|e|format!("Couldn't reopen the selected Dervo version: {e}"))?;
    Ok(true)
}

/// The version helper passes a dedicated pipe, or on Windows a file to create. No acknowledgement is emitted
/// until the local host and native window have both started.
pub fn acknowledge_startup(ready:bool, detail:&str) {
    if let Some(path)=std::env::var_os(HANDOFF_FILE) {
        std::env::remove_var(HANDOFF_FILE);
        write_handoff_file(Path::new(&path),ready,detail);
        return;
    }
    let Some(fd)=std::env::var("DERVO_HANDOFF_FD").ok().and_then(|s|s.parse::<u32>().ok()).filter(|fd|*fd>2) else {return};
    if let Ok(mut pipe)=std::fs::OpenOptions::new().write(true).open(format!("/dev/fd/{fd}")) {
        let _=writeln!(pipe,"{}",json!({"ready":ready,"detail":detail}));
        let _=pipe.flush();
    }
    std::env::remove_var("DERVO_HANDOFF_FD");
}

/// One line, in a file that mustn't exist yet: the helper made its folder, and a file already there isn't this start's.
fn write_handoff_file(path:&Path, ready:bool, detail:&str) {
    if !path.is_absolute() {return;}
    if let Ok(mut file)=std::fs::OpenOptions::new().write(true).create_new(true).open(path) {
        let _=writeln!(file,"{}",json!({"ready":ready,"detail":detail}));
        let _=file.flush();
    }
}

#[tauri::command]
pub async fn use_preview_version(window: tauri::WebviewWindow, app: tauri::AppHandle,
    state: tauri::State<'_, crate::startup::Host>, project_id: String) -> Result<(), String> {
    if window.label() != "main" { return Err("Only the main window can switch Dervo versions".into()); }
    state.switch_version(project_id)?;
    app.exit(0);
    Ok(())
}

#[tauri::command]
pub async fn install_update(window: tauri::WebviewWindow, app: tauri::AppHandle,
    state: tauri::State<'_, crate::startup::Host>) -> Result<(), String> {
    if window.label() != "main" { return Err("Only the main window can update Dervo".into()); }
    state.update_version()?;
    app.exit(0);
    Ok(())
}

#[tauri::command]
pub async fn restore_previous_version(window: tauri::WebviewWindow, app: tauri::AppHandle,
    state: tauri::State<'_, crate::startup::Host>, switch_id: String) -> Result<(), String> {
    if window.label() != "main" { return Err("Only the main window can restore a Dervo version".into()); }
    state.rollback_version(switch_id)?;
    app.exit(0);
    Ok(())
}

/// The selected version finds its own Python: since beta.17 a built app carries one, and 16.4 or older uses the framework
/// when DERVO_PYTHON is unset. This app's own Python is never handed over: it lives inside this app, and an older version
/// would run its host on it and fail its keychain and confinement checks. Only an override this app was given is passed
/// on, as before (16.4 sets the framework there when it opens a newer version).
fn forward_python_override(launch:&mut Command, received:Option<std::ffi::OsString>)->&mut Command {
    if let Some(value)=received {launch.env(crate::launch::PYTHON_VARIABLE,value);}
    launch
}

#[cfg(test)] mod tests {
    use super::*;
    struct Scratch(std::path::PathBuf);
    impl Scratch {
        fn new(name:&str)->Self {
            let root=std::env::temp_dir().join(format!("dervo handoff {} {name}",std::process::id()));
            std::fs::create_dir_all(&root).unwrap(); Self(root)
        }
        fn script(&self, fail:bool)->std::path::PathBuf {
            let script=self.0.join("helper.py");
            let body=format!(r#"import json,sys,pathlib
root=pathlib.Path(__file__).parent
plan=json.loads(sys.stdin.readline())
(root/'plan.json').write_text(json.dumps(plan))
print(json.dumps({{'state':'prepared'}}),flush=True)
assert json.loads(sys.stdin.readline())=={{'action':'backup'}}
(root/'backup').write_text('done')
print(json.dumps({{'error':'disk full'}} if {fail} else {{'state':'backed_up'}}),flush=True)
line=sys.stdin.readline()
if line:
    assert json.loads(line)=={{'action':'launch'}}
    (root/'launch').write_text('requested')
"#,fail=if fail {"True"} else {"False"});
            std::fs::write(&script,body).unwrap(); script
        }
    }
    impl Drop for Scratch {fn drop(&mut self) {let _=std::fs::remove_dir_all(&self.0);}}
    #[test] fn helper_commit_follows_successful_backup_acknowledgement() {
        let scratch=Scratch::new("success");
        let python=crate::launch::resolve_app_python(None).unwrap();
        let mut helper=Handoff::prepare(&python,&scratch.script(false),json!({"fixture":true})).unwrap();
        assert!(!scratch.0.join("backup").exists());
        helper.backup().unwrap();
        assert!(!scratch.0.join("launch").exists());
        helper.commit().unwrap();
        for _ in 0..100 {
            if scratch.0.join("launch").exists() {return;}
            std::thread::sleep(Duration::from_millis(10));
        }
        panic!("The acknowledged helper never received launch");
    }
    #[test] fn handoff_file_is_written_once_and_only_when_absolute() {
        let scratch=Scratch::new("handoff file");
        let path=scratch.0.join("started.json");
        write_handoff_file(&path,true,"");
        assert_eq!(std::fs::read_to_string(&path).unwrap(),"{\"detail\":\"\",\"ready\":true}\n");
        write_handoff_file(&path,false,"second start");
        assert!(!std::fs::read_to_string(&path).unwrap().contains("second start"));
        write_handoff_file(Path::new("relative.json"),true,"");
        assert!(!Path::new("relative.json").exists());
    }
    #[test] fn failed_backup_drops_helper_without_launch() {
        let scratch=Scratch::new("failure");
        let python=crate::launch::resolve_app_python(None).unwrap();
        let mut helper=Handoff::prepare(&python,&scratch.script(true),json!({"fixture":true})).unwrap();
        assert!(helper.backup().unwrap_err().contains("disk full"));
        drop(helper);
        assert!(!scratch.0.join("launch").exists());
    }
    /// A built app's helper runs on the app's own Python (17 updating to the next version). The fixture app's own Python
    /// is a link to the framework one, so this runs without a staged runtime; launch.rs checks the path rules.
    #[test] fn built_app_helper_runs_on_its_own_python_even_when_handed_the_framework() {
        let scratch=Scratch::new("own runtime");
        let framework=crate::launch::resolve_app_python(None).unwrap();
        let contents=scratch.0.join("Dervo Local.app/Contents");
        let exe=contents.join("MacOS/dervo-local");
        let own=crate::launch::bundled_python(&contents);
        std::fs::create_dir_all(exe.parent().unwrap()).unwrap();std::fs::write(&exe,"").unwrap();
        std::fs::create_dir_all(own.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(&framework,&own).unwrap();
        // 16.4's release_switch.py opens the new version with DERVO_PYTHON set to the framework.
        let python=crate::launch::resolve_mac_python(Some(framework.as_os_str()),&exe,&framework).unwrap();
        assert_eq!(python,own);
        let mut helper=Handoff::prepare(&python,&scratch.script(false),json!({"fixture":true})).unwrap();
        helper.backup().unwrap();
        helper.commit().unwrap();
        for _ in 0..100 {
            if scratch.0.join("launch").exists() {return;}
            std::thread::sleep(Duration::from_millis(10));
        }
        panic!("The helper on the app's own Python never received launch");
    }
    #[test] fn selected_version_gets_only_an_override_this_app_was_given() {
        let python=|command:&Command| command.get_envs().find(|(k,_)|*k==crate::launch::PYTHON_VARIABLE).map(|(_,v)|v.map(|v|v.to_owned()));
        let mut launch=Command::new("/fixture/Dervo Local.app/Contents/MacOS/dervo-local");
        launch.env_clear();
        assert_eq!(python(forward_python_override(&mut launch,None)),None,"a built app's own Python is never handed over");
        let framework=std::ffi::OsString::from("/Library/Frameworks/Python.framework/Versions/3.13/bin/python3");
        let mut launch=Command::new("/fixture/Dervo Local.app/Contents/MacOS/dervo-local");
        assert_eq!(python(forward_python_override(&mut launch,Some(framework.clone()))),Some(Some(framework)));
    }
}
