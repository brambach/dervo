use std::{env, fs, path::Path};

/// The Linux packages bundle a Python and a Codex that scripts/stage-linux-python.py and scripts/stage-windows-codex.py
/// put in these folders. Both are git-ignored, so a fresh checkout has neither, and tauri_build refuses to build when a
/// resource glob matches nothing. That would stop `cargo test` and `cargo check` on a clean Linux machine for no reason,
/// as the tests never run the bundled programs.
///
/// A debug build (cargo test, cargo check, tauri dev) gets a marker file in each missing folder so the globs match.
/// A release build gets nothing, so a package can't be made without the real Python and Codex: it fails as before.
/// The staging scripts clear a folder that holds only the marker before they stage, and the backend finds no bundled
/// program in a folder that only holds the marker, the same as when the folder is missing.
const LINUX_STAGED: [&str; 2] = ["runtime/codex-linux", "linux-python"];

fn main() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("linux") && env::var("PROFILE").as_deref() != Ok("release") {
        for folder in LINUX_STAGED {
            let folder = Path::new(folder);
            if !folder.exists() {
                fs::create_dir_all(folder).expect("create the unstaged folder");
                fs::write(folder.join("NOT-STAGED"), "A debug build made this folder so cargo can build without the bundled \
program. Stage the real one with scripts/stage-linux-python.py or scripts/stage-windows-codex.py before packaging.\n")
                    .expect("write the unstaged marker");
            }
        }
    }
    tauri_build::build()
}
