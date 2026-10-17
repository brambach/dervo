//! Off the Mac, the host gets no Keychain socket (b16/seam). That's what an unsigned Mac build does too (keychain.rs,
//! `configure` answers None before `signed()`), so the host already runs without one. Windows Credential Manager comes
//! with sign-in on b16/windows (milestone 3).
use std::{path::Path, process::Command};

pub struct Endpoint;

pub fn configure(_command: &mut Command, _python: &Path, _script: &Path) -> Result<Option<Endpoint>, String> {
    Ok(None)
}

impl Endpoint {
    pub fn serve(self) {}
}

pub fn self_check() -> Result<(), String> {
    Err("The Keychain self-check is for signed Mac builds.".into())
}
