use crate::{
    Error, Result,
    validation::{MAX_SECRET_BYTES, validate_secret, validate_slot},
};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use tauri::{
    AppHandle, Runtime,
    plugin::{PluginApi, PluginHandle},
};
use zeroize::{Zeroize, Zeroizing};

pub fn init<R: Runtime, C: DeserializeOwned>(
    _app: &AppHandle<R>,
    api: PluginApi<R, C>,
) -> Result<Platform<R>> {
    let handle = api
        .register_android_plugin("com.account.platform", "PlatformPlugin")
        .map_err(|_| Error::NativeBridge)?;
    Ok(Platform(handle))
}

#[derive(Serialize)]
struct SlotRequest<'a> {
    slot: &'a str,
}
// Never derive Debug for objects containing secret bytes or their encodings.
#[derive(Serialize)]
struct StoreRequest<'a> {
    slot: &'a str,
    secret: &'a str,
}
#[derive(Deserialize)]
struct LoadResponse {
    secret: Option<String>,
}
#[derive(Deserialize)]
struct EmptyResponse {}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SaveMarkdownRequest<'a> {
    file_name: &'a str,
    content: &'a str,
}

#[derive(Deserialize)]
struct SaveMarkdownResponse {
    saved: bool,
}

const MAX_BILL_BYTES: usize = 4 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SelectBillResponse {
    cancelled: bool,
    failed: Option<bool>,
    file_name: Option<String>,
    content_base64: Option<String>,
}

#[derive(Deserialize)]
struct SelectBatchResponse { cancelled: bool, count: usize }
#[derive(Serialize)]
struct BillIndexRequest { index: usize }

/// Raw bytes stay on the native side and are erased after the upload attempt.
/// Deliberately has no Serialize or Debug implementation.
pub struct SelectedBill {
    pub file_name: String,
    pub bytes: Vec<u8>,
}
impl Drop for SelectedBill {
    fn drop(&mut self) {
        self.bytes.zeroize();
    }
}

/// Native-only secure storage through Android Keystore. No JavaScript command API.
pub struct Platform<R: Runtime>(PluginHandle<R>);

impl<R: Runtime> Platform<R> {
    pub async fn select_bills(&self) -> Result<Option<usize>> {
        let response: SelectBatchResponse = self.0.run_mobile_plugin_async("selectBill", ())
            .await.map_err(|_| Error::NativeBridge)?;
        if response.cancelled { return Ok(None); }
        if response.count == 0 || response.count > 128 { return Err(Error::NativeBridge); }
        Ok(Some(response.count))
    }
    pub async fn clear_selected_bills(&self) -> Result<()> {
        let _: EmptyResponse = self.0.run_mobile_plugin_async("clearSelectedBills", ())
            .await.map_err(|_| Error::NativeBridge)?;
        Ok(())
    }
    pub async fn read_selected_bill(&self, index: usize) -> Result<(Option<SelectedBill>, Option<String>)> {
        let response: SelectBillResponse = self
            .0
            .run_mobile_plugin_async("readSelectedBill", BillIndexRequest { index })
            .await
            .map_err(|_| Error::NativeBridge)?;
        let encoded = response.content_base64.map(Zeroizing::new);
        if response.cancelled { return Ok((None, None)); }
        let name = response.file_name.ok_or(Error::NativeBridge)?;
        if response.failed.unwrap_or(false) {
            let safe: String = name.chars().filter(|c| !c.is_control() && *c != '/' && *c != '\\').take(200).collect();
            return Ok((None, Some(safe)));
        }
        let encoded = encoded.ok_or(Error::NativeBridge)?;
        let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
        let reserved = ["CON", "PRN", "AUX", "NUL"].contains(&stem.as_str())
            || (stem.len() == 4
                && (stem.starts_with("COM") || stem.starts_with("LPT"))
                && matches!(stem.as_bytes()[3], b'1'..=b'9'));
        let valid_name = !name.is_empty()
            && name.len() <= 200
            && name.trim() == name
            && !name.ends_with('.')
            && !reserved
            && !name.contains("..")
            && !name
                .chars()
                .any(|c| c.is_control() || "/\\:<>\"|?*".contains(c))
            && name.rsplit_once('.').is_some_and(|(_, suffix)| {
                suffix.eq_ignore_ascii_case("csv") || suffix.eq_ignore_ascii_case("xlsx")
            });
        if !valid_name || encoded.len() > MAX_BILL_BYTES.div_ceil(3) * 4 {
            return Err(Error::NativeBridge);
        }
        // Base64's estimate includes up to two padding bytes. Wipe failures too.
        let mut bytes = vec![0; MAX_BILL_BYTES + 2];
        match STANDARD.decode_slice(encoded.as_bytes(), &mut bytes) {
            Ok(count) if (1..=MAX_BILL_BYTES).contains(&count) => {
                bytes.truncate(count);
                Ok((Some(SelectedBill {
                    file_name: name,
                    bytes,
                }), None))
            }
            _ => {
                bytes.zeroize();
                Err(Error::NativeBridge)
            }
        }
    }

    /// Native-only SAF creation. No path or URI can be supplied by JavaScript.
    pub async fn save_markdown_report(&self, file_name: &str, content: &str) -> Result<bool> {
        let response: SaveMarkdownResponse = self
            .0
            .run_mobile_plugin_async(
                "saveMarkdownReport",
                SaveMarkdownRequest { file_name, content },
            )
            .await
            .map_err(|_| Error::NativeBridge)?;
        Ok(response.saved)
    }
    pub fn store_secret(&self, slot: &str, secret: &[u8]) -> Result<()> {
        validate_slot(slot)?;
        validate_secret(secret)?;
        let encoded = Zeroizing::new(STANDARD.encode(secret));
        let _: EmptyResponse = self
            .0
            .run_mobile_plugin(
                "storeSecret",
                StoreRequest {
                    slot,
                    secret: &encoded,
                },
            )
            .map_err(|_| Error::NativeBridge)?;
        Ok(())
    }
    pub fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>> {
        validate_slot(slot)?;
        let response: LoadResponse = self
            .0
            .run_mobile_plugin("loadSecret", SlotRequest { slot })
            .map_err(|_| Error::NativeBridge)?;
        let Some(encoded) = response.secret.map(Zeroizing::new) else {
            return Ok(None);
        };
        if encoded.len() > MAX_SECRET_BYTES.div_ceil(3) * 4 {
            return Err(Error::Decryption);
        }
        let secret = STANDARD
            .decode(encoded.as_bytes())
            .map_err(|_| Error::Decryption)?;
        validate_secret(&secret)?;
        Ok(Some(secret))
    }
    pub fn delete_secret(&self, slot: &str) -> Result<()> {
        validate_slot(slot)?;
        let _: EmptyResponse = self
            .0
            .run_mobile_plugin("deleteSecret", SlotRequest { slot })
            .map_err(|_| Error::NativeBridge)?;
        Ok(())
    }
}
