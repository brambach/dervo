//! A13 and A14: macOS notifications and the Dock badge.
//!
//! Notifications go through the system UserNotifications framework, called through objc2 so no new crate is needed: the
//! objc2, objc2-foundation and block2 versions here are the ones already in Cargo.lock. The renderer decides what to post
//! and never posts for the conversation on screen. A click brings the main window forward and emits
//! `dervo-notification-open` with the project and conversation, which the renderer opens with its Task guide.
//!
//! UserNotifications only works inside an app bundle with an identifier. A bare `cargo run` binary has none, and asking
//! for the notification center there raises an Objective-C exception, so every entry point checks the bundle first and
//! does nothing without one.

use std::sync::{mpsc, OnceLock};
use std::time::Duration;

use block2::{Block, RcBlock};
use objc2::rc::Retained;
use objc2::runtime::{AnyClass, AnyObject, Bool, NSObject};
use objc2::{define_class, msg_send, AnyThread, ClassType};
use objc2_foundation::{NSDictionary, NSObjectProtocol, NSString};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

#[link(name = "UserNotifications", kind = "framework")]
extern "C" {}

/// Keys the renderer's notifications carry, and the event a click emits.
pub const PROJECT_KEY: &str = "projectId";
pub const CHANNEL_KEY: &str = "channelId";
pub const OPEN_EVENT: &str = "dervo-notification-open";

// UNAuthorizationOptions and UNNotificationPresentationOptions, from the framework headers.
const AUTHORIZE_BADGE_SOUND_ALERT: usize = 1 | 2 | 4;
const PRESENT_SOUND_LIST_BANNER: usize = 2 | 8 | 16;

static APP: OnceLock<AppHandle> = OnceLock::new();

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Opened {
    pub project_id: String,
    pub channel_id: String,
}

/// The Dock badge text for a count: nothing at zero, the number otherwise, capped so it stays readable.
pub fn badge_label(count: u32) -> Option<String> {
    match count {
        0 => None,
        1..=99 => Some(count.to_string()),
        _ => Some("99+".into()),
    }
}

/// What getNotificationSettings reports, as the renderer names it.
pub fn permission_name(status: isize) -> &'static str {
    match status {
        0 => "not_determined",
        1 => "denied",
        2 | 3 | 4 => "allowed",
        _ => "unknown",
    }
}

define_class!(
    // SAFETY: NSObject has no subclassing requirements, and this class has no ivars and no Drop.
    #[unsafe(super(NSObject))]
    #[name = "DervoNotificationDelegate"]
    struct NotificationDelegate;

    impl NotificationDelegate {
        #[unsafe(method(userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:))]
        fn did_receive(&self, _center: *mut AnyObject, response: *mut AnyObject, completion: &Block<dyn Fn()>) {
            unsafe { opened(response) };
            completion.call(());
        }

        #[unsafe(method(userNotificationCenter:willPresentNotification:withCompletionHandler:))]
        fn will_present(&self, _center: *mut AnyObject, _notification: *mut AnyObject, completion: &Block<dyn Fn(usize)>) {
            // Shown while Dervo is in front too; the renderer already left out the conversation on screen.
            completion.call((PRESENT_SOUND_LIST_BANNER,));
        }
    }

    unsafe impl NSObjectProtocol for NotificationDelegate {}
);

impl NotificationDelegate {
    fn new() -> Retained<Self> {
        let this = Self::alloc().set_ivars(());
        unsafe { msg_send![super(this), init] }
    }
}

fn bundled() -> bool {
    let Some(class) = AnyClass::get(c"NSBundle") else { return false };
    unsafe {
        let bundle: *mut AnyObject = msg_send![class, mainBundle];
        if bundle.is_null() {
            return false;
        }
        let identifier: *mut AnyObject = msg_send![bundle, bundleIdentifier];
        !identifier.is_null()
    }
}

fn center() -> Option<*mut AnyObject> {
    if !bundled() {
        return None;
    }
    let class = AnyClass::get(c"UNUserNotificationCenter")?;
    let center: *mut AnyObject = unsafe { msg_send![class, currentNotificationCenter] };
    (!center.is_null()).then_some(center)
}

unsafe fn string_for(dictionary: *mut AnyObject, key: &str) -> Option<String> {
    if dictionary.is_null() {
        return None;
    }
    let key = NSString::from_str(key);
    let value: *mut AnyObject = msg_send![dictionary, objectForKey: &*key];
    if value.is_null() {
        return None;
    }
    let is_string: Bool = msg_send![value, isKindOfClass: NSString::class()];
    if !is_string.as_bool() {
        return None;
    }
    Some((*(value as *const NSString)).to_string())
}

unsafe fn opened(response: *mut AnyObject) {
    if response.is_null() {
        return;
    }
    let notification: *mut AnyObject = msg_send![response, notification];
    let request: *mut AnyObject = msg_send![notification, request];
    let content: *mut AnyObject = msg_send![request, content];
    let info: *mut AnyObject = msg_send![content, userInfo];
    let (Some(project_id), Some(channel_id)) = (string_for(info, PROJECT_KEY), string_for(info, CHANNEL_KEY)) else { return };
    let Some(app) = APP.get() else { return };
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
    let _ = app.emit_to("main", OPEN_EVENT, Opened { project_id, channel_id });
}

/// Install the click delegate at launch, so a click that launched or woke Dervo still reaches the renderer.
pub fn setup(app: &AppHandle) {
    let _ = APP.set(app.clone());
    let Some(center) = center() else { return };
    let delegate = NotificationDelegate::new();
    unsafe {
        let _: () = msg_send![center, setDelegate: &*delegate];
    }
    // The center holds its delegate weakly; this one lives as long as the app.
    std::mem::forget(delegate);
}

fn main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Only the main window can use notifications".into());
    }
    Ok(())
}

/// Where "Open System Settings" goes when notifications are off: the Notifications pane, a fixed destination.
pub const NOTIFICATION_SETTINGS: &str = "x-apple.systempreferences:com.apple.Notifications-Settings.extension";

/// Opens System Settings at Notifications, so turning Dervo's notifications back on is one click from Preferences.
/// Fixed destination only: nothing from the renderer is opened.
#[tauri::command]
pub fn notification_settings(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    std::process::Command::new("/usr/bin/open").arg(NOTIFICATION_SETTINGS)
        .spawn().map_err(|_| "System Settings didn’t open. Open it from the Apple menu, then Notifications.".to_string())?;
    Ok(())
}

/// The current permission, asking macOS first when `request` is true and nothing was decided yet.
#[tauri::command]
pub fn notification_permission(window: tauri::WebviewWindow, request: bool) -> Result<String, String> {
    main_window(&window)?;
    let Some(center) = center() else { return Ok("unavailable".into()) };
    let (sender, receiver) = mpsc::channel::<isize>();
    let settings = RcBlock::new(move |settings: *mut AnyObject| {
        let status: isize = if settings.is_null() { -1 } else { unsafe { msg_send![settings, authorizationStatus] } };
        let _ = sender.send(status);
    });
    unsafe {
        let _: () = msg_send![center, getNotificationSettingsWithCompletionHandler: &*settings];
    }
    let status = receiver.recv_timeout(Duration::from_secs(10)).map_err(|_| "macOS didn't report the notification setting".to_string())?;
    if status != 0 || !request {
        return Ok(permission_name(status).into());
    }
    let (sender, receiver) = mpsc::channel::<bool>();
    let answer = RcBlock::new(move |granted: Bool, _error: *mut AnyObject| {
        let _ = sender.send(granted.as_bool());
    });
    unsafe {
        let _: () = msg_send![center, requestAuthorizationWithOptions: AUTHORIZE_BADGE_SOUND_ALERT, completionHandler: &*answer];
    }
    // The system prompt waits for a person, so this allows a few minutes.
    let granted = receiver.recv_timeout(Duration::from_secs(300)).map_err(|_| "No answer to the notification prompt yet".to_string())?;
    Ok(if granted { "allowed" } else { "denied" }.into())
}

/// Post one notification. Nothing is sent when notifications aren't available or allowed; macOS decides that.
#[tauri::command]
pub fn notification_post(window: tauri::WebviewWindow, id: String, title: String, body: String, project_id: String, channel_id: String) -> Result<bool, String> {
    main_window(&window)?;
    if title.is_empty() || title.len() > 200 || body.len() > 1000 || id.is_empty() || id.len() > 200 || project_id.len() > 200 || channel_id.len() > 200 {
        return Err("Notification text or ids are out of bounds".into());
    }
    let Some(center) = center() else { return Ok(false) };
    let (Some(content_class), Some(request_class), Some(sound_class)) = (
        AnyClass::get(c"UNMutableNotificationContent"),
        AnyClass::get(c"UNNotificationRequest"),
        AnyClass::get(c"UNNotificationSound"),
    ) else {
        return Ok(false);
    };
    unsafe {
        let content: Option<Retained<AnyObject>> = msg_send![content_class, new];
        let Some(content) = content else { return Ok(false) };
        let title = NSString::from_str(&title);
        let body = NSString::from_str(&body);
        let _: () = msg_send![&*content, setTitle: &*title];
        let _: () = msg_send![&*content, setBody: &*body];
        let keys = [NSString::from_str(PROJECT_KEY), NSString::from_str(CHANNEL_KEY)];
        let values = [NSString::from_str(&project_id), NSString::from_str(&channel_id)];
        let info = NSDictionary::from_retained_objects(&[&*keys[0], &*keys[1]], &[values[0].clone(), values[1].clone()]);
        let _: () = msg_send![&*content, setUserInfo: &*info];
        let sound: *mut AnyObject = msg_send![sound_class, defaultSound];
        let _: () = msg_send![&*content, setSound: sound];
        let identifier = NSString::from_str(&id);
        let request: *mut AnyObject = msg_send![request_class, requestWithIdentifier: &*identifier, content: &*content, trigger: std::ptr::null_mut::<AnyObject>()];
        let _: () = msg_send![center, addNotificationRequest: request, withCompletionHandler: std::ptr::null::<Block<dyn Fn(*mut AnyObject)>>()];
    }
    Ok(true)
}

/// Set or clear the Dock badge. It counts only what waits on you.
#[tauri::command]
pub fn dock_badge(app: AppHandle, window: tauri::WebviewWindow, count: u32) -> Result<(), String> {
    main_window(&window)?;
    let label = badge_label(count);
    app.run_on_main_thread(move || {
        let Some(class) = AnyClass::get(c"NSApplication") else { return };
        unsafe {
            let application: *mut AnyObject = msg_send![class, sharedApplication];
            if application.is_null() {
                return;
            }
            let tile: *mut AnyObject = msg_send![application, dockTile];
            match &label {
                Some(text) => {
                    let text = NSString::from_str(text);
                    let _: () = msg_send![tile, setBadgeLabel: &*text];
                }
                None => {
                    let _: () = msg_send![tile, setBadgeLabel: std::ptr::null::<NSString>()];
                }
            }
        }
    })
    .map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn badge_counts_only_something_and_stays_short() {
        assert_eq!(badge_label(0), None);
        assert_eq!(badge_label(1).as_deref(), Some("1"));
        assert_eq!(badge_label(99).as_deref(), Some("99"));
        assert_eq!(badge_label(100).as_deref(), Some("99+"));
    }

    #[test]
    fn the_settings_link_is_the_notifications_pane_and_nothing_else() {
        assert_eq!(NOTIFICATION_SETTINGS, "x-apple.systempreferences:com.apple.Notifications-Settings.extension");
        assert!(!NOTIFICATION_SETTINGS.contains(' ') && !NOTIFICATION_SETTINGS.starts_with("http"));
    }

    #[test]
    fn permission_names_cover_every_documented_status() {
        assert_eq!(permission_name(0), "not_determined");
        assert_eq!(permission_name(1), "denied");
        for allowed in [2, 3, 4] {
            assert_eq!(permission_name(allowed), "allowed");
        }
        assert_eq!(permission_name(-1), "unknown");
        assert_eq!(permission_name(9), "unknown");
    }

    #[test]
    fn a_bare_test_binary_has_no_notification_center() {
        // cargo test runs outside an app bundle: every entry point must stand down instead of raising.
        assert!(center().is_none());
    }
}
