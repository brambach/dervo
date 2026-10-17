//! Notifications off the Mac, through Tauri's notification plugin (b16/windows). Windows shows a toast with the title
//! and body. What isn't here yet: a click on it doesn't open the thread (the plugin reports no clicks on desktop), and
//! there's no taskbar badge for the waiting count.
use tauri::AppHandle;
use tauri_plugin_notification::{NotificationExt, PermissionState};

pub fn setup(_app: &AppHandle) {}

fn main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Only the main window can use notifications".into());
    }
    Ok(())
}

/// Where "Open System Settings" goes when notifications are off: Windows Settings at Notifications, a fixed address.
#[cfg_attr(not(windows), allow(dead_code))]
pub const NOTIFICATION_SETTINGS: &str = "ms-settings:notifications";

#[tauri::command]
pub fn notification_settings(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    #[cfg(windows)]
    std::process::Command::new("explorer.exe").arg(NOTIFICATION_SETTINGS)
        .spawn().map_err(|_| "Settings didn’t open. Open it from the Start menu, then Notifications.".to_string())?;
    // Linux desktops each keep notifications in their own settings app, with no address in common. Say where instead.
    #[cfg(not(windows))]
    return Err("Turn on notifications for Dervo in your desktop's Settings, under Notifications.".into());
    #[cfg(windows)]
    Ok(())
}

/// The same names the Mac reports (notifications.rs, permission_name).
fn permission_name(state: PermissionState) -> &'static str {
    match state {
        PermissionState::Granted => "allowed",
        PermissionState::Denied => "denied",
        _ => "not_determined",
    }
}

#[tauri::command]
pub fn notification_permission(window: tauri::WebviewWindow, request: bool) -> Result<String, String> {
    main_window(&window)?;
    let notifications = window.notification();
    let state = notifications.permission_state().map_err(|e| e.to_string())?;
    if request && !matches!(state, PermissionState::Granted | PermissionState::Denied) {
        return Ok(permission_name(notifications.request_permission().map_err(|e| e.to_string())?).into());
    }
    Ok(permission_name(state).into())
}

#[tauri::command]
pub fn notification_post(window: tauri::WebviewWindow, id: String, title: String, body: String, project_id: String, channel_id: String) -> Result<bool, String> {
    main_window(&window)?;
    if title.is_empty() || title.len() > 200 || body.len() > 1000 || id.is_empty() || id.len() > 200 || project_id.len() > 200 || channel_id.len() > 200 {
        return Err("Notification text or ids are out of bounds".into());
    }
    let notifications = window.notification();
    if !matches!(notifications.permission_state(), Ok(PermissionState::Granted)) { return Ok(false) }
    notifications.builder().title(title).body(body).show().map_err(|e| e.to_string())?;
    Ok(true)
}

#[tauri::command]
pub fn dock_badge(app: AppHandle, window: tauri::WebviewWindow, count: u32) -> Result<(), String> {
    main_window(&window)?;
    let _ = (app, count);
    Ok(())
}
