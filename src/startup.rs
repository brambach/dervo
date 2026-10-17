use crate::{bridge::Worker,launch};
use serde::Serialize;
use serde_json::{json,Value};
use std::{path::PathBuf,sync::Mutex};

#[derive(Clone,Serialize)]
pub struct StartupStatus { pub ready:bool, pub kind:String, pub detail:String }
const RESTART_LIMIT:usize=3;
const RESTART_WINDOW:std::time::Duration=std::time::Duration::from_secs(300);
struct Inner { worker:Option<Worker>, status:StartupStatus, restarts:Vec<std::time::Instant> }
pub struct Host { inner:Mutex<Inner>, script:PathBuf, default_dir:Result<PathBuf,String> }
impl Host {
    pub fn new(script:PathBuf,default_dir:Result<PathBuf,String>)->Self {
        Self {inner:Mutex::new(Inner {worker:None,status:StartupStatus {ready:false,kind:"starting".into(),detail:String::new()},restarts:Vec::new()}),script,default_dir}
    }
    pub fn selection_error(&self, detail:String) {
        if let Ok(mut inner)=self.inner.lock() {
            inner.status=StartupStatus {ready:false,kind:"selection".into(),detail};
        }
    }
    pub fn status(&self)->Result<StartupStatus,String> {
        Ok(self.inner.lock().map_err(|_|"Local startup state is unavailable")?.status.clone())
    }
    /// Retry only initial startup. A connected or subsequently lost host is never replaced here.
    pub fn retry(&self)->StartupStatus {
        let Ok(mut inner)=self.inner.lock() else {
            return StartupStatus {ready:false,kind:"error".into(),detail:"Local startup state is unavailable. Quit and reopen Dervo.".into()};
        };
        if inner.worker.is_some() || matches!(inner.status.kind.as_str(),"switching"|"selection") {return inner.status.clone()}
        let result=self.start();
        inner.status=match result {
            Ok(worker)=>{inner.worker=Some(worker);StartupStatus {ready:true,kind:"ready".into(),detail:String::new()}},
            Err((kind,detail))=>StartupStatus {ready:false,kind:kind.into(),detail},
        };
        inner.status.clone()
    }
    /// Hold the native request mutex across preparation and host shutdown. No new
    /// renderer operation can start work between the idle check and backup.
    pub fn switch_version(&self, project_id:String)->Result<(),String> {
        self.change_version("_prepare_release_switch",json!({"projectId":project_id}))
    }
    /// Update and restart: the host names the newer app it found or unpacked; the renderer names nothing.
    pub fn update_version(&self)->Result<(),String> {
        self.change_version("_prepare_update",json!({}))
    }
    pub fn rollback_version(&self, switch_id:String)->Result<(),String> {
        self.change_version("_prepare_release_rollback",json!({"switchId":switch_id}))
    }
    fn change_version(&self, operation:&str, mut args:Value)->Result<(),String> {
        let result=(|| {
            let mut inner=self.inner.lock().map_err(|_|"Local store is unavailable")?;
            let executable=std::env::current_exe().map_err(|e|e.to_string())?;
            // An AppImage runs from a temporary mount; the file the person has is $APPIMAGE (backend/linux_update.py).
            #[cfg(target_os="linux")]
            let executable=launch::own_appimage().unwrap_or(executable);
            let python=launch::resolve_app_python(std::env::var_os(launch::PYTHON_VARIABLE).as_deref())?;
            args["currentExecutable"]=json!(executable);
            args["dataOverride"]=json!(std::env::var_os(launch::DATA_DIR_VARIABLE).is_some());
            args["parentPid"]=json!(std::process::id());
            let plan=inner.worker.as_mut().ok_or("Finish Dervo setup before switching versions")?.call(operation,args)?;
            let mut handoff=crate::version_switch::Handoff::prepare(&python,&self.script.with_file_name("release_switch.py"),plan)?;
            // Worker::drop stops owned subprocesses and releases the workspace lock.
            drop(inner.worker.take());
            inner.status=StartupStatus {ready:false,kind:"switching".into(),detail:"Backing up your workspace and opening the new version.".into()};
            handoff.backup()?;
            handoff.commit()?;
            Ok::<(),String>(())
        })();
        if result.is_err() {
            // A failed helper is killed and reaped before reopening the same host.
            // No workspace restore or launch has been committed at this point.
            if let Ok(mut inner)=self.inner.lock() {
                if inner.worker.is_none() {inner.status.kind="error".into();}
            }
            self.retry();
        }
        result
    }
    fn start(&self)->Result<Worker,(&'static str,String)> {
        let data=launch::resolve_data_dir(std::env::var_os(launch::DATA_DIR_VARIABLE).as_deref(),self.default_dir.clone())
            .map_err(|e|("data",e))?;
        let override_value=std::env::var_os(launch::PYTHON_VARIABLE);
        // A built app carries its own Python, so a failure here is a broken copy ("runtime"), shown on the failed page
        // with its reason. Never "python": StartupGate's install-Python page is for 16.4 and earlier.
        let python=launch::resolve_app_python(override_value.as_deref())
            .map_err(|e|(launch::python_failure_kind(override_value.as_deref()),e))?;
        let mut worker=Worker::launch(&python,&self.script,&data).map_err(|e|("host",e))?;
        // A new host reconciles the last one's runs before it reads requests, so its first answer gets longer (beta.18).
        worker.call_within("status",json!({}),crate::bridge::STARTUP_ANSWER)
            .map_err(|e|(if e == "Another Dervo host owns this database" {"already-open"} else {"host"},e))?;
        Ok(worker)
    }
    /// A host that has stopped is started again before the next request, at most RESTART_LIMIT times in
    /// RESTART_WINDOW. The request that was in flight when it stopped is never sent twice. A slow host isn't stopped
    /// (Worker::call answers busy), so this is for one that exited or stopped beating. Each restart goes to host-errors.log.
    pub fn call(&self,operation:&str,args:Value)->Result<Value,String> {
        let mut inner=self.inner.lock().map_err(|_|"Local store is unavailable")?;
        if let Some(mut old)=inner.worker.take_if(|w|!w.alive()) {
            let ended=old.ended();
            let now=std::time::Instant::now();
            inner.restarts.retain(|t|now.duration_since(*t)<RESTART_WINDOW);
            if inner.restarts.len()>=RESTART_LIMIT {
                old.note(&format!("The host stopped ({ended}) before a {operation} request. Not starting another: it stopped {RESTART_LIMIT} times in 5 minutes."));
                drop(old);
                return Err("Dervo's local store keeps stopping. Quit and reopen Dervo. Your saved work is kept.".into());
            }
            inner.restarts.push(now);
            old.note(&format!("The host stopped ({ended}). Starting a new one for a {operation} request (restart {} of {RESTART_LIMIT} allowed in 5 minutes).",inner.restarts.len()));
            drop(old);
            inner.worker=Some(self.start().map_err(|(_,e)|format!("Dervo's local store stopped and couldn't restart: {e}"))?);
        }
        inner.worker.as_mut().ok_or("Finish Dervo setup before opening your workspace")?.call(operation,args)
    }
}
fn main_window(window:&tauri::WebviewWindow)->Result<(),String> {
    if window.label()=="main" {Ok(())}else{Err("Only the main window can use local setup".into())}
}
#[tauri::command]
pub fn startup_status(window:tauri::WebviewWindow,state:tauri::State<'_,Host>)->Result<StartupStatus,String> {
    main_window(&window)?;state.status()
}
#[tauri::command]
pub async fn startup_retry(window:tauri::WebviewWindow,state:tauri::State<'_,Host>)->Result<StartupStatus,String> {
    main_window(&window)?;Ok(state.retry())
}
