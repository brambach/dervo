//! Preview (beta.11, panel direction C): what an agent made, shown beside the thread. Agent-written HTML is untrusted,
//! so it never loads from the app's own origin. It comes from `dervo-preview://<grant>/<path>`, a scheme only this
//! module answers, and the renderer puts it in an iframe sandboxed without `allow-same-origin`. Every response also
//! carries its own CSP with `sandbox allow-scripts`, so the page stays in an opaque origin even if something loads it
//! outside that iframe. The reasoning and the tests for each rule are in notes/PLAN-panels.md, "Preview security".
//!
//! Rules this file enforces:
//! - A grant names one project folder, and only the main window can ask for one. The folder comes from the host's own
//!   project list, never from the renderer.
//! - Only files inside that folder after symlinks are resolved, never a name starting with a dot anywhere in the path
//!   (`.env`, `.git`, `..`), only viewable types, never a directory (no listing), and only GET.
//! - Only the main webview gets an answer.
//! - Local pages are `http://localhost:<port>` or `http://127.0.0.1:<port>` only, checked here before `open` sees them.
use serde_json::{json, Value};
use std::{borrow::Cow, collections::HashMap, io::Read, path::{Path, PathBuf}, sync::Mutex};
use tauri::http::{Request, Response, StatusCode};

pub const SCHEME: &str = "dervo-preview";
/// A mockup or a screenshot, never an export of the project. Anything bigger is refused rather than streamed.
pub const MAX_BYTES: u64 = 32 * 1024 * 1024;

#[derive(Default)]
pub struct Grants(Mutex<HashMap<String, PathBuf>>);

impl Grants {
    /// The grant for this folder: the same one each time, so the frame's origin stays put while you look at it.
    pub fn grant(&self, root: &Path) -> Result<String, String> {
        let root = allowed_root(root)?;
        let mut grants = self.0.lock().map_err(|_| "Preview is unavailable. Restart Dervo.".to_string())?;
        if let Some((id, _)) = grants.iter().find(|(_, path)| **path == root) { return Ok(id.clone()); }
        let id = random_id()?;
        grants.insert(id.clone(), root);
        Ok(id)
    }
    pub fn root(&self, id: &str) -> Option<PathBuf> { self.0.lock().ok()?.get(id).cloned() }
}

/// A project folder that may be previewed: it exists, it's a directory, and it isn't the whole disk or your home folder.
pub fn allowed_root(root: &Path) -> Result<PathBuf, String> {
    let root = root.canonicalize().map_err(|_| "That project folder isn't there any more.".to_string())?;
    if !root.is_dir() { return Err("That project folder isn't there any more.".into()); }
    let home = std::env::var_os("HOME").map(PathBuf::from).and_then(|home| home.canonicalize().ok());
    if root.parent().is_none() || Some(&root) == home.as_ref() {
        return Err("Preview needs a project folder, not the whole disk or your home folder.".into());
    }
    Ok(root)
}

fn random_id() -> Result<String, String> {
    let mut bytes = [0u8; 16];
    std::fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut bytes)).map_err(|e| e.to_string())?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// The content type for a viewable file, by extension. Anything else isn't served.
pub fn content_type(path: &Path) -> Option<&'static str> {
    let ext = path.extension()?.to_str()?.to_ascii_lowercase();
    Some(match ext.as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "avif" => "image/avif",
        "svg" => "image/svg+xml",
        "ico" => "image/x-icon",
        // A PDF an agent made (beta.15, group 1). It loads under the same page policy and sandbox as a mockup.
        "pdf" => "application/pdf",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        _ => return None,
    })
}

fn percent_decode(raw: &str) -> Option<String> {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            let hex = std::str::from_utf8(bytes.get(i + 1..i + 3)?).ok()?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            i += 3;
        } else { out.push(bytes[i]); i += 1; }
    }
    String::from_utf8(out).ok()
}

/// Why a request got no file. Every one is a plain 404 to the page, so a page can't probe what exists.
#[derive(Debug, PartialEq)]
pub enum Refused { Encoding, Hidden, Outside, Missing, NotAFile, Kind, TooBig }

/// The file a request path names inside `root`, or why not. `request_path` is the URL path, still percent-encoded.
pub fn resolve(root: &Path, request_path: &str) -> Result<PathBuf, Refused> {
    let decoded = percent_decode(request_path.split(['?', '#']).next().unwrap_or("")).ok_or(Refused::Encoding)?;
    if decoded.contains('\0') || decoded.contains('\\') { return Err(Refused::Encoding); }
    let parts: Vec<&str> = decoded.split('/').filter(|part| !part.is_empty()).collect();
    if parts.is_empty() { return Err(Refused::NotAFile); }
    // `.`, `..`, `.env`, `.git` and every other dot name, wherever it sits in the path.
    if parts.iter().any(|part| part.starts_with('.')) { return Err(Refused::Hidden); }
    let root = root.canonicalize().map_err(|_| Refused::Missing)?;
    let joined = parts.iter().fold(root.clone(), |path, part| path.join(part));
    let real = joined.canonicalize().map_err(|_| Refused::Missing)?;
    // Symlinks resolved: the real file must still be inside the project, and its real path mustn't be a dot name either
    // (`notes.txt -> .env` inside the project is refused the same as `.env`).
    let inside = real.strip_prefix(&root).map_err(|_| Refused::Outside)?;
    if inside.components().any(|c| c.as_os_str().to_string_lossy().starts_with('.')) { return Err(Refused::Hidden); }
    let meta = std::fs::metadata(&real).map_err(|_| Refused::Missing)?;
    if !meta.is_file() { return Err(Refused::NotAFile); }
    if content_type(&real).is_none() { return Err(Refused::Kind); }
    if meta.len() > MAX_BYTES { return Err(Refused::TooBig); }
    Ok(real)
}

/// The policy on every preview response. The page may run its own scripts and styles and load files from its own grant,
/// and nothing else: no network, no frames, no forms, no workers, no plugins, no base changes. `sandbox allow-scripts`
/// keeps it in an opaque origin even when loaded outside Dervo's sandboxed iframe.
pub fn page_policy(grant: &str) -> String {
    let own = format!("{SCHEME}://{grant}");
    format!("default-src 'none'; script-src {own} 'unsafe-inline'; style-src {own} 'unsafe-inline'; img-src {own} data: blob:; \
font-src {own} data:; media-src {own} data: blob:; connect-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; \
manifest-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; sandbox allow-scripts")
}

fn empty(status: StatusCode) -> Response<Cow<'static, [u8]>> {
    Response::builder().status(status).header("Content-Type", "text/plain; charset=utf-8").header("Cache-Control", "no-store")
        .header("Content-Security-Policy", "default-src 'none'; sandbox").body(Cow::Borrowed(&b""[..])).unwrap()
}

/// The file `resolve` returned, read once through one handle (security review L2): opened without following a link
/// that was swapped in since, checked as a regular file on that handle, and never more than `MAX_BYTES` read even if it
/// grows after the check.
pub fn read_bounded(path: &Path) -> Option<Vec<u8>> {
    #[cfg(unix)]
    use std::os::unix::fs::OpenOptionsExt;
    // The platform's own value: 0x0100 on macOS, but 0x20000 on x86-64 Linux, where 0x0100 is O_NOCTTY and a swapped-in
    // link was followed.
    #[cfg(unix)]
    const O_NOFOLLOW: i32 = libc::O_NOFOLLOW;
    // Windows opens a link itself rather than its target, so the regular-file check below refuses it (b16/seam).
    #[cfg(windows)]
    use std::os::windows::fs::OpenOptionsExt;
    #[cfg(windows)]
    const O_NOFOLLOW: u32 = 0x0020_0000; // FILE_FLAG_OPEN_REPARSE_POINT
    let file = std::fs::OpenOptions::new().read(true).custom_flags(O_NOFOLLOW).open(path).ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.len() > MAX_BYTES { return None; }
    let mut body = Vec::with_capacity(meta.len() as usize);
    file.take(MAX_BYTES + 1).read_to_end(&mut body).ok()?;
    if body.len() as u64 > MAX_BYTES { return None; }
    Some(body)
}

/// Reads in flight at once, across every preview page. A page asking for the same big file under 500 query strings
/// gets 503 past this, rather than holding 500 copies in memory (security review L2).
pub const MAX_READS: usize = 4;
static READS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

/// `respond`, off WebKit's thread (main.rs registers the scheme as asynchronous), with at most `MAX_READS` at once.
pub fn respond_limited(grants: &Grants, webview: &str, request: &Request<Vec<u8>>) -> Response<Cow<'static, [u8]>> {
    use std::sync::atomic::Ordering;
    if READS.fetch_add(1, Ordering::SeqCst) >= MAX_READS {
        READS.fetch_sub(1, Ordering::SeqCst);
        return empty(StatusCode::SERVICE_UNAVAILABLE);
    }
    let response = respond(grants, webview, request);
    READS.fetch_sub(1, Ordering::SeqCst);
    response
}

/// Answer one `dervo-preview://` request. `webview` is the label of the webview that asked.
pub fn respond(grants: &Grants, webview: &str, request: &Request<Vec<u8>>) -> Response<Cow<'static, [u8]>> {
    if webview != "main" { return empty(StatusCode::FORBIDDEN); }
    if request.method() != tauri::http::Method::GET { return empty(StatusCode::METHOD_NOT_ALLOWED); }
    let uri = request.uri();
    let Some(grant) = uri.host().map(str::to_ascii_lowercase) else { return empty(StatusCode::NOT_FOUND) };
    let Some(root) = grants.root(&grant) else { return empty(StatusCode::NOT_FOUND) };
    let Ok(path) = resolve(&root, uri.path()) else { return empty(StatusCode::NOT_FOUND) };
    let Some(body) = read_bounded(&path) else { return empty(StatusCode::NOT_FOUND) };
    Response::builder().status(StatusCode::OK)
        .header("Content-Type", content_type(&path).unwrap_or("application/octet-stream"))
        .header("Content-Security-Policy", page_policy(&grant))
        .header("X-Content-Type-Options", "nosniff")
        .header("Referrer-Policy", "no-referrer")
        .header("Cache-Control", "no-store")
        .body(Cow::Owned(body)).unwrap()
}

/// A local page you typed: `http://localhost:<port>` or `http://127.0.0.1:<port>`, with an explicit port and an
/// optional path. No other host, no https (nothing local needs it), no user or password, no other scheme.
pub fn local_page(url: &str) -> Option<String> {
    let rest = url.strip_prefix("http://")?;
    let (authority, path) = match rest.find(['/', '?', '#']) { Some(i) => (&rest[..i], &rest[i..]), None => (rest, "/") };
    let (host, port) = authority.rsplit_once(':')?;
    if host != "localhost" && host != "127.0.0.1" { return None; }
    if port.is_empty() || port.len() > 5 || !port.bytes().all(|b| b.is_ascii_digit()) { return None; }
    let number: u32 = port.parse().ok()?;
    if number == 0 || number > 65535 { return None; }
    if path.chars().any(|c| c.is_whitespace() || c.is_control() || c == '\\') { return None; }
    Some(format!("http://{host}:{number}{path}"))
}

fn project_folder(projects: &Value, project_id: &str) -> Option<PathBuf> {
    projects.as_array()?.iter().find(|p| p.get("id").and_then(Value::as_str) == Some(project_id))
        .and_then(|p| p.get("cwd")).and_then(Value::as_str).map(PathBuf::from)
}

/// A thread's own folder (thread_folders.py), from the host's binding, when it's there. What a thread that changes code
/// made is in that folder, not the project's (Bryce, 8 Oct). Otherwise the binding's cwd is the project folder anyway.
fn thread_folder(binding: &Value) -> Option<PathBuf> {
    let folder = PathBuf::from(binding.get("cwd")?.as_str()?);
    (folder.is_absolute() && folder.is_dir()).then_some(folder)
}

/// The grant for a project's folder, or for the thread's own folder when `thread_id` has one. Main window only; the
/// folder comes from the host's project list and the thread's binding, never from the renderer.
#[tauri::command]
pub fn preview_grant(window: tauri::WebviewWindow, host: tauri::State<'_, crate::bridge::Host>, grants: tauri::State<'_, Grants>, project_id: String, thread_id: Option<String>) -> Result<Value, String> {
    if window.label() != "main" { return Err("Only the main window can open a preview.".into()); }
    let projects = host.call("projects", json!({}))?;
    let folder = project_folder(&projects, &project_id).ok_or("That project isn't open in Dervo.")?;
    // A binding that can't be read is an error the pane shows, never a quiet switch to the project's copy. Only a thread
    // whose own folder is gone (merged, archived) previews the project folder.
    let folder = match thread_id {
        Some(id) => thread_folder(&host.call("binding", json!({"projectId": project_id, "channelId": id}))?).unwrap_or(folder),
        None => folder,
    };
    let grant = grants.grant(&folder)?;
    Ok(json!({"grant": grant, "base": format!("{SCHEME}://{grant}/")}))
}

/// Open what Preview shows in your own browser: a file of the granted project, checked by the same rules, or a local
/// page, checked by `local_page`. macOS `open` picks the app; nothing else is run.
#[tauri::command]
pub fn preview_open_outside(window: tauri::WebviewWindow, grants: tauri::State<'_, Grants>, grant: Option<String>, path: Option<String>, url: Option<String>) -> Result<(), String> {
    if window.label() != "main" { return Err("Only the main window can open a preview.".into()); }
    let target = match (grant, path, url) {
        (Some(grant), Some(path), None) => {
            let root = grants.root(&grant.to_ascii_lowercase()).ok_or("That preview has closed.")?;
            resolve(&root, &path).map_err(|_| "Dervo can't open that file.".to_string())?.to_string_lossy().into_owned()
        }
        (None, None, Some(url)) => local_page(&url).ok_or("Only a page on localhost or 127.0.0.1 with a port opens from here.")?,
        _ => return Err("Nothing to open.".into()),
    };
    open_outside(&target)
}

/// Opens `target`, a file path or a URL, in whatever the system uses for it. On a Mac that's `open` and its exit code.
/// On Windows it's explorer.exe, whose exit code means nothing (it's 1 when it opened), so a started process is the
/// answer. The target goes to explorer whole and quoted, so a comma in a file name isn't read as one of its switches,
/// and a target with a quote in it, which no Windows file name has, is refused rather than let out of the quotes.
pub fn open_outside(target: &str) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        if target.contains('"') { return Err("Windows can't open that.".into()); }
        return std::process::Command::new("explorer.exe").raw_arg(format!("\"{target}\"")).spawn().map(|_| ())
            .map_err(|_| "Windows couldn't open it.".to_string());
    }
    // Linux: xdg-open, the desktop's own opener, and its exit code, as on a Mac. A target starting with '-' is refused,
    // since xdg-open has no "--".
    #[cfg(target_os = "linux")]
    {
        if target.starts_with('-') { return Err("Linux can't open that.".into()); }
        return std::process::Command::new("/usr/bin/xdg-open").arg(target).status().map_err(|e| e.to_string())
            .and_then(|status| if status.success() { Ok(()) } else { Err("Your desktop couldn't open it.".into()) });
    }
    #[cfg(not(any(windows, target_os = "linux")))]
    std::process::Command::new("/usr/bin/open").arg("--").arg(target).status().map_err(|e| e.to_string())
        .and_then(|status| if status.success() { Ok(()) } else { Err("macOS couldn't open it.".into()) })
}

/// Whether an invoke came from Dervo's own page (security review M4). Tauri 2.11.5 counts any scheme the app registers
/// as local (`Webview::is_local_url`), so a `dervo-preview:` frame passes the capability ACL like the main page does,
/// and an app manifest can't tell them apart. What the command side can see is the request's `Origin` header on the
/// `ipc://` fetch path, which WebKit sets and a page can't forge. When there is one, it must be the main page's own
/// origin (`page`, the webview's top-level URL): a preview frame, a localhost frame or anything else is refused. An
/// opaque origin (`null`) is refused. The `postMessage` fallback carries no trustworthy origin at all, so a missing
/// header is let through here and the per-launch invoke key, injected into the main frame only, is what stops a frame
/// there (notes/PLAN-panels.md, "Preview security").
pub fn own_origin(origin: Option<&str>, page: Option<&tauri::Url>) -> bool {
    let Some(origin) = origin else { return true };
    let (Ok(origin), Some(page)) = (tauri::Url::parse(origin), page) else { return false };
    origin.scheme() != SCHEME && origin.scheme() == page.scheme() && origin.host_str() == page.host_str()
        && origin.port_or_known_default() == page.port_or_known_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test] fn a_file_is_read_once_through_one_handle_and_never_past_the_cap() {
        let (base, root) = project();
        let page = root.join("evidence/mock/index.html");
        assert_eq!(read_bounded(&page).as_deref(), Some(&b"<h1>Mockup</h1>"[..]));
        // A link swapped in after resolve() isn't followed.
        let swapped = root.join("evidence/mock/swapped.html");
        symlink(base.join("outside.html"), &swapped).unwrap();
        assert_eq!(read_bounded(&swapped), None);
        assert_eq!(read_bounded(&root.join("evidence/mock")), None, "a folder");
        // Past the cap: refused on the handle's own size (a sparse file, so nothing big is written).
        let big = root.join("evidence/mock/big.html");
        std::fs::File::create(&big).unwrap().set_len(MAX_BYTES + 1).unwrap();
        assert_eq!(read_bounded(&big), None);
        // Negative control: exactly at the cap is read.
        std::fs::File::create(&big).unwrap().set_len(MAX_BYTES).unwrap();
        assert_eq!(read_bounded(&big).map(|b| b.len() as u64), Some(MAX_BYTES));
        std::fs::remove_dir_all(&base).ok();
    }

    #[test] fn only_the_main_pages_own_origin_may_invoke() {
        let app = tauri::Url::parse("tauri://localhost/index.html").unwrap();
        let dev = tauri::Url::parse("http://localhost:5173/").unwrap();
        assert!(own_origin(Some("tauri://localhost"), Some(&app)));
        assert!(own_origin(Some("http://localhost:5173"), Some(&dev)));
        for origin in ["dervo-preview://0123456789abcdef0123456789abcdef", "null", "http://localhost:3000", "http://127.0.0.1:5173",
                       "tauri://evil", "https://localhost", "not a url", ""] {
            assert!(!own_origin(Some(origin), Some(&app)), "{origin}");
            assert!(!own_origin(Some(origin), Some(&dev)) || origin == "http://localhost:5173", "{origin}");
        }
        assert!(!own_origin(Some("tauri://localhost"), None), "no page to compare with");
        // A preview page is never its own reference, even if the webview somehow sits on the preview scheme.
        let preview = tauri::Url::parse("dervo-preview://0123456789abcdef0123456789abcdef/page.html").unwrap();
        assert!(!own_origin(Some("dervo-preview://0123456789abcdef0123456789abcdef"), Some(&preview)));
        // Negative control: no Origin header (the postMessage path) can't be judged here and is left to the invoke key.
        assert!(own_origin(None, Some(&app)));
    }

    fn project() -> (PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("dervo-preview-test-{}", random_id().unwrap()));
        let root = base.join("project");
        std::fs::create_dir_all(root.join("evidence/mock")).unwrap();
        std::fs::create_dir_all(root.join(".git")).unwrap();
        std::fs::write(root.join("evidence/mock/index.html"), "<h1>Mockup</h1>").unwrap();
        std::fs::write(root.join("evidence/mock/shot.png"), [0x89, b'P', b'N', b'G']).unwrap();
        std::fs::write(root.join(".env"), "SECRET=1").unwrap();
        std::fs::write(root.join(".git/config"), "[core]").unwrap();
        std::fs::write(root.join("notes.txt"), "plain").unwrap();
        std::fs::write(root.join("data.json"), "{}").unwrap();
        std::fs::write(base.join("outside.html"), "<h1>Outside</h1>").unwrap();
        (base.canonicalize().unwrap(), root.canonicalize().unwrap())
    }

    #[test] fn a_file_in_the_project_is_served_and_anything_else_is_refused() {
        let (base, root) = project();
        assert_eq!(resolve(&root, "/evidence/mock/index.html").unwrap(), root.join("evidence/mock/index.html"));
        assert_eq!(resolve(&root, "/evidence/mock/shot.png").unwrap(), root.join("evidence/mock/shot.png"));
        assert_eq!(resolve(&root, "/evidence/mock/index.html?v=3").unwrap(), root.join("evidence/mock/index.html"));
        // Negative controls: traversal, encoded traversal, dotfiles, a directory, a type that isn't viewable.
        assert_eq!(resolve(&root, "/../outside.html"), Err(Refused::Hidden));
        assert_eq!(resolve(&root, "/evidence/%2e%2e/%2e%2e/../outside.html"), Err(Refused::Hidden));
        assert_eq!(resolve(&root, "/%2E%2E/outside.html"), Err(Refused::Hidden));
        assert_eq!(resolve(&root, "/.env"), Err(Refused::Hidden));
        assert_eq!(resolve(&root, "/.git/config"), Err(Refused::Hidden));
        assert_eq!(resolve(&root, "/evidence/./mock/index.html"), Err(Refused::Hidden));
        assert_eq!(resolve(&root, "/evidence/mock"), Err(Refused::NotAFile));
        assert_eq!(resolve(&root, "/"), Err(Refused::NotAFile));
        assert_eq!(resolve(&root, "/notes.txt"), Err(Refused::Kind));
        assert_eq!(resolve(&root, "/data.json"), Err(Refused::Kind));
        assert_eq!(content_type(Path::new("report.PDF")), Some("application/pdf"));
        assert_eq!(content_type(Path::new("notes.md")), None, "Markdown is read by the host and rendered without HTML, never framed");
        assert_eq!(resolve(&root, "/evidence%5cmock%5cindex.html"), Err(Refused::Encoding));
        assert_eq!(resolve(&root, "/index.html%00.png"), Err(Refused::Encoding));
        assert_eq!(resolve(&root, "/missing.html"), Err(Refused::Missing));
        let _ = std::fs::remove_dir_all(base);
    }

    #[test] fn a_symlink_is_followed_only_while_it_stays_inside_the_project() {
        let (base, root) = project();
        symlink(base.join("outside.html"), root.join("escape.html")).unwrap();
        symlink(&base, root.join("up")).unwrap();
        symlink(root.join(".env"), root.join("env.html")).unwrap();
        symlink(root.join("evidence/mock/index.html"), root.join("alias.html")).unwrap();
        assert_eq!(resolve(&root, "/alias.html").unwrap(), root.join("evidence/mock/index.html"), "a link inside the project works");
        // Negative controls: a link to a file outside, a link to a folder outside, a link that lands on .env.
        assert_eq!(resolve(&root, "/escape.html"), Err(Refused::Outside));
        assert_eq!(resolve(&root, "/up/outside.html"), Err(Refused::Outside));
        assert_eq!(resolve(&root, "/env.html"), Err(Refused::Hidden));
        let _ = std::fs::remove_dir_all(base);
    }

    #[test] fn only_the_main_webview_gets_a_file_and_only_by_get_with_the_page_policy() {
        let (base, root) = project();
        let grants = Grants::default();
        let grant = grants.grant(&root).unwrap();
        assert_eq!(grants.grant(&root).unwrap(), grant, "the same folder keeps its grant");
        let get = |uri: &str| Request::builder().uri(uri).body(Vec::new()).unwrap();
        let ok = respond(&grants, "main", &get(&format!("{SCHEME}://{grant}/evidence/mock/index.html")));
        assert_eq!(ok.status(), StatusCode::OK);
        assert_eq!(ok.body().as_ref(), b"<h1>Mockup</h1>");
        let csp = ok.headers()["Content-Security-Policy"].to_str().unwrap();
        assert!(csp.contains("sandbox allow-scripts") && csp.contains("connect-src 'none'") && csp.contains("default-src 'none'"));
        assert!(!csp.contains("allow-same-origin"));
        assert_eq!(ok.headers()["X-Content-Type-Options"], "nosniff");
        // Negative controls: another webview, a POST, an unknown grant, a dotfile, traversal.
        assert_eq!(respond(&grants, "preview", &get(&format!("{SCHEME}://{grant}/evidence/mock/index.html"))).status(), StatusCode::FORBIDDEN);
        let post = Request::builder().method("POST").uri(format!("{SCHEME}://{grant}/evidence/mock/index.html")).body(Vec::new()).unwrap();
        assert_eq!(respond(&grants, "main", &post).status(), StatusCode::METHOD_NOT_ALLOWED);
        assert_eq!(respond(&grants, "main", &get(&format!("{SCHEME}://{}/evidence/mock/index.html", "0".repeat(32)))).status(), StatusCode::NOT_FOUND);
        assert_eq!(respond(&grants, "main", &get(&format!("{SCHEME}://{grant}/.env"))).status(), StatusCode::NOT_FOUND);
        assert_eq!(respond(&grants, "main", &get(&format!("{SCHEME}://{grant}/%2e%2e/outside.html"))).status(), StatusCode::NOT_FOUND);
        let _ = std::fs::remove_dir_all(base);
    }

    #[test] fn a_grant_is_never_the_whole_disk_or_the_home_folder() {
        assert!(allowed_root(Path::new("/")).is_err());
        if let Some(home) = std::env::var_os("HOME") { assert!(allowed_root(Path::new(&home)).is_err()); }
        assert!(allowed_root(Path::new("/definitely/not/here")).is_err());
        let (base, root) = project();
        assert!(allowed_root(&root).is_ok(), "negative control: a real project folder is allowed");
        let _ = std::fs::remove_dir_all(base);
    }

    #[test] fn the_project_folder_comes_from_the_hosts_list_by_id() {
        let list = json!([{"id": "p1", "cwd": "~/dervo/app"}, {"id": "p2", "cwd": "/tmp/x"}]);
        assert_eq!(project_folder(&list, "p1"), Some(PathBuf::from("~/dervo/app")));
        assert_eq!(project_folder(&list, "p3"), None);
        assert_eq!(project_folder(&json!({"id": "p1"}), "p1"), None);
        let here = std::env::temp_dir();
        assert_eq!(thread_folder(&json!({"cwd": here.to_string_lossy()})), Some(here.clone()));
        for binding in [json!({}), json!({"cwd": "relative/folder"}), json!({"cwd": here.join("dervo-no-such-thread-folder").to_string_lossy()}), json!({"cwd": 7})] {
            assert_eq!(thread_folder(&binding), None, "{binding}");
        }
    }

    #[test] fn a_local_page_is_localhost_or_127_0_0_1_with_a_port_and_nothing_else() {
        assert_eq!(local_page("http://localhost:5173").as_deref(), Some("http://localhost:5173/"));
        assert_eq!(local_page("http://127.0.0.1:8818/bugs?x=1").as_deref(), Some("http://127.0.0.1:8818/bugs?x=1"));
        // Negative controls: remote, lookalikes, no port, a user part, other schemes, bad ports.
        for url in ["http://example.com:80/", "https://localhost:5173/", "http://localhost/", "http://localhost:/", "http://localhost.evil.com:80/",
            "http://evil.com#@localhost:80/", "http://user@localhost:5173/", "http://localhost:5173@evil.com/", "http://0.0.0.0:3000/",
            "http://[::1]:3000/", "http://localhost:0/", "http://localhost:70000/", "file:///etc/passwd", "javascript:alert(1)",
            "http://127.0.0.2:3000/", "http://localhost:5173/a b", "dervo-preview://abc/index.html", "http://LOCALHOST:3000/"] {
            assert_eq!(local_page(url), None, "{url}");
        }
    }

    #[test] fn the_app_policy_frames_only_previews_and_local_pages() {
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let csp = config["app"]["security"]["csp"].as_str().unwrap();
        let frame = csp.split(';').map(str::trim).find(|d| d.starts_with("frame-src")).expect("frame-src is set");
        assert_eq!(frame, "frame-src dervo-preview: http://localhost:* http://127.0.0.1:*");
        // Negative controls: the app's own pages still load only its own scripts, and nothing remote can be framed.
        assert!(csp.contains("default-src 'self'") && csp.contains("script-src 'self' 'wasm-unsafe-eval';"));
        assert!(!frame.contains("https:") && !frame.contains(" *") && !frame.contains("'self'"));
        assert!(csp.contains("img-src 'self' data: blob: dervo-preview:"));
        // The capability still names only the main window, so a preview frame is granted nothing.
        let capability: Value = serde_json::from_str(include_str!("../capabilities/main.json")).unwrap();
        assert_eq!(capability["windows"], json!(["main"]));
        assert!(capability.get("remote").is_none());
    }
}
