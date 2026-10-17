//! Off the Mac there's one app icon (PLAN-beta16-build-20260925.md, b16/windows). `dock` is false, as it is on a Mac
//! development run without the Dock tile plug-in, and choosing another icon is refused.
use serde_json::{json, Value};
use tauri::AppHandle;

use crate::bridge::Host;

const DEFAULT_ICON: &str = "cream";

pub fn setup<R: tauri::Runtime>(_app: &AppHandle<R>) {}

fn main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" { return Err("Only the main window can change the app icon".into()) }
    Ok(())
}

#[tauri::command]
pub fn app_icon_get(window: tauri::WebviewWindow) -> Result<Value, String> {
    main_window(&window)?;
    Ok(json!({"icon": DEFAULT_ICON, "dock": false}))
}

#[tauri::command]
pub fn app_icon_set(window: tauri::WebviewWindow, state: tauri::State<'_, Host>, icon: String) -> Result<Value, String> {
    main_window(&window)?;
    let _ = (state, icon);
    Err("The icon picker is on the Mac only.".into())
}
