//! Release signatures: an update is installed only when the Dervo release key signed its installer's SHA-256 together
//! with its release id, build number and product version. The private key lives in 1Password and never touches this
//! code's inputs on a tester's Mac; the public key below is compiled in.
//!
//! The app uses `verify` through `cli`, which the host runs as `dervo-local --verify-release-signature` before it
//! unpacks or saves a download (backend/release_signature.py). The release signer (tools/release-sign) includes this
//! same file, so the signed message has one definition.
use ed25519_dalek::{Signature, VerifyingKey};
use serde_json::{json, Value};

pub const VERIFY_FLAG: &str = "--verify-release-signature";
/// 64 hex characters, or nothing before the key has been made. Without a key every downloaded update is refused.
const PUBLIC_KEY_HEX: &str = include_str!("release-signing-key.hex");
const CONTEXT: &str = "dervo-release-signature-v1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Release { pub release_id: String, pub build_number: u64, pub product_version: String, pub sha256: String }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal { NoKey, Unsigned, BadSignature, Invalid }
impl Refusal {
    pub fn code(self) -> &'static str {
        match self { Self::NoKey => "no_key", Self::Unsigned => "unsigned", Self::BadSignature => "bad_signature", Self::Invalid => "invalid" }
    }
}

fn release_id_ok(value: &str) -> bool {
    let bytes = value.as_bytes();
    (1..=80).contains(&bytes.len()) && bytes[0].is_ascii_alphanumeric()
        && bytes.iter().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

fn version_ok(value: &str) -> bool {
    let parts: Vec<&str> = value.split('.').collect();
    parts.len() == 3 && parts.iter().all(|part| {
        !part.is_empty() && part.len() <= 9 && part.bytes().all(|b| b.is_ascii_digit()) && (part == &"0" || !part.starts_with('0'))
    })
}

fn lower_hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn hex_bytes<const N: usize>(value: &str) -> Option<[u8; N]> {
    if !lower_hex(value, N * 2) { return None; }
    let mut out = [0u8; N];
    for (i, pair) in value.as_bytes().chunks(2).enumerate() {
        out[i] = u8::from_str_radix(std::str::from_utf8(pair).ok()?, 16).ok()?;
    }
    Some(out)
}

impl Release {
    /// Every field is checked for shape first, so no field can smuggle a line break or another field into the message.
    pub fn new(release_id: &str, build_number: u64, product_version: &str, sha256: &str) -> Option<Self> {
        (release_id_ok(release_id) && build_number > 0 && version_ok(product_version) && lower_hex(sha256, 64)).then(|| Self {
            release_id: release_id.into(), build_number, product_version: product_version.into(), sha256: sha256.into(),
        })
    }
    /// The exact bytes the release key signs.
    pub fn message(&self) -> Vec<u8> {
        format!("{CONTEXT}\nrelease_id={}\nbuild_number={}\nproduct_version={}\nsha256={}\n",
                self.release_id, self.build_number, self.product_version, self.sha256).into_bytes()
    }
}

/// The compiled-in public key, or None before one has been made.
pub fn public_key() -> Option<[u8; 32]> {
    let text = PUBLIC_KEY_HEX.trim();
    if text.is_empty() { None } else { hex_bytes::<32>(text) }
}

pub fn verify(public: Option<[u8; 32]>, release: &Release, signature: Option<&str>) -> Result<(), Refusal> {
    let public = public.ok_or(Refusal::NoKey)?;
    let signature = signature.filter(|s| !s.is_empty()).ok_or(Refusal::Unsigned)?;
    let bytes = hex_bytes::<64>(signature).ok_or(Refusal::BadSignature)?;
    let key = VerifyingKey::from_bytes(&public).map_err(|_| Refusal::NoKey)?;
    // Strict: refuses small-order keys and non-canonical signatures, so a signature has exactly one valid form.
    key.verify_strict(&release.message(), &Signature::from_bytes(&bytes)).map_err(|_| Refusal::BadSignature)
}

fn request(input: &str) -> Result<(Release, Option<String>), Refusal> {
    let value: Value = serde_json::from_str(input).map_err(|_| Refusal::Invalid)?;
    let object = value.as_object().ok_or(Refusal::Invalid)?;
    let keys = ["releaseId", "buildNumber", "productVersion", "sha256", "signature"];
    if object.len() != keys.len() || !keys.iter().all(|key| object.contains_key(*key)) { return Err(Refusal::Invalid); }
    let text = |key: &str| object[key].as_str().ok_or(Refusal::Invalid);
    let build = match &object["buildNumber"] {
        Value::Number(n) => n.as_u64(),
        Value::String(s) if !s.is_empty() && s.len() <= 20 && s.bytes().all(|b| b.is_ascii_digit()) => s.parse().ok(),
        _ => None,
    }.ok_or(Refusal::Invalid)?;
    let release = Release::new(text("releaseId")?, build, text("productVersion")?, text("sha256")?).ok_or(Refusal::Invalid)?;
    let signature = match &object["signature"] { Value::Null => None, Value::String(s) => Some(s.clone()), _ => return Err(Refusal::Invalid) };
    Ok((release, signature))
}

/// One JSON request in, one JSON answer out. The answer echoes the release id and hash it verified, so the host can
/// check it's the answer to its own question.
pub fn answer(input: &str, public: Option<[u8; 32]>) -> Value {
    match request(input).and_then(|(release, signature)| verify(public, &release, signature.as_deref()).map(|_| release)) {
        Ok(release) => json!({"verified": true, "releaseId": release.release_id, "sha256": release.sha256}),
        Err(refusal) => json!({"verified": false, "reason": refusal.code()}),
    }
}

/// `dervo-local --verify-release-signature`: reads at most 4 KB from stdin, prints the answer, exits 0 only when verified.
pub fn cli() -> i32 {
    use std::io::{Read, Write};
    let mut input = String::new();
    let read = std::io::stdin().take(4096).read_to_string(&mut input);
    let result = if read.is_ok() { answer(&input, public_key()) } else { json!({"verified": false, "reason": "invalid"}) };
    let mut out = std::io::stdout();
    let _ = writeln!(out, "{result}");
    let _ = out.flush();
    if result["verified"] == json!(true) { 0 } else { 1 }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    pub fn key(seed: u8) -> SigningKey { SigningKey::from_bytes(&[seed; 32]) }
    fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }
    fn release() -> Release { Release::new("0.1.0-beta.11", 1789452916913747, "0.1.0", &"ab".repeat(32)).unwrap() }
    fn sign(key: &SigningKey, release: &Release) -> String { hex(&key.sign(&release.message()).to_bytes()) }
    fn public(key: &SigningKey) -> Option<[u8; 32]> { Some(key.verifying_key().to_bytes()) }
    fn ask(release: &Release, signature: Value, public: Option<[u8; 32]>) -> Value {
        answer(&json!({"releaseId": release.release_id, "buildNumber": release.build_number.to_string(),
                       "productVersion": release.product_version, "sha256": release.sha256, "signature": signature}).to_string(), public)
    }

    #[test] fn a_good_signature_verifies() {
        let (k, r) = (key(7), release());
        assert_eq!(verify(public(&k), &r, Some(&sign(&k, &r))), Ok(()));
        assert_eq!(ask(&r, json!(sign(&k, &r)), public(&k)), json!({"verified": true, "releaseId": r.release_id, "sha256": r.sha256}));
    }

    #[test] fn a_changed_hash_version_build_or_release_id_is_refused() {
        let (k, r) = (key(7), release());
        let signature = sign(&k, &r);
        let changed = [
            Release { sha256: "cd".repeat(32), ..r.clone() },
            Release { product_version: "0.1.1".into(), ..r.clone() },
            Release { build_number: r.build_number - 1, ..r.clone() },
            Release { release_id: "0.1.0-beta.10".into(), ..r.clone() },
        ];
        for other in changed {
            assert_eq!(verify(public(&k), &other, Some(&signature)), Err(Refusal::BadSignature), "{other:?}");
        }
        // Negative control: the untouched release still verifies with the same signature.
        assert_eq!(verify(public(&k), &r, Some(&signature)), Ok(()));
    }

    #[test] fn an_unsigned_release_is_refused() {
        let (k, r) = (key(7), release());
        assert_eq!(verify(public(&k), &r, None), Err(Refusal::Unsigned));
        assert_eq!(verify(public(&k), &r, Some("")), Err(Refusal::Unsigned));
        assert_eq!(ask(&r, Value::Null, public(&k)), json!({"verified": false, "reason": "unsigned"}));
    }

    #[test] fn a_signature_from_another_key_is_refused() {
        let (k, r) = (key(7), release());
        assert_eq!(verify(public(&k), &r, Some(&sign(&key(8), &r))), Err(Refusal::BadSignature));
        assert_eq!(verify(public(&key(8)), &r, Some(&sign(&key(8), &r))), Ok(()));  // negative control
    }

    #[test] fn no_compiled_key_refuses_everything() {
        let (k, r) = (key(7), release());
        assert_eq!(verify(None, &r, Some(&sign(&k, &r))), Err(Refusal::NoKey));
    }

    #[test] fn malformed_signatures_and_requests_are_refused() {
        let (k, r) = (key(7), release());
        let good = sign(&k, &r);
        for bad in [good.to_uppercase(), good[..126].to_string(), format!("{good}00"), "zz".repeat(64)] {
            assert_eq!(verify(public(&k), &r, Some(&bad)), Err(Refusal::BadSignature));
        }
        for input in ["", "[]", "{}", r#"{"releaseId":"a","buildNumber":1,"productVersion":"0.1.0","sha256":"x","signature":null}"#] {
            assert_eq!(answer(input, public(&k)), json!({"verified": false, "reason": "invalid"}));
        }
        // A field can't carry a line break into the signed message.
        assert!(Release::new("a\nsha256=x", 1, "0.1.0", &"ab".repeat(32)).is_none());
        assert!(Release::new("a", 1, "0.1.0\n", &"ab".repeat(32)).is_none());
        assert!(Release::new("a", 0, "0.1.0", &"ab".repeat(32)).is_none());
        assert!(Release::new("a", 1, "01.1.0", &"ab".repeat(32)).is_none());
    }

    #[test] fn the_message_is_fixed() {
        assert_eq!(String::from_utf8(release().message()).unwrap(), format!(
            "dervo-release-signature-v1\nrelease_id=0.1.0-beta.11\nbuild_number=1789452916913747\nproduct_version=0.1.0\nsha256={}\n", "ab".repeat(32)));
    }

    #[test] fn the_compiled_key_is_empty_or_a_valid_key() {
        let text = PUBLIC_KEY_HEX.trim();
        assert!(text.is_empty() || public_key().map(|k| VerifyingKey::from_bytes(&k).is_ok()) == Some(true));
    }
}
