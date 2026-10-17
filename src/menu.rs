//! Dervo's menu bar. Tauri's default menu has no Settings item, so Dervo builds its own: the Dervo menu with About,
//! Settings…, Check for Updates…, Services, Hide and Quit; Edit, View and Window from the platform's own items, so copy,
//! paste, undo and the window list keep working in every field; and Help with Report a Dervo Bug… and Send Feedback….
//!
//! Dervo's own items send one event, `dervo-menu`, to the main window, naming the action. The renderer runs the same
//! handler as the key (nativeMenu.ts). WebKit hands a key to the page first and passes it to the menu bar only when the
//! page left it alone, so ⌘, and ⌘⇧B run once whichever way they arrive. The window can listen for this event already
//! (core:event:allow-listen) and can't emit one, so nothing in a page can pretend to be the menu.
use tauri::menu::{AboutMetadata, Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID, WINDOW_SUBMENU_ID};
use tauri::{AppHandle, Emitter, Runtime};

/// The one event the menu sends to the main window. Its payload is an action name from ACTIONS.
pub const EVENT: &str = "dervo-menu";

/// Dervo's own items: menu id, the action the renderer is told, the label, and the key the item shows. The keys are the
/// renderer's own (⌘, in ConnectedApp.tsx, ⌘⇧B beside it), so the menu names a key that already works.
pub const ACTIONS: [(&str, &str, &str, Option<&str>); 4] = [
    ("dervo-settings", "settings", "Settings…", Some("CmdOrCtrl+,")),
    ("dervo-check-updates", "check-updates", "Check for Updates…", None),
    ("dervo-report-bug", "report-bug", "Report a Dervo Bug…", Some("CmdOrCtrl+Shift+B")),
    ("dervo-send-feedback", "send-feedback", "Send Feedback…", None),
];

/// The action a menu id stands for, or None for the platform's own items, which handle themselves.
pub fn action(id: &str) -> Option<&'static str> {
    ACTIONS.iter().find(|(menu, ..)| *menu == id).map(|(_, action, ..)| *action)
}

fn item<R: Runtime>(app: &AppHandle<R>, id: &str) -> tauri::Result<MenuItem<R>> {
    let (menu, _, label, keys) = ACTIONS.iter().find(|(menu, ..)| *menu == id).copied().expect("a menu id from ACTIONS");
    MenuItem::with_id(app, menu, label, true, keys)
}

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let about = AboutMetadata { name: Some("Dervo".into()), version: Some(app.package_info().version.to_string()), ..Default::default() };
    let dervo = Submenu::with_items(app, "Dervo", true, &[
        &PredefinedMenuItem::about(app, Some("About Dervo"), Some(about))?,
        &PredefinedMenuItem::separator(app)?,
        &item(app, "dervo-settings")?,
        &item(app, "dervo-check-updates")?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::services(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::hide(app, Some("Hide Dervo"))?,
        &PredefinedMenuItem::hide_others(app, None)?,
        &PredefinedMenuItem::show_all(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        // The platform's Quit, as in Tauri's default menu, so closing still waits for drafts to save (draft_exit.rs).
        &PredefinedMenuItem::quit(app, Some("Quit Dervo"))?,
    ])?;
    let edit = Submenu::with_items(app, "Edit", true, &[
        &PredefinedMenuItem::undo(app, None)?,
        &PredefinedMenuItem::redo(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::cut(app, None)?,
        &PredefinedMenuItem::copy(app, None)?,
        &PredefinedMenuItem::paste(app, None)?,
        &PredefinedMenuItem::select_all(app, None)?,
    ])?;
    let view = Submenu::with_items(app, "View", true, &[&PredefinedMenuItem::fullscreen(app, None)?])?;
    // These two ids make macOS treat the menus as its own Window and Help menus: the window list, and Help's search.
    let window = Submenu::with_id_and_items(app, WINDOW_SUBMENU_ID, "Window", true, &[
        &PredefinedMenuItem::minimize(app, None)?,
        &PredefinedMenuItem::maximize(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::close_window(app, None)?,
    ])?;
    let help = Submenu::with_id_and_items(app, HELP_SUBMENU_ID, "Help", true, &[
        &item(app, "dervo-report-bug")?,
        &item(app, "dervo-send-feedback")?,
    ])?;
    Menu::with_items(app, &[&dervo, &edit, &view, &window, &help])
}

/// A click on one of Dervo's own items tells the main window which action. The platform's items never get here.
pub fn handle<R: Runtime>(app: &AppHandle<R>, event: MenuEvent) {
    if let Some(action) = action(event.id().as_ref()) {
        if let Err(error) = app.emit_to("main", EVENT, action) { eprintln!("Dervo couldn't pass a menu choice to its window: {error}"); }
    }
}

#[cfg(test)]
mod tests {
    use super::{action, ACTIONS, EVENT};

    #[test] fn each_dervo_item_names_one_action_and_the_platform_items_name_none() {
        assert_eq!(action("dervo-settings"), Some("settings"));
        assert_eq!(action("dervo-check-updates"), Some("check-updates"));
        assert_eq!(action("dervo-report-bug"), Some("report-bug"));
        assert_eq!(action("dervo-send-feedback"), Some("send-feedback"));
        // Negative control: an id the menu didn't make, or a near miss, is no action.
        assert_eq!(action("quit"), None);
        assert_eq!(action("dervo-settings "), None);
        assert_eq!(action(""), None);
        let mut ids: Vec<_> = ACTIONS.iter().map(|(id, ..)| *id).collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), ACTIONS.len(), "every menu id is its own");
    }

    #[test] fn the_menu_shows_the_keys_the_window_already_answers() {
        let keys: Vec<_> = ACTIONS.iter().filter_map(|(_, action, _, keys)| keys.map(|keys| (*action, keys))).collect();
        assert_eq!(keys, vec![("settings", "CmdOrCtrl+,"), ("report-bug", "CmdOrCtrl+Shift+B")]);
        // The renderer's handlers for the same keys, and the event and actions it listens for.
        let shell = include_str!("../frontend/src/local/ConnectedApp.tsx");
        let menu = include_str!("../frontend/src/local/nativeMenu.ts");
        assert!(shell.contains("key===','") && shell.contains("useBugKey(reportBug)") && menu.contains("isBugKey(event)"));
        assert!(menu.contains(&format!("'{EVENT}'")));
        for (_, action, ..) in ACTIONS { assert!(menu.contains(&format!("'{action}'")), "{action} is handled"); }
    }

    #[test] fn the_window_may_listen_for_the_menu_but_never_send_as_it() {
        let capability = include_str!("../capabilities/main.json");
        assert!(capability.contains("\"core:event:allow-listen\""));
        // Negative control: no page can emit events, so none can fake a menu choice, and no wider event set came in.
        assert!(!capability.contains("allow-emit"));
        assert!(!capability.contains("core:event:default"));
        assert!(!capability.contains("core:menu"));
    }
}
