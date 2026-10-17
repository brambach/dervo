//! Sign a packaged Dervo release with the release key held in 1Password.
//!
//!   dervo-release-sign public-key [--vault NAME]
//!       Prints the release key's public half as 64 hex characters, for src/release-signing-key.hex.
//!   dervo-release-sign sign <package folder> [--vault NAME]
//!       Checks the package's installer against its package.json, signs its SHA-256, release id, build number and
//!       product version, and writes release-signature.json beside package.json.
//!
//! The private key is read with `op read`, which asks for Touch ID. It arrives over a pipe, stays in memory that's
//! wiped on drop, and is never written to disk, a log or argv. The signed message is defined once, in
//! src/release_signature.rs, which the app uses to check it.
#[allow(dead_code)]
#[path = "../../../src/release_signature.rs"]
mod release_signature;

use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs, io::Read, path::Path, process::{Command, Stdio}};
use zeroize::Zeroizing;

const ITEM: &str = "Dervo release signing key";
const PKCS8_V1: [u8; 16] = [0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20];
const PKCS8_V2: [u8; 16] = [0x30, 0x51, 0x02, 0x01, 0x01, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20];

fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }

/// `op read` for one field of the release key item. Its output comes back over a pipe into wiped memory.
fn op_read(vault: &str, field: &str) -> Result<Zeroizing<Vec<u8>>, String> {
    let mut child = Command::new("op").args(["read", "--no-newline", &format!("op://{vault}/{ITEM}/{field}")])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::inherit()).spawn()
        .map_err(|e| format!("Couldn't run the 1Password CLI (op): {e}"))?;
    let mut out = Zeroizing::new(Vec::with_capacity(512));
    child.stdout.take().ok_or("1Password gave no output")?.take(16384).read_to_end(&mut out)
        .map_err(|e| format!("Couldn't read from 1Password: {e}"))?;
    if !child.wait().map_err(|e| e.to_string())?.success() {
        return Err(format!("1Password didn't return \"{ITEM}\" {field} from the {vault} vault."));
    }
    Ok(out)
}

/// An OpenSSH public key line, `ssh-ed25519 AAAA… comment`, as its 32 key bytes.
fn openssh_public(text: &[u8]) -> Result<[u8; 32], String> {
    let text = std::str::from_utf8(text).map_err(|_| "The public key isn't text")?;
    let mut parts = text.split_whitespace();
    let blob = match (parts.next(), parts.next()) {
        (Some("ssh-ed25519"), Some(b64)) => STANDARD.decode(b64).map_err(|_| "The public key isn't valid base64")?,
        _ => return Err("The 1Password item's public key isn't an Ed25519 SSH key.".into()),
    };
    let mut expected = vec![0, 0, 0, 11];
    expected.extend_from_slice(b"ssh-ed25519");
    expected.extend_from_slice(&[0, 0, 0, 32]);
    if blob.len() != expected.len() + 32 || blob[..expected.len()] != expected[..] {
        return Err("The public key's contents aren't an Ed25519 key.".into());
    }
    Ok(blob[expected.len()..].try_into().unwrap())
}

/// A PKCS #8 PEM Ed25519 private key, as 1Password's `op read` returns an SSH key's private half, as a signing key.
fn pkcs8_private(pem: &[u8]) -> Result<SigningKey, String> {
    let text = std::str::from_utf8(pem).map_err(|_| "The private key isn't text")?;
    let body: Zeroizing<String> = Zeroizing::new(text.lines().map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with("-----")).collect());
    if !text.trim_start().starts_with("-----BEGIN PRIVATE KEY-----") {
        return Err("1Password returned the private key in a format this tool doesn't read. Expected PKCS #8.".into());
    }
    let der = Zeroizing::new(STANDARD.decode(body.as_bytes()).map_err(|_| "The private key isn't valid base64")?);
    let known = (der.len() == 48 && der[..16] == PKCS8_V1) || (der.len() == 83 && der[..16] == PKCS8_V2);
    if !known { return Err("The private key isn't an Ed25519 key.".into()); }
    let mut seed = Zeroizing::new([0u8; 32]);
    seed.copy_from_slice(&der[16..48]);
    Ok(SigningKey::from_bytes(&seed))
}

fn text<'a>(value: &'a Value, pointer: &str) -> Result<&'a str, String> {
    value.pointer(pointer).and_then(Value::as_str).ok_or_else(|| format!("package.json has no {pointer}"))
}

/// The release a package folder holds, after hashing its installer again.
fn package_release(folder: &Path) -> Result<release_signature::Release, String> {
    let package: Value = serde_json::from_slice(&fs::read(folder.join("package.json")).map_err(|e| format!("Couldn't read package.json: {e}"))?)
        .map_err(|_| "package.json isn't JSON")?;
    let release_id = text(&package, "/releaseId")?;
    let artifact = text(&package, "/artifact")?;
    if artifact.contains('/') || artifact.starts_with('.') { return Err("package.json names an unexpected installer".into()); }
    let sha256 = text(&package, "/sha256")?;
    if text(&package, "/bundleAudit/nativeVersion/releaseId")? != release_id {
        return Err("package.json's release id and native version disagree".into());
    }
    let build: u64 = text(&package, "/bundleAudit/nativeVersion/buildNumber")?.parse().map_err(|_| "The build number isn't a number")?;
    let version = text(&package, "/bundleAudit/nativeVersion/productVersion")?;
    let mut file = fs::File::open(folder.join(artifact)).map_err(|e| format!("Couldn't open the installer: {e}"))?;
    let mut hasher = Sha256::new();
    std::io::copy(&mut file, &mut hasher).map_err(|e| format!("Couldn't read the installer: {e}"))?;
    if hex(&hasher.finalize()) != sha256 { return Err("The installer's SHA-256 doesn't match package.json. Nothing was signed.".into()); }
    release_signature::Release::new(release_id, build, version, sha256).ok_or_else(|| "package.json's release fields aren't valid".into())
}

fn sign(folder: &Path, vault: &str) -> Result<Value, String> {
    let release = package_release(folder)?;
    let public = release_signature::public_key()
        .ok_or("This checkout has no release public key. Run `public-key` and save it in src/release-signing-key.hex first.")?;
    let output = folder.join("release-signature.json");
    if output.exists() { return Err("release-signature.json already exists in that package. Nothing was signed.".into()); }
    let key = pkcs8_private(&op_read(vault, "private key")?)?;
    if key.verifying_key().to_bytes() != public {
        return Err("The key in 1Password isn't the one compiled into Dervo. Nothing was signed.".into());
    }
    let signature = hex(&key.sign(&release.message()).to_bytes());
    drop(key);
    release_signature::verify(Some(public), &release, Some(&signature)).map_err(|_| "The new signature didn't verify. Nothing was saved.")?;
    let record = json!({"releaseId": release.release_id, "buildNumber": release.build_number.to_string(),
                        "productVersion": release.product_version, "sha256": release.sha256,
                        "signature": signature, "publicKey": hex(&public)});
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&output).map_err(|e| format!("Couldn't save the signature: {e}"))?;
    std::io::Write::write_all(&mut file, format!("{}\n", serde_json::to_string_pretty(&record).unwrap()).as_bytes())
        .map_err(|e| format!("Couldn't save the signature: {e}"))?;
    Ok(record)
}

fn run(args: &[String]) -> Result<String, String> {
    let mut vault = "Personal".to_string();
    let mut rest = Vec::new();
    let mut items = args.iter();
    while let Some(arg) = items.next() {
        if arg == "--vault" { vault = items.next().ok_or("--vault needs a vault name")?.clone(); } else { rest.push(arg.as_str()); }
    }
    if vault.is_empty() || vault.contains('/') { return Err("Choose a vault name without a slash.".into()); }
    match rest.as_slice() {
        ["public-key"] => Ok(hex(&openssh_public(&op_read(&vault, "public key")?)?)),
        ["sign", folder] => Ok(serde_json::to_string_pretty(&sign(Path::new(folder), &vault)?).unwrap()
            + "\nPut \"signature\" in this release's row in dervo_beta_releases when you publish it."),
        _ => Err("Usage: dervo-release-sign public-key [--vault NAME]\n       dervo-release-sign sign <package folder> [--vault NAME]".into()),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(out) => println!("{out}"),
        Err(error) => { eprintln!("{error}"); std::process::exit(1); }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pem(der: &[u8]) -> Vec<u8> { format!("-----BEGIN PRIVATE KEY-----\n{}\n-----END PRIVATE KEY-----\n", STANDARD.encode(der)).into_bytes() }

    #[test] fn reads_both_pkcs8_forms_and_openssh_public_keys() {
        let key = SigningKey::from_bytes(&[9; 32]);
        let public = key.verifying_key().to_bytes();
        let v1 = [&PKCS8_V1[..], &[9; 32]].concat();
        let v2 = [&PKCS8_V2[..], &[9; 32], &[0x81, 0x21, 0x00], &public].concat();
        for der in [v1, v2] { assert_eq!(pkcs8_private(&pem(&der)).unwrap().verifying_key().to_bytes(), public); }
        let blob = [&[0, 0, 0, 11][..], b"ssh-ed25519", &[0, 0, 0, 32], &public].concat();
        assert_eq!(openssh_public(format!("ssh-ed25519 {} comment", STANDARD.encode(blob)).as_bytes()).unwrap(), public);
        assert!(openssh_public(b"ssh-rsa AAAA").is_err());
        assert!(pkcs8_private(b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----").is_err());
        assert!(pkcs8_private(&pem(&[0; 48])).is_err());
    }

    #[test] fn a_package_whose_installer_changed_is_not_signed() {
        let folder = std::env::temp_dir().join(format!("dervo-release-sign-{}", std::process::id()));
        fs::create_dir_all(&folder).unwrap();
        fs::write(folder.join("Dervo-0.1.0-beta.11-macos.zip"), b"installer").unwrap();
        let digest = hex(&Sha256::digest(b"installer"));
        let package = |sha: &str| json!({"releaseId": "0.1.0-beta.11", "artifact": "Dervo-0.1.0-beta.11-macos.zip", "sha256": sha,
            "bundleAudit": {"nativeVersion": {"releaseId": "0.1.0-beta.11", "buildNumber": "42", "productVersion": "0.1.0"}}});
        fs::write(folder.join("package.json"), package(&digest).to_string()).unwrap();
        let release = package_release(&folder).unwrap();
        assert_eq!((release.build_number, release.sha256.as_str()), (42, digest.as_str()));
        fs::write(folder.join("package.json"), package(&"0".repeat(64)).to_string()).unwrap();
        assert!(package_release(&folder).unwrap_err().contains("doesn't match"));
        fs::remove_dir_all(&folder).unwrap();
    }
}
