//! Launch-time resolution of where Dervo stores data and which Python runs the local host.
//!
//! Nothing here reads, migrates or lists data. It validates paths, discovers the
//! interpreter and builds the host command. The backend receives exactly two
//! arguments: the host script and the selected data directory. The data directory
//! is created by the Python store with owner-only permissions, never here.
//!
//! Overrides
//!   DERVO_DATA_DIR: absolute directory for disposable verification. Relative
//!     paths, `..` components, symlinks and non-directories are rejected. When
//!     unset, the Tauri application data directory is used.
//!   DERVO_PYTHON: absolute path to the interpreter for source runs (cargo test,
//!     tauri dev). Missing, empty, relative, non-executable or directory values
//!     fail without any PATH fallback. When unset, a source run uses the Python 3.13
//!     framework required by checks. A built app ignores it: it runs only the Python
//!     3.13 it carries in Contents/Resources/runtime/python (runtime/python.json,
//!     staged by scripts/python_runtime.py). The generic PATH resolver remains
//!     available for explicit development callers.
//!
//! The backend sandbox (backend/task_evidence.py) accepts only the same interpreter:
//! the app's own copy in a built app, the framework in a source checkout. Selecting a
//! different python3 here doesn't relax that; the host reports confinement as blocked.
use std::ffi::OsStr;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

pub const DATA_DIR_VARIABLE: &str = "DERVO_DATA_DIR";
pub const PYTHON_VARIABLE: &str = "DERVO_PYTHON";
const PYTHON_NAME: &str = "python3";

/// Chooses the data directory. `default_dir` is the application data directory
/// from Tauri, passed in so tests can substitute it.
pub fn resolve_data_dir(override_value: Option<&OsStr>, default_dir: Result<PathBuf, String>) -> Result<PathBuf, String> {
    let value = match override_value {
        None => {
            return default_dir.map_err(|error| format!(
                "The application data directory couldn't be determined: {error}. Set {DATA_DIR_VARIABLE} to an absolute directory for verification."));
        }
        Some(value) if value.is_empty() => {
            return Err(format!("{DATA_DIR_VARIABLE} is set but empty. Unset it to use the application data directory."));
        }
        Some(value) => value,
    };
    let path = PathBuf::from(value);
    if !path.is_absolute() {
        return Err(format!("{DATA_DIR_VARIABLE} must be an absolute path, got {}", path.display()));
    }
    if path.components().any(|component| matches!(component, Component::ParentDir)) {
        return Err(format!("{DATA_DIR_VARIABLE} must not contain .. components, got {}", path.display()));
    }
    match std::fs::symlink_metadata(&path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(format!(
            "{DATA_DIR_VARIABLE}={} is a symlink. Point it at the real directory.", path.display())),
        Ok(metadata) if !metadata.is_dir() => Err(format!(
            "{DATA_DIR_VARIABLE}={} exists but is not a directory.", path.display())),
        Ok(_) => Ok(path),
        // The host creates a missing directory itself with owner-only permissions.
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(path),
        Err(error) => Err(format!("{DATA_DIR_VARIABLE}={} couldn't be inspected: {error}", path.display())),
    }
}

/// Chooses the interpreter for the local host. `path_value` is the PATH variable.
pub fn resolve_python(override_value: Option<&OsStr>, path_value: Option<&OsStr>) -> Result<PathBuf, String> {
    if let Some(value) = override_value {
        if value.is_empty() {
            return Err(format!("{PYTHON_VARIABLE} is set but empty. Point it at a python3 executable or unset it."));
        }
        let path = PathBuf::from(value);
        if !path.is_absolute() {
            return Err(format!("{PYTHON_VARIABLE} must be an absolute path, got {}", path.display()));
        }
        return match executable_problem(&path) {
            None => Ok(path),
            Some(problem) => Err(format!(
                "{PYTHON_VARIABLE}={} {problem}. No PATH fallback is used for an explicit override.", path.display())),
        };
    }
    let mut searched = 0;
    for directory in path_value.map(|value| std::env::split_paths(value)).into_iter().flatten() {
        if directory.as_os_str().is_empty() || !directory.is_absolute() {
            continue; // empty and relative PATH entries are skipped deliberately
        }
        searched += 1;
        let candidate = directory.join(PYTHON_NAME);
        if executable_problem(&candidate).is_none() {
            return Ok(candidate);
        }
    }
    Err(format!("{PYTHON_NAME} was not found in {searched} absolute PATH directories. Set {PYTHON_VARIABLE} to an explicit interpreter."))
}

/// Finder launches don't inherit a shell PATH. Use the same framework as coding checks.
#[cfg(not(any(windows, target_os = "linux")))]
pub fn resolve_app_python(override_value: Option<&OsStr>) -> Result<PathBuf,String> {
    resolve_mac_python(override_value, &own_executable()?, Path::new(FRAMEWORK_PYTHON))
}
/// Windows has no Python framework: Dervo carries its own Python 3.13 next to its exe, in windows-python/ (staged by
/// .github/workflows/windows.yml, b16/windows).
#[cfg(windows)]
pub fn resolve_app_python(override_value: Option<&OsStr>) -> Result<PathBuf,String> {
    let exe = std::env::current_exe().map_err(|e| format!("Dervo couldn't find its own folder: {e}"))?;
    let folder = exe.parent().ok_or("Dervo couldn't find its own folder")?;
    resolve_framework_python(override_value, &folder.join("windows-python").join("python.exe"))
}
/// Linux packages (deb, rpm, AppImage) carry Dervo's own Python 3.13 in linux-python/ among the app's resources
/// (runtime/python-linux-x64.json, staged by scripts/stage-linux-python.py). Tauri puts those in /usr/lib/<product> for
/// a deb or rpm and under the AppImage's mount, not beside the exe, so main.rs records the resource folder before the
/// host starts. A source run (cargo test, tauri dev) has no packaged copy: DERVO_PYTHON, or python3 on PATH.
#[cfg(target_os = "linux")]
pub fn resolve_app_python(override_value: Option<&OsStr>) -> Result<PathBuf,String> {
    if override_value.is_some() { return resolve_python(override_value, None) }
    match RESOURCES.get() {
        Some(resources) if resources.join("linux-python").is_dir() => {
            let bundled = linux_bundled_python(resources);
            match executable_problem(&bundled) {
                None => Ok(bundled),
                Some(problem) => Err(format!(
                    "Dervo's own Python at {} {problem}. This copy of Dervo is incomplete: install it again.", bundled.display())),
            }
        }
        _ => resolve_python(None, std::env::var_os("PATH").as_deref()),
    }
}
/// Where the app's resources are, recorded once at startup (main.rs) for resolve_app_python on Linux.
#[cfg(target_os = "linux")]
pub static RESOURCES: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
#[cfg(target_os = "linux")]
pub fn linux_bundled_python(resources: &Path) -> PathBuf { resources.join("linux-python").join("bin").join("python3.13") }
#[cfg_attr(target_os = "linux", allow(dead_code))]
fn resolve_framework_python(override_value: Option<&OsStr>, framework: &Path) -> Result<PathBuf,String> {
    if override_value.is_some() {return resolve_python(override_value,None)}
    match executable_problem(framework) {
        None=>Ok(framework.to_path_buf()),
        Some(problem)=>Err(format!("Python 3.13 framework at {} {problem}.",framework.display())),
    }
}

/// The python.org framework that source runs and tests use, and that every Mac app up to beta.16.4 required.
#[cfg_attr(any(windows, target_os = "linux"), allow(dead_code))]
const FRAMEWORK_PYTHON: &str = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3";
/// Where a built app carries its own Python 3.13, relative to Contents (runtime/python.json, scripts/python_runtime.py).
pub const BUNDLED_PYTHON: &str = "Resources/runtime/python/bin/python3.13";

/// This executable with links resolved, so a symlink to the app's binary still finds the app around it.
fn own_executable() -> Result<PathBuf,String> {
    let exe = std::env::current_exe().map_err(|e| format!("Dervo couldn't find its own folder: {e}"))?;
    Ok(std::fs::canonicalize(&exe).unwrap_or(exe))
}

/// `<name>.app/Contents` when `executable` is `<name>.app/Contents/MacOS/<binary>`, otherwise None (a source run).
pub fn app_contents(executable: &Path) -> Option<PathBuf> {
    let macos = executable.parent()?;
    let contents = macos.parent()?;
    let app = contents.parent()?;
    (macos.file_name()? == "MacOS" && contents.file_name()? == "Contents" && app.extension()? == "app")
        .then(|| contents.to_path_buf())
}

/// The app's own Python, given its Contents folder.
pub fn bundled_python(contents: &Path) -> PathBuf { contents.join(BUNDLED_PYTHON) }

/// A built app runs only the Python it carries, whatever DERVO_PYTHON says. Older versions hand their own interpreter
/// to the version they open (16.4's release_switch.py and redirect_selected pass the framework in DERVO_PYTHON), and the
/// keychain bridge and check confinement accept only the app's own copy. A missing or broken copy is an error naming
/// it, never the framework or PATH. Outside an app bundle (cargo test, tauri dev) it's DERVO_PYTHON or the framework.
#[cfg_attr(any(windows, target_os = "linux"), allow(dead_code))]
pub(crate) fn resolve_mac_python(override_value: Option<&OsStr>, executable: &Path, framework: &Path) -> Result<PathBuf,String> {
    let Some(contents) = app_contents(executable) else { return resolve_framework_python(override_value, framework) };
    let bundled = bundled_python(&contents);
    match executable_problem(&bundled) {
        None => Ok(bundled),
        Some(problem) => Err(format!(
            "Dervo's own Python at {} {problem}. This copy of Dervo is incomplete: download it again and replace it.",
            bundled.display())),
    }
}

/// The startup kind for a failed resolve_app_python (src/startup.rs, StartupGate.tsx). "configuration" is a source
/// run's bad DERVO_PYTHON. "runtime" is a missing or broken Python of Dervo's own, or a source checkout without the
/// framework. Since beta.17 it's never "python": nothing asks a tester to install Python.
pub fn python_failure_kind(override_value: Option<&OsStr>) -> &'static str {
    let built = own_executable().ok().and_then(|exe| app_contents(&exe)).is_some();
    if override_value.is_some() && !built { "configuration" } else { "runtime" }
}

/// Symlinks are accepted when the final target is an executable regular file.
fn executable_problem(path: &Path) -> Option<String> {
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    match std::fs::metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Some(
            if std::fs::symlink_metadata(path).is_ok() { "is a dangling symlink" } else { "does not exist" }.to_string()),
        Err(error) => Some(format!("couldn't be inspected: {error}")),
        Ok(metadata) if metadata.is_dir() => Some("is a directory, not an executable file".to_string()),
        Ok(metadata) if !metadata.is_file() => Some("is not a regular file".to_string()),
        // Windows has no execute bit: an existing regular file is as far as this check goes there (b16/seam).
        #[cfg(unix)]
        Ok(metadata) if metadata.permissions().mode() & 0o111 == 0 => Some("is not executable".to_string()),
        Ok(_) => None,
    }
}

/// The exact host invocation: isolated interpreter flags, the script, and only the
/// selected data directory. The environment is cleared so nothing leaks to the host.
pub fn host_command(python: &Path, script: &Path, data: &Path) -> Command {
    let mut command = Command::new(python);
    command.args(["-E", "-s", "-B"]).arg(script).arg(data)
        .env_clear().stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    host_environment(&mut command);
    command
}

/// The folders Python needs on Windows to find itself, the home folder and temp. None of them is a secret, and nothing
/// else from Dervo's own environment reaches the host (scripts/windows-host-smoke.py uses the same list).
#[cfg(target_os = "linux")]
const LINUX_ENVIRONMENT: [&str; 10] = ["HOME", "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_CACHE_HOME", "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_CURRENT_DESKTOP", "TMPDIR"];
/// The host's PATH on Linux: the system folders only, never the user's shell PATH. xdg-open (platform_os.open_command)
/// is a script that finds the desktop's opener there.
#[cfg(target_os = "linux")]
const LINUX_PATH: &str = "/usr/local/bin:/usr/bin:/bin";
#[cfg(windows)]
const WINDOWS_ENVIRONMENT: [&str; 6] = ["SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"];

/// The AppImage this Dervo runs from, or None. $APPIMAGE counts only when this executable is inside $APPDIR, the
/// AppImage's own mount: a .deb Dervo opened from another AppImage (a terminal or launcher packaged as one) inherits
/// that one's variables, and an update would otherwise replace the other program's file.
#[cfg(target_os = "linux")]
pub fn own_appimage() -> Option<PathBuf> {
    let image = PathBuf::from(std::env::var_os("APPIMAGE")?);
    let mount = std::fs::canonicalize(std::env::var_os("APPDIR")?).ok()?;
    let exe = std::env::current_exe().ok().and_then(|exe| std::fs::canonicalize(exe).ok())?;
    appimage_owns(&exe, &mount).then_some(image)
}

/// Whether `exe` is inside the AppImage mount `mount` (both already canonical).
pub fn appimage_owns(exe: &Path, mount: &Path) -> bool { mount.parent().is_some() && exe.starts_with(mount) }

/// What a Python child of the app gets after `env_clear` besides its own arguments. A Mac adds nothing: the host finds
/// its home in the account database. Windows adds WINDOWS_ENVIRONMENT and the app's own exe in DERVO_APP_EXECUTABLE
/// (the installed exe's name comes from the installer, and update signatures are checked by running it:
/// platform_os.app_executable), and starts it without a console window, which otherwise flashes up on every launch.
pub fn host_environment(command: &mut Command) -> &mut Command {
    // Linux: the desktop session's own variables, which the Secret Service (linux_credentials.py) and xdg-open need to
    // reach the session bus and the display, a fixed PATH, and the app's exe for update checks as on Windows. None is a
    // secret; nothing else from Dervo's environment reaches the host.
    #[cfg(target_os = "linux")]
    {
        for name in LINUX_ENVIRONMENT {
            if let Some(value) = std::env::var_os(name) { command.env(name, value); }
        }
        command.env("PATH", LINUX_PATH);
        // An AppImage runs from a temporary mount; $APPIMAGE is the file the user actually has.
        let image = own_appimage();
        let exe = image.clone().or_else(|| std::env::current_exe().ok());
        if let Some(exe) = exe { command.env("DERVO_APP_EXECUTABLE", exe); }
        // So the host asks for AppImage updates (backend/beta_updates.py linux_format). Never passed on from elsewhere:
        // the host's environment is cleared, so a .deb Dervo started from another AppImage asks for .deb updates.
        if let Some(image) = image { command.env("APPIMAGE", image); }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        for name in WINDOWS_ENVIRONMENT {
            if let Some(value) = std::env::var_os(name) { command.env(name, value); }
        }
        if let Ok(exe) = std::env::current_exe() { command.env("DERVO_APP_EXECUTABLE", exe); }
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn an_appimage_counts_only_for_an_executable_inside_its_own_mount() {
        let mount = Path::new("/tmp/.mount_DervoAb12");
        assert!(appimage_owns(&mount.join("usr/bin/dervo-local"), mount));
        assert!(!appimage_owns(Path::new("/usr/bin/dervo-local"), mount), "a .deb opened from another AppImage");
        assert!(!appimage_owns(Path::new("/tmp/.mount_DervoAb12x/usr/bin/dervo-local"), mount));
        assert!(!appimage_owns(Path::new("/usr/bin/dervo-local"), Path::new("/")));
    }

    struct Scratch(PathBuf);
    impl Scratch {
        fn new(name: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("dervo launch test {} {name}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            Scratch(dir)
        }
        fn path(&self, relative: &str) -> PathBuf { self.0.join(relative) }
        fn executable(&self, relative: &str, mode: u32) -> PathBuf {
            let path = self.path(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, "#!/bin/sh\nexit 0\n").unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
            path
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    fn os(value: &str) -> &OsStr { OsStr::new(value) }
    fn default_ok() -> Result<PathBuf, String> { Ok(PathBuf::from("/fixture/Application Support/dervo")) }

    #[test] fn default_data_dir_is_used_when_no_override() {
        assert_eq!(resolve_data_dir(None, default_ok()).unwrap(), PathBuf::from("/fixture/Application Support/dervo"));
    }
    #[test] fn default_data_dir_failure_names_the_override() {
        let error = resolve_data_dir(None, Err("no home".into())).unwrap_err();
        assert!(error.contains("no home") && error.contains(DATA_DIR_VARIABLE), "{error}");
    }
    #[test] fn empty_override_is_rejected_not_ignored() {
        let error = resolve_data_dir(Some(os("")), default_ok()).unwrap_err();
        assert!(error.contains("empty") && error.contains(DATA_DIR_VARIABLE), "{error}");
    }
    #[test] fn relative_override_is_rejected() {
        let error = resolve_data_dir(Some(os("relative/data dir")), default_ok()).unwrap_err();
        assert!(error.contains("absolute"), "{error}");
    }
    #[test] fn parent_components_are_rejected() {
        let error = resolve_data_dir(Some(os("/tmp/../etc/dervo")), default_ok()).unwrap_err();
        assert!(error.contains(".."), "{error}");
    }
    #[test] fn missing_absolute_override_is_accepted_for_the_host_to_create() {
        let scratch = Scratch::new("missing");
        let wanted = scratch.path("not yet created");
        assert_eq!(resolve_data_dir(Some(wanted.as_os_str()), Err("unused".into())).unwrap(), wanted);
        assert!(!wanted.exists(), "resolution must not create the directory");
    }
    #[test] fn existing_directory_with_spaces_is_accepted() {
        let scratch = Scratch::new("existing");
        let wanted = scratch.path("data with spaces");
        fs::create_dir(&wanted).unwrap();
        assert_eq!(resolve_data_dir(Some(wanted.as_os_str()), Err("unused".into())).unwrap(), wanted);
    }
    #[test] fn file_and_symlink_overrides_are_rejected() {
        let scratch = Scratch::new("kinds");
        let file = scratch.path("a file");
        fs::write(&file, "x").unwrap();
        assert!(resolve_data_dir(Some(file.as_os_str()), default_ok()).unwrap_err().contains("not a directory"));
        let real = scratch.path("real");
        fs::create_dir(&real).unwrap();
        let link = scratch.path("link");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        assert!(resolve_data_dir(Some(link.as_os_str()), default_ok()).unwrap_err().contains("symlink"));
    }

    #[test] fn python_override_is_used_without_consulting_path() {
        let scratch = Scratch::new("python override");
        let chosen = scratch.executable("chosen bin/python3", 0o755);
        let other = scratch.executable("path bin/python3", 0o755);
        let path = other.parent().unwrap().as_os_str();
        assert_eq!(resolve_python(Some(chosen.as_os_str()), Some(path)).unwrap(), chosen);
    }
    #[test] fn invalid_python_override_fails_without_fallback() {
        let scratch = Scratch::new("python invalid");
        let on_path = scratch.executable("path bin/python3", 0o755);
        let path = on_path.parent().unwrap().as_os_str();
        let missing = scratch.path("absent/python3");
        let error = resolve_python(Some(missing.as_os_str()), Some(path)).unwrap_err();
        assert!(error.contains(PYTHON_VARIABLE) && error.contains("does not exist") && error.contains("fallback"), "{error}");
        let plain = scratch.executable("plain/python3", 0o644);
        assert!(resolve_python(Some(plain.as_os_str()), Some(path)).unwrap_err().contains("not executable"));
        let directory = scratch.path("path bin");
        assert!(resolve_python(Some(directory.as_os_str()), Some(path)).unwrap_err().contains("directory"));
        assert!(resolve_python(Some(os("")), Some(path)).unwrap_err().contains("empty"));
        assert!(resolve_python(Some(os("bin/python3")), Some(path)).unwrap_err().contains("absolute"));
        let dangling = scratch.path("dangling");
        std::os::unix::fs::symlink(scratch.path("gone"), &dangling).unwrap();
        assert!(resolve_python(Some(dangling.as_os_str()), Some(path)).unwrap_err().contains("dangling symlink"));
    }
    #[test] fn symlinked_python_override_is_accepted() {
        let scratch = Scratch::new("python symlink");
        let real = scratch.executable("real bin/python3", 0o755);
        let link = scratch.path("link to python3");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        assert_eq!(resolve_python(Some(link.as_os_str()), None).unwrap(), link);
    }
    #[test] fn path_search_takes_first_executable_and_skips_relative_empty_and_plain_entries() {
        let scratch = Scratch::new("python path");
        let plain = scratch.executable("plain bin/python3", 0o644);
        let first = scratch.executable("first bin/python3", 0o755);
        let second = scratch.executable("second bin/python3", 0o755);
        let path = std::env::join_paths([
            PathBuf::from(""), PathBuf::from("relative/bin"),
            plain.parent().unwrap().to_path_buf(), first.parent().unwrap().to_path_buf(), second.parent().unwrap().to_path_buf(),
        ]).unwrap();
        assert_eq!(resolve_python(None, Some(path.as_os_str())).unwrap(), first);
    }
    #[test] fn empty_or_missing_path_reports_the_override_name() {
        for value in [None, Some(os("")), Some(os("relative/only"))] {
            let error = resolve_python(None, value).unwrap_err();
            assert!(error.contains(PYTHON_VARIABLE) && error.contains("0 absolute"), "{error}");
        }
    }

    #[test] fn finder_uses_framework_without_path_and_override_never_falls_back() {
        let scratch=Scratch::new("finder runtime");
        let framework=scratch.executable("Python.framework/bin/python3",0o755);
        assert_eq!(resolve_framework_python(None,&framework).unwrap(),framework);
        let missing=scratch.path("missing/python3");
        assert!(resolve_framework_python(None,&missing).is_err());
        assert!(resolve_framework_python(Some(missing.as_os_str()),&framework).is_err());
        let chosen=scratch.executable("explicit/python3",0o755);
        assert_eq!(resolve_framework_python(Some(chosen.as_os_str()),&missing).unwrap(),chosen);
    }

    /// A built app's layout: Contents/MacOS/dervo-local and, when `python` is set, its own Python with that mode.
    fn app(scratch: &Scratch, python: Option<u32>) -> PathBuf {
        let exe = scratch.executable("Dervo Local.app/Contents/MacOS/dervo-local", 0o755);
        if let Some(mode) = python { scratch.executable(&format!("Dervo Local.app/Contents/{BUNDLED_PYTHON}"), mode); }
        exe
    }
    #[test] fn app_contents_needs_the_whole_bundle_shape() {
        assert_eq!(app_contents(Path::new("/Applications/Dervo Local.app/Contents/MacOS/dervo-local")),
            Some(PathBuf::from("/Applications/Dervo Local.app/Contents")));
        for source_run in ["/repo/target/debug/dervo-local", "/repo/target/debug/deps/dervo_local-0123",
                           "/x/Dervo Local/Contents/MacOS/dervo-local", "/x/Dervo.app/Contents/Other/dervo-local",
                           "/x/Dervo.app/Other/MacOS/dervo-local", "dervo-local", "/"] {
            assert_eq!(app_contents(Path::new(source_run)), None, "{source_run}");
        }
    }
    #[test] fn built_app_runs_only_its_own_python_whatever_the_override_says() {
        let scratch = Scratch::new("bundled runtime");
        let exe = app(&scratch, Some(0o755));
        let framework = scratch.executable("Python.framework/bin/python3", 0o755);
        let other = scratch.executable("homebrew/bin/python3", 0o755);
        let own = scratch.path(&format!("Dervo Local.app/Contents/{BUNDLED_PYTHON}"));
        // 16.4's version switch opens the next version with DERVO_PYTHON set to the framework.
        for given in [None, Some(framework.as_os_str()), Some(other.as_os_str()), Some(os("")), Some(os("relative"))] {
            assert_eq!(resolve_mac_python(given, &exe, &framework).unwrap(), own, "{given:?}");
        }
    }
    #[test] fn broken_own_python_is_an_error_never_the_framework_or_path() {
        let scratch = Scratch::new("bundled missing");
        let framework = scratch.executable("Python.framework/bin/python3", 0o755);
        let exe = app(&scratch, None);
        for given in [None, Some(framework.as_os_str())] {
            let error = resolve_mac_python(given, &exe, &framework).unwrap_err();
            assert!(error.contains("Dervo's own Python") && error.contains("does not exist")
                && error.contains("runtime/python/bin/python3.13") && error.contains("download it again"), "{error}");
        }
        let scratch = Scratch::new("bundled plain");
        let exe = app(&scratch, Some(0o644));
        assert!(resolve_mac_python(None, &exe, &framework).unwrap_err().contains("is not executable"));
    }
    #[test] fn source_runs_keep_the_framework_and_the_override() {
        let scratch = Scratch::new("source run");
        let exe = scratch.executable("target/debug/dervo-local", 0o755);
        let framework = scratch.executable("Python.framework/bin/python3", 0o755);
        assert_eq!(resolve_mac_python(None, &exe, &framework).unwrap(), framework);
        let chosen = scratch.executable("explicit/python3", 0o755);
        assert_eq!(resolve_mac_python(Some(chosen.as_os_str()), &exe, &framework).unwrap(), chosen);
        let missing = scratch.path("missing/python3");
        assert!(resolve_mac_python(None, &exe, &missing).unwrap_err().contains("Python 3.13 framework"));
        assert!(resolve_mac_python(Some(missing.as_os_str()), &exe, &framework).unwrap_err().contains("fallback"));
    }
    #[test] fn tests_resolve_like_a_source_run_and_failures_never_ask_for_python() {
        // cargo test runs target/debug/deps/<crate>-<hash>, outside any bundle.
        #[cfg(target_os = "macos")]
        assert_eq!(resolve_app_python(None).unwrap(), PathBuf::from(FRAMEWORK_PYTHON));
        // Linux has no framework: a source run with nothing recorded takes python3 from PATH, never a guessed path.
        #[cfg(target_os = "linux")]
        assert_eq!(resolve_app_python(None).unwrap(),
                   resolve_python(None, std::env::var_os("PATH").as_deref()).unwrap());
        assert_eq!(python_failure_kind(None), "runtime");
        assert_eq!(python_failure_kind(Some(os("/explicit/python3"))), "configuration");
    }

    #[cfg(target_os = "linux")]
    #[test] fn a_linux_package_runs_its_own_python_from_its_resources() {
        assert_eq!(linux_bundled_python(Path::new("/usr/lib/Dervo Local")),
                   PathBuf::from("/usr/lib/Dervo Local/linux-python/bin/python3.13"));
    }

    #[test] fn host_command_passes_only_flags_script_and_data() {
        let command = host_command(Path::new("/fixture/bin/python3"), Path::new("/fixture/Resources/backend/host.py"), Path::new("/fixture/data dir"));
        assert_eq!(command.get_program(), OsStr::new("/fixture/bin/python3"));
        let args: Vec<&OsStr> = command.get_args().collect();
        assert_eq!(args, ["-E", "-s", "-B", "/fixture/Resources/backend/host.py", "/fixture/data dir"].map(OsStr::new));
        #[cfg(not(target_os = "linux"))]
        assert_eq!(command.get_envs().count(), 0, "no explicit environment is handed to the host");
        // Linux: only the desktop session's allowlisted variables, the fixed PATH and the app's own exe.
        #[cfg(target_os = "linux")]
        for (name, _) in command.get_envs() {
            let name = name.to_str().unwrap();
            assert!(LINUX_ENVIRONMENT.contains(&name) || name == "PATH" || name == "DERVO_APP_EXECUTABLE", "{name} leaked");
        }
    }
    #[test] fn host_command_runs_stub_with_cleared_environment() {
        // A stub shell script stands in for python3. No provider, app or real host is started.
        let scratch = Scratch::new("stub host");
        let stub = scratch.path("stub bin/python3");
        fs::create_dir_all(stub.parent().unwrap()).unwrap();
        fs::write(&stub, "#!/bin/sh\nprintf '%s\\n' \"$#\" \"$@\" \"${HOME-unset}\" \"${DERVO_LAUNCH_CANARY-unset}\"\n").unwrap();
        fs::set_permissions(&stub, fs::Permissions::from_mode(0o755)).unwrap();
        std::env::set_var("DERVO_LAUNCH_CANARY", "must not leak");
        let script = scratch.path("Resources/host.py");
        let data = scratch.path("data with spaces");
        let output = host_command(&stub, &script, &data).output().unwrap();
        assert!(output.status.success());
        let text = String::from_utf8(output.stdout).unwrap();
        // Linux hands the host HOME from the session (LINUX_ENVIRONMENT); a Mac hands it nothing.
        let home = if cfg!(target_os = "linux") { std::env::var("HOME").unwrap_or_else(|_| "unset".into()) } else { "unset".into() };
        let expected = format!("5\n-E\n-s\n-B\n{}\n{}\n{home}\nunset\n", script.display(), data.display());
        assert_eq!(text, expected);
        assert!(!data.exists(), "building the command must not create the data directory");
    }
}
