use std::sync::atomic::{AtomicBool,Ordering};
use tauri::{Emitter,Manager};

#[derive(Default)]
pub struct DraftExit { ready:AtomicBool }

/// The renderer flushes its draft queue before a normal close or Quit command.
pub fn request(app:&tauri::AppHandle)->bool {
    let Some(state)=app.try_state::<DraftExit>() else{return false};
    if !state.ready.load(Ordering::Acquire){return false}
    let _=app.emit_to("main","dervo-save-before-exit",());
    true
}

#[tauri::command]
pub fn draft_guard_ready(window:tauri::WebviewWindow,state:tauri::State<'_,DraftExit>)->Result<(),String>{
    if window.label()!="main"{return Err("Only the main window can finish draft setup.".into())}
    state.ready.store(true,Ordering::Release);Ok(())
}

#[tauri::command]
pub fn draft_exit(window:tauri::WebviewWindow,state:tauri::State<'_,DraftExit>)->Result<(),String>{
    if window.label()!="main"{return Err("Only the main window can close this workspace.".into())}
    state.ready.store(false,Ordering::Release);
    window.app_handle().exit(0);Ok(())
}
