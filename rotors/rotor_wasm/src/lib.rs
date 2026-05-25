use rotor_core::RotorSession;
use wasm_bindgen::prelude::*;

fn js_err<E: std::fmt::Display>(err: E) -> JsValue {
    JsValue::from_str(&err.to_string())
}

#[wasm_bindgen]
pub struct WasmRotorSession {
    inner: RotorSession,
}

#[wasm_bindgen]
impl WasmRotorSession {
    #[wasm_bindgen(constructor)]
    pub fn new(room_slug: String, identity: String) -> Result<WasmRotorSession, JsValue> {
        Ok(Self {
            inner: RotorSession::new(room_slug, identity).map_err(js_err)?,
        })
    }

    pub fn create_group(&mut self) -> Result<(), JsValue> {
        self.inner.create_group().map_err(js_err)
    }

    pub fn add_member(&mut self, key_package_bytes: &[u8]) -> Result<js_sys::Array, JsValue> {
        let (welcome, commit) = self
            .inner
            .add_member(key_package_bytes)
            .map_err(js_err)?;

        let result = js_sys::Array::new();
        result.push(&js_sys::Uint8Array::from(welcome.as_slice()));
        result.push(&js_sys::Uint8Array::from(commit.as_slice()));

        Ok(result)
    }

    pub fn key_package(&mut self) -> Result<Vec<u8>, JsValue> {
        self.inner.key_package().map_err(js_err)
    }

    pub fn join_from_welcome(&mut self, welcome_bytes: &[u8]) -> Result<(), JsValue> {
        self.inner.join_from_welcome(welcome_bytes).map_err(js_err)
    }

    pub fn process_message(&mut self, message_bytes: &[u8]) -> Result<Option<Vec<u8>>, JsValue> {
        self.inner.process_message(message_bytes).map_err(js_err)
    }

    pub fn encrypt_app(&mut self, plaintext: &[u8]) -> Result<Vec<u8>, JsValue> {
        self.inner.encrypt_app(plaintext).map_err(js_err)
    }

    pub fn export_state(&self) -> Result<Vec<u8>, JsValue> {
        self.inner.export_state().map_err(js_err)
    }

    pub fn import_state(state_bytes: &[u8]) -> Result<WasmRotorSession, JsValue> {
        Ok(Self {
            inner: RotorSession::import_state(state_bytes).map_err(js_err)?,
        })
    }
}