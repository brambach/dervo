use serde_json::Value;
#[cfg(any(target_os = "macos", test))]
use serde_json::json;
use crate::bridge::Host;

#[cfg_attr(not(target_os = "macos"), allow(dead_code))] // the Mac download picker's check
fn ready(status: &Value, context: &str, release_id: &str) -> bool {
    status.get("context").and_then(Value::as_str) == Some(context)
        && status.get("canDownload").and_then(Value::as_bool) == Some(true)
        && status.pointer("/release/release_id").and_then(Value::as_str) == Some(release_id)
}

#[cfg(target_os = "macos")]
#[tauri::command]
pub fn download_beta_update(window: tauri::WebviewWindow, state: tauri::State<'_, Host>, context: String, release_id: String) -> Result<Value, String> {
    if window.label() != "main" { return Err("Only the main window can download Dervo.".into()); }
    let status = state.call("beta_update_status", json!({}))?;
    if !ready(&status, &context, &release_id) { return Err("Check for updates before choosing a download folder.".into()); }
    use objc2::MainThreadMarker;
    use objc2_app_kit::NSOpenPanel;
    let mtm = MainThreadMarker::new().ok_or("The download folder picker must run on the main thread.")?;
    let panel = NSOpenPanel::openPanel(mtm);
    panel.setCanChooseDirectories(true);
    panel.setCanChooseFiles(false);
    panel.setAllowsMultipleSelection(false);
    if panel.runModal() != 1 { return Ok(Value::Null); }
    let folder = panel.URL().and_then(|url| url.path()).ok_or("Choose a local download folder.")?.to_string();
    // Only this native selection supplies a folder. The host checks activation,
    // release identity and membership again after the picker closes.
    state.call("_beta_update_download", json!({"context":context,"releaseId":release_id,"folder":folder}))
}

/// The download folder comes from AppKit's NSOpenPanel above. Windows updates come with b16/windows (milestone 5).
#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub async fn download_beta_update(window: tauri::WebviewWindow, state: tauri::State<'_, Host>, context: String, release_id: String) -> Result<Value, String> {
    if window.label() != "main" { return Err("Only the main window can download Dervo.".into()); }
    let _ = (state, context, release_id);
    Err("Downloading an update isn't available on this computer yet.".into())
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn selection_requires_exact_current_available_release() {
        let status = json!({"context":"new","canDownload":true,"release":{"release_id":"v2"}});
        assert!(ready(&status, "new", "v2"));
        assert!(!ready(&status, "old", "v2"));
        assert!(!ready(&status, "new", "v1"));
        assert!(!ready(&json!({"context":"new","canDownload":false,"release":{"release_id":"v2"}}), "new", "v2"));
        assert!(!ready(&Value::Null, "new", "v2"));
    }
}
