// A Windows release build is a windowed app, so no console window opens beside Dervo. The host already runs with its
// own hidden console (CREATE_NO_WINDOW, src/launch.rs). Debug builds keep the console for their output. No effect on a Mac.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod window_vibrancy;
mod draft_exit;
mod bridge;
// Keychain, notifications and the Dock icon are AppKit, Foundation and Security through and through. Other platforms
// get the same names from src/not_mac/ (b16/seam), so a Mac build compiles exactly the files it always did.
#[cfg_attr(not(target_os = "macos"), path = "not_mac/keychain.rs")]
mod keychain;
mod launch;
mod startup;
mod version_switch;
mod beta_updates;
#[cfg_attr(not(target_os = "macos"), path = "not_mac/notifications.rs")]
mod notifications;
mod release_signature;
mod preview;
mod menu;
// Voice for Home: its own supervised process, outside the host's request queue (backend/dervo_voice).
mod voice;
mod phone;
#[cfg_attr(not(target_os = "macos"), path = "not_mac/app_icon.rs")]
mod app_icon;
use tauri::Manager;
/// Dervo's own commands answer only the main webview's own page (security review M4, preview::own_origin). Core and
/// plugin commands never reach this handler; the capability ACL covers those.
fn guarded<R: tauri::Runtime>(handler: impl Fn(tauri::ipc::Invoke<R>) -> bool + Send + Sync + 'static) -> impl Fn(tauri::ipc::Invoke<R>) -> bool + Send + Sync + 'static {
    move |invoke| {
        let webview = invoke.message.webview();
        let origin = invoke.message.headers().get("Origin").and_then(|value| value.to_str().ok()).map(str::to_owned);
        if webview.label() != "main" || !preview::own_origin(origin.as_deref(), webview.url().ok().as_ref()) {
            invoke.resolver.reject("Only Dervo's own window can do this.");
            return true;
        }
        handler(invoke)
    }
}
fn main() {
    if std::env::args().nth(1).as_deref() == Some("--keychain-self-check") {
        match keychain::self_check() {
            Ok(()) => { println!("Native Keychain create, read, rotation, tombstone, Python denial and cleanup passed."); std::process::exit(0); }
            Err(error) => { eprintln!("{error}"); std::process::exit(1); }
        }
    }
    // The host checks a downloaded update's signature with this app's own compiled-in key, then this process exits
    // without opening a window.
    if std::env::args().nth(1).as_deref()==Some(release_signature::VERIFY_FLAG) {
        std::process::exit(release_signature::cli());
    }
    let builder = tauri::Builder::default();
    // Windows gets its folder picker and notifications from Tauri's plugins (bridge.rs, not_mac/notifications.rs).
    #[cfg(not(target_os = "macos"))]
    let builder = builder.plugin(tauri_plugin_dialog::init()).plugin(tauri_plugin_notification::init());
    // Dervo's own menu bar in place of Tauri's default, which had no Settings item (menu.rs). Mac only: on Windows a
    // menu sits inside the window as a Dervo/Edit/View/Window/Help strip (Harry, 27 Sep). Nothing is lost there: its
    // keys are the renderer's own (Ctrl+, and Ctrl+Shift+B) and WebView2 handles copy, paste and undo in fields.
    #[cfg(target_os = "macos")]
    let builder = builder.menu(menu::build).on_menu_event(menu::handle);
    let result = builder
        .manage(draft_exit::DraftExit::default())
        // Preview: agent-written pages from the project folder, on their own scheme and never the app's origin (preview.rs).
        .manage(preview::Grants::default())
        .manage(voice::Voice::default())
        // Off WebKit's thread, so a big file or many requests at once never stall the window (security review L2).
        .register_asynchronous_uri_scheme_protocol(preview::SCHEME, |ctx, request, responder| {
            let app = ctx.app_handle().clone();
            let webview = ctx.webview_label().to_owned();
            std::thread::spawn(move || {
                let grants = app.state::<preview::Grants>();
                responder.respond(preview::respond_limited(&grants, &webview, &request));
            });
        })
        .on_window_event(|window,event| {
            if window.label()=="main" {
                if let tauri::WindowEvent::CloseRequested{api,..}=event {
                    // With a phone paired, closing the window keeps Dervo running for the phone, as Messages does.
                    // Quit still quits. The Dock icon brings the window back (RunEvent::Reopen below).
                    if window.app_handle().try_state::<phone::PhoneLink>().is_some_and(|link| link.has_phones()) {
                        api.prevent_close();
                        if let Err(error)=window.hide() { eprintln!("Dervo couldn't hide the window: {error}"); }
                    } else if draft_exit::request(window.app_handle()){api.prevent_close();}
                }
            }
        })
        .setup(|app| {
            let script=app.path().resource_dir()?.join("backend/host.py");
            // Linux keeps the app's own Python among its resources, not beside the exe (launch::resolve_app_python).
            #[cfg(target_os = "linux")]
            let _ = launch::RESOURCES.set(app.path().resource_dir()?);
            // Data lives in the application data directory, or in an explicit DERVO_DATA_DIR for
            // disposable verification. Nothing is read or migrated from any other location.
            let default_dir=app.path().app_data_dir().map_err(|e|e.to_string());
            // Keep ordinary startup errors in the setup screen. Selection errors
            // are shown there too, rather than opening a different version silently.
            let selected=version_switch::redirect_selected(&script,default_dir.clone());
            if matches!(selected,Ok(true)) {app.handle().exit(0);return Ok(());}
            let default_dir_for_phone=default_dir.clone();
            let host=startup::Host::new(script,default_dir);
            if let Err(error)=selected {host.selection_error(error);}
            if host.status()?.kind!="selection" {host.retry();}
            app.manage(host);
            // The state spine: the host's push lines reach the window as one event, so it reads what changed instead of
            // polling (backend/thread_states.py).
            let handle=app.handle().clone();
            bridge::on_host_event(move |event| {
                use tauri::Emitter;
                let _=handle.emit_to("main","dervo-host-event",event);
            });
            // Dervo on your phone runs here, not in the window, so it keeps going while the window is closed. Its keys go
            // to the Keychain on a signed build with the everyday data folder, and to a private file otherwise.
            let data_override=std::env::var_os(launch::DATA_DIR_VARIABLE);
            match launch::resolve_data_dir(data_override.as_deref(),default_dir_for_phone) {
                Ok(data)=>{
                    #[cfg(target_os = "macos")]
                    let keychain=data_override.is_none()&&matches!(keychain::signed(),Ok(true));
                    #[cfg(not(target_os = "macos"))]
                    let keychain=false;
                    app.manage(phone::PhoneLink::start(app.handle().clone(),data,keychain));
                }
                Err(error)=>eprintln!("Dervo on your phone is off: {error}"),
            }
            // No configured window is auto-created. Choose the WebKit store before
            // renderer imports can read localStorage, IndexedDB or cookies.
            // Explicit data overrides are disposable sessions, even when invalid:
            // startup errors must never expose the everyday persistent store.
            // No override keeps the existing default store and draft keys intact.
            let window = app.config().app.windows.first()
                .ok_or("Missing main window configuration")?;
            // Dervo opens wide enough to show a project's board beside its sidebar. A display smaller than that gets
            // the display instead: the configured size is clamped to the work area, which already excludes the menu
            // bar and the Dock, so a laptop screen never opens a window it can't fit or reach the traffic lights on.
            let (width, height) = window_size(app, window);
            // The window is built hidden, painted Dervo's paper for the appearance macOS reports, then shown, so a cold
            // launch never flashes WebKit's white before the page's first frame. ThemeProvider takes over from there.
            let main = tauri::WebviewWindowBuilder::from_config(app, window)?
                .incognito(std::env::var_os(launch::DATA_DIR_VARIABLE).is_some())
                .inner_size(width, height)
                .center()
                .visible(false)
                .build()?;
            let theme = main.theme().unwrap_or(tauri::Theme::Light);
            if let Err(error) = main.set_background_color(Some(boot_background(theme))) { eprintln!("Dervo couldn't paint the window before launch: {error}"); }
            main.show()?;
            notifications::setup(app.handle());
            // The Mac icon picker (beta.15, group 7): the chosen icon in the Dock while Dervo runs.
            app_icon::setup(app.handle());
            let status=app.state::<startup::Host>().status()?;
            version_switch::acknowledge_startup(status.ready,&status.detail);
            Ok(())
        })
        .invoke_handler(guarded(tauri::generate_handler![beta_updates::download_beta_update,draft_exit::draft_guard_ready,draft_exit::draft_exit,window_vibrancy::set_window_vibrancy,bridge::local_request,preview::preview_grant,preview::preview_open_outside,bridge::pick_project,bridge::choose_project_folder,bridge::choose_folder_in,startup::startup_status,startup::startup_retry,version_switch::use_preview_version,version_switch::install_update,version_switch::restore_previous_version,notifications::notification_permission,notifications::notification_settings,notifications::notification_post,notifications::dock_badge,app_icon::app_icon_get,app_icon::app_icon_set,phone::phone_link_status,phone::phone_link_set_relay,phone::phone_pairing_offer,phone::phone_allow,phone::phone_set_approvals,phone::phone_deny,phone::phone_unpair,voice::voice_start,voice::voice_stop,voice::voice_command,voice::voice_install]))
        .build(tauri::generate_context!());
    match result {
        Ok(app)=>app.run(|handle,event| {
            if let tauri::RunEvent::ExitRequested{api,code,..}=&event {
                if code.is_none()&&draft_exit::request(handle){api.prevent_exit();}
            }
            // Quitting turns voice off properly: the process settles its journal and the microphone closes.
            if let tauri::RunEvent::Exit=&event {use tauri::Manager;handle.state::<voice::Voice>().supervisor.stop();
            }
            // The window a paired phone kept running behind (CloseRequested above) comes back from the Dock.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen{..}=event {
                if let Some(window)=handle.get_webview_window("main") { let _=window.show(); let _=window.set_focus(); }
            }
        }),
        Err(error)=>{version_switch::acknowledge_startup(false,&error.to_string());eprintln!("Dervo couldn't start: {error}");std::process::exit(1);}
    }
}

/// The size the main window opens at: what the configuration asks for, never larger than the primary display's work
/// area, and never below the configured minimum. A monitor Tauri can't report leaves the configured size alone.
fn window_size<R: tauri::Runtime>(app: &tauri::App<R>, window: &tauri::utils::config::WindowConfig) -> (f64, f64) {
    match app.primary_monitor() {
        Ok(Some(monitor)) => {
            let scale = monitor.scale_factor();
            let area = monitor.work_area();
            let (available_width, available_height) = (area.size.width as f64 / scale, area.size.height as f64 / scale);
            (fit_to_display(window.width, available_width, window.min_width.unwrap_or(0.0)),
             fit_to_display(window.height, available_height, window.min_height.unwrap_or(0.0)))
        }
        _ => (window.width, window.height),
    }
}

/// One axis of that decision. A work area smaller than the window's own minimum is not a size worth shrinking to, so
/// the configured value stands and the platform places what it can.
fn fit_to_display(want: f64, available: f64, least: f64) -> f64 {
    if available > least { want.min(available) } else { want }
}

/// The colour on the native window before the page paints: Dervo's paper (DervoTokens.css) for the appearance macOS
/// reports, opaque. The renderer's boot.css and index.html paint the same two values, so nothing flashes between them.
fn boot_background(theme: tauri::Theme) -> tauri::window::Color {
    match theme {
        tauri::Theme::Dark => tauri::window::Color(0x14, 0x13, 0x12, 0xff),
        _ => tauri::window::Color(0xf7, 0xf6, 0xf3, 0xff),
    }
}

#[cfg(test)]
mod window_tests {
    use super::{boot_background, fit_to_display};

    #[test] fn the_window_boots_in_the_paper_of_the_appearance_macos_reports() {
        let dark: (u8, u8, u8, u8) = boot_background(tauri::Theme::Dark).into();
        let light: (u8, u8, u8, u8) = boot_background(tauri::Theme::Light).into();
        // Paper at night and by day, from DervoTokens.css, and never translucent.
        assert_eq!(dark, (0x14, 0x13, 0x12, 0xff));
        assert_eq!(light, (0xf7, 0xf6, 0xf3, 0xff));
        // Negative control: the two appearances never share a colour, and neither is WebKit's white or black.
        assert_ne!(dark, light);
        assert!(dark != (0xff, 0xff, 0xff, 0xff) && light != (0xff, 0xff, 0xff, 0xff) && dark != (0, 0, 0, 0xff));
    }

    #[test] fn the_main_window_may_set_its_theme_and_nothing_wider() {
        let capability = include_str!("../capabilities/main.json");
        assert!(capability.contains("\"core:window:allow-set-theme\""));
        // Negative control: the theme permission came alone, not as the whole window set.
        assert!(!capability.contains("core:window:default"));
        assert!(!capability.contains("core:window:allow-set-background-color"));
    }

    #[test] fn a_window_opens_at_its_configured_size_and_never_larger_than_the_display() {
        // A display with room to spare: Dervo opens at the size it asks for.
        assert_eq!(fit_to_display(1512.0, 1920.0, 800.0), 1512.0);
        // A smaller laptop: the window shrinks to the work area rather than hanging off it.
        assert_eq!(fit_to_display(1512.0, 1440.0, 800.0), 1440.0);
        assert_eq!(fit_to_display(945.0, 900.0, 500.0), 900.0);
        // Exactly the work area is not too big.
        assert_eq!(fit_to_display(1512.0, 1512.0, 800.0), 1512.0);
        // A work area at or below the minimum is not worth shrinking to: the configured size stands.
        assert_eq!(fit_to_display(1512.0, 800.0, 800.0), 1512.0);
        assert_eq!(fit_to_display(1512.0, 640.0, 800.0), 1512.0);
        // No minimum configured still clamps, because any positive work area beats zero.
        assert_eq!(fit_to_display(1512.0, 1024.0, 0.0), 1024.0);
    }
}
