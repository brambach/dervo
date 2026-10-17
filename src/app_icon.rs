//! The Mac icon picker (beta.15, group 7). The choice is one id in Dervo's own preferences (`DervoAppIcon` in the
//! `local.dervo.connected` domain). The running app shows it with `NSApp.applicationIconImage`, and the Dock tile plug-in
//! (src/docktile, in Contents/PlugIns) draws it while Dervo is closed. Finder, Spotlight and Launchpad keep the bundled
//! icon: changing that file would break the signature, and an update would put it back.
//!
//! Only ids on this list are written or drawn. The host decides whether the account may use the founder icon
//! (backend/app_icons.py); the preference itself isn't a lock, so a hand-edited value only changes the Dock picture.
use objc2::rc::{Allocated, Retained};
use objc2::runtime::{AnyClass, AnyObject, Bool};
use objc2::msg_send;
use objc2_foundation::NSString;
use serde_json::{json, Value};
use tauri::AppHandle;

use crate::bridge::Host;

/// The same ids, in the same order, as icons/choices/choices.json and backend/app_icons.py.
pub const ICONS: [&str; 10] = ["cream", "forest", "meadow", "peek", "crew", "terminal", "pixel", "night", "clay", "obsidian"];
pub const DEFAULT_ICON: &str = "cream";
const KEY: &str = "DervoAppIcon";
/// Posted after a change so a loaded Dock tile plug-in redraws (DervoDockTile.m listens for it).
const CHANGED: &str = "local.dervo.connected.appIconChanged";
const PLUGIN: &str = "PlugIns/DervoDockTile.docktileplugin";

pub fn known(icon: &str) -> bool { ICONS.contains(&icon) }

/// This app's Contents folder, when it runs from a bundle.
fn contents() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let contents = exe.parent()?.parent()?;
    (contents.file_name()? == "Contents").then(|| contents.to_path_buf())
}

/// The plug-in's copy of an icon, or None for Cream (the bundled icon) and for a build without the plug-in.
fn image_path(icon: &str) -> Option<std::path::PathBuf> {
    if icon == DEFAULT_ICON || !known(icon) { return None }
    let path = contents()?.join(PLUGIN).join("Contents/Resources").join(format!("{icon}.icns"));
    path.is_file().then_some(path)
}

fn has_plugin() -> bool { contents().is_some_and(|contents| contents.join(PLUGIN).join("Contents/Info.plist").is_file()) }

unsafe fn defaults() -> Option<*mut AnyObject> {
    let class = AnyClass::get(c"NSUserDefaults")?;
    let defaults: *mut AnyObject = msg_send![class, standardUserDefaults];
    (!defaults.is_null()).then_some(defaults)
}

/// The saved choice, or Cream when there's none or it isn't one Dervo knows.
pub fn saved() -> String {
    unsafe {
        let Some(defaults) = defaults() else { return DEFAULT_ICON.into() };
        let key = NSString::from_str(KEY);
        let value: *mut AnyObject = msg_send![defaults, stringForKey: &*key];
        if value.is_null() { return DEFAULT_ICON.into() }
        let text = (*(value as *const NSString)).to_string();
        if known(&text) { text } else { DEFAULT_ICON.into() }
    }
}

/// Show `icon` as the running app's Dock icon. Cream (or a missing image) puts the bundled icon back. Main thread only.
unsafe fn show(icon: &str) {
    let Some(class) = AnyClass::get(c"NSApplication") else { return };
    let application: *mut AnyObject = msg_send![class, sharedApplication];
    if application.is_null() { return }
    let image = image_path(icon).and_then(|path| {
        let class = AnyClass::get(c"NSImage")?;
        let path = NSString::from_str(&path.to_string_lossy());
        let allocated: Allocated<AnyObject> = msg_send![class, alloc];
        let image: Option<Retained<AnyObject>> = msg_send![allocated, initWithContentsOfFile: &*path];
        image
    });
    match &image {
        Some(image) => { let _: () = msg_send![application, setApplicationIconImage: &**image]; }
        None => { let _: () = msg_send![application, setApplicationIconImage: std::ptr::null::<AnyObject>()]; }
    }
}

/// Save the choice and tell the Dock tile plug-in. Main thread only.
unsafe fn save(icon: &str) {
    if let Some(defaults) = defaults() {
        let key = NSString::from_str(KEY);
        let value = NSString::from_str(icon);
        let _: () = msg_send![defaults, setObject: &*value, forKey: &*key];
        let _: Bool = msg_send![defaults, synchronize];
    }
    if let Some(class) = AnyClass::get(c"NSDistributedNotificationCenter") {
        let center: *mut AnyObject = msg_send![class, defaultCenter];
        if !center.is_null() {
            let name = NSString::from_str(CHANGED);
            let _: () = msg_send![center, postNotificationName: &*name, object: std::ptr::null::<AnyObject>(),
                userInfo: std::ptr::null::<AnyObject>(), deliverImmediately: Bool::YES];
        }
    }
}

/// At launch: show the saved choice in the Dock while Dervo runs.
pub fn setup<R: tauri::Runtime>(app: &AppHandle<R>) {
    let icon = saved();
    if icon == DEFAULT_ICON { return }
    let _ = app.run_on_main_thread(move || unsafe { show(&icon) });
}

fn main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" { return Err("Only the main window can change the app icon".into()) }
    Ok(())
}

/// What's chosen now, and whether this build carries the Dock tile plug-in (a development run doesn't).
#[tauri::command]
pub fn app_icon_get(window: tauri::WebviewWindow) -> Result<Value, String> {
    main_window(&window)?;
    Ok(json!({"icon": saved(), "dock": has_plugin()}))
}

/// Choose an icon. The host checks the account may use it (the founder icon) before anything is saved.
#[tauri::command]
pub fn app_icon_set(window: tauri::WebviewWindow, state: tauri::State<'_, Host>, icon: String) -> Result<Value, String> {
    main_window(&window)?;
    if !known(&icon) { return Err("That icon isn't one Dervo offers".into()) }
    let answer = state.call("_app_icon_check", json!({"icon": icon}))?;
    if answer.get("allowed").and_then(Value::as_bool) != Some(true) {
        return Err(answer.get("detail").and_then(Value::as_str).unwrap_or("That icon isn't available on this account").into());
    }
    // A synchronous command runs on the main thread, where AppKit wants this.
    objc2::MainThreadMarker::new().ok_or("The app icon must change on the main thread")?;
    unsafe {
        save(&icon);
        show(&icon);
    }
    Ok(json!({"icon": icon, "dock": has_plugin()}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_match_the_icon_catalogue() {
        let catalogue: Value = serde_json::from_str(include_str!("../icons/choices/choices.json")).unwrap();
        let ids: Vec<&str> = catalogue["choices"].as_array().unwrap().iter().map(|choice| choice["id"].as_str().unwrap()).collect();
        assert_eq!(ids, ICONS);
        assert_eq!(ICONS[0], DEFAULT_ICON);
    }

    #[test]
    fn only_listed_ids_are_known() {
        for icon in ICONS { assert!(known(icon)); }
        for icon in ["", "Cream", "obsidian ", "../obsidian", "gold-leaf", "obsidian.icns", "OBSIDIAN"] { assert!(!known(icon)); }
    }

    #[test]
    fn cream_and_unknown_ids_never_name_an_image() {
        assert_eq!(image_path(DEFAULT_ICON), None);
        assert_eq!(image_path("../../etc/passwd"), None);
    }

    #[test]
    fn the_plugin_listens_for_the_same_notification_and_key() {
        let source = include_str!("docktile/DervoDockTile.m");
        assert!(source.contains(&format!("@\"{CHANGED}\"")));
        assert!(source.contains(&format!("@\"{KEY}\"")));
        let plist = include_str!("../Info.plist");
        assert!(plist.contains("<key>NSDockTilePlugIn</key>\n\t<string>DervoDockTile.docktileplugin</string>"));
    }
}
