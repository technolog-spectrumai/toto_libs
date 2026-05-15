use std::collections::HashMap;
use std::sync::Mutex;

use enigma_core::EnigmaSession;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, State};
use zeroize::Zeroize;

// ── App config ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub profile: String,
    pub identity: String,
}

type AppConfigState = Mutex<AppConfig>;

#[derive(Debug, Clone, Serialize)]
pub struct AppRuntimeConfig {
    pub profile: String,
    pub identity: String,
    pub profile_state_path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OfflineProfile {
    pub display_name: String,
    pub email: String,
    pub phone: String,
    pub bio: String,
    pub date_of_birth: String,
}

impl Default for OfflineProfile {
    fn default() -> Self {
        Self {
            display_name: String::new(),
            email: String::new(),
            phone: String::new(),
            bio: String::new(),
            date_of_birth: String::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct OfflineProfileOption {
    pub id: String,
    pub label: String,
    pub profile: OfflineProfile,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AppProfileState {
    #[serde(default)]
    pub server_url: String,
    #[serde(default)]
    pub auth_token: Option<String>,
    #[serde(default)]
    pub offline_profile: OfflineProfile,
    #[serde(default)]
    pub offline_profiles: Vec<OfflineProfileOption>,
    #[serde(default)]
    pub selected_offline_profile_id: String,
}

// ── In-process session registry ───────────────────────────────────────────────

type Sessions = Mutex<HashMap<String, EnigmaSession>>;

fn session_key(profile: &str, room: &str, identity: &str) -> String {
    format!("{}:{}:{}", profile, room, identity)
}

// ── Small helpers ─────────────────────────────────────────────────────────────

fn safe_filename_component(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect::<String>()
}

fn active_config(config: &State<AppConfigState>) -> Result<AppConfig, String> {
    config
        .lock()
        .map(|config| config.clone())
        .map_err(|e| e.to_string())
}

fn runtime_config(app: &AppHandle, config: &AppConfig) -> AppRuntimeConfig {
    AppRuntimeConfig {
        profile: config.profile.clone(),
        identity: config.identity.clone(),
        profile_state_path: profile_state_path(app, config).display().to_string(),
    }
}

// ── Persistence helpers ───────────────────────────────────────────────────────

fn profile_dir(app: &AppHandle, config: &AppConfig) -> std::path::PathBuf {
    let base_dir = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| std::path::PathBuf::from("."));

    let profile = safe_filename_component(&config.profile);
    let dir = base_dir.join("profiles").join(profile);

    let _ = std::fs::create_dir_all(&dir);

    dir
}

fn profile_state_path(app: &AppHandle, config: &AppConfig) -> std::path::PathBuf {
    profile_dir(app, config).join("profile_state.json")
}

fn load_profile_state(app: &AppHandle, config: &AppConfig) -> AppProfileState {
    let path = profile_state_path(app, config);

    let Ok(bytes) = std::fs::read(path) else {
        return normalize_profile_state(AppProfileState::default(), config);
    };

    normalize_profile_state(
        serde_json::from_slice::<AppProfileState>(&bytes).unwrap_or_default(),
        config,
    )
}

fn save_profile_state(
    app: &AppHandle,
    config: &AppConfig,
    state: &AppProfileState,
) -> Result<(), String> {
    let path = profile_state_path(app, config);
    let state = normalize_profile_state(state.clone(), config);

    let bytes = serde_json::to_vec_pretty(&state).map_err(|e| e.to_string())?;

    std::fs::write(path, bytes).map_err(|e| e.to_string())
}

fn default_offline_profile_option(
    config: &AppConfig,
    profile: OfflineProfile,
) -> OfflineProfileOption {
    let label = if config.identity.trim().is_empty() {
        config.profile.trim()
    } else {
        config.identity.trim()
    };
    let label = if label.is_empty() { "Default" } else { label };

    let id = safe_filename_component(label);
    let id = if id.is_empty() {
        "default".to_string()
    } else {
        id
    };

    OfflineProfileOption {
        id,
        label: label.to_string(),
        profile,
    }
}

fn normalize_profile_state(mut state: AppProfileState, config: &AppConfig) -> AppProfileState {
    if state.offline_profiles.is_empty() {
        let default_profile = default_offline_profile_option(config, state.offline_profile.clone());
        state.selected_offline_profile_id = default_profile.id.clone();
        state.offline_profiles.push(default_profile);
    } else if state.offline_profiles.len() == 1
        && state.offline_profiles[0].id == "default"
        && state.offline_profiles[0].label == "Default"
    {
        let default_profile =
            default_offline_profile_option(config, state.offline_profiles[0].profile.clone());
        state.selected_offline_profile_id = default_profile.id.clone();
        state.offline_profiles[0] = default_profile;
    }

    if state.selected_offline_profile_id.is_empty() {
        state.selected_offline_profile_id = state
            .offline_profiles
            .first()
            .map(|profile| profile.id.clone())
            .unwrap_or_else(|| "default".to_string());
    }

    if !state
        .offline_profiles
        .iter()
        .any(|profile| profile.id == state.selected_offline_profile_id)
    {
        state.selected_offline_profile_id = state
            .offline_profiles
            .first()
            .map(|profile| profile.id.clone())
            .unwrap_or_else(|| "default".to_string());
    }

    if let Some(selected) = state
        .offline_profiles
        .iter()
        .find(|profile| profile.id == state.selected_offline_profile_id)
    {
        state.offline_profile = selected.profile.clone();
    }

    state
}

fn state_path(
    app: &AppHandle,
    config: &AppConfig,
    room: &str,
    identity: &str,
) -> std::path::PathBuf {
    let room = safe_filename_component(room);
    let identity = safe_filename_component(identity);

    profile_dir(app, config).join(format!("mls_{}_{}.bin", room, identity))
}

fn persist(
    app: &AppHandle,
    config: &AppConfig,
    session: &EnigmaSession,
    room: &str,
    identity: &str,
) {
    if let Ok(mut bytes) = session.export_state() {
        let _ = std::fs::write(state_path(app, config, room, identity), &bytes);
        bytes.zeroize();
    }
}

fn restore(
    app: &AppHandle,
    config: &AppConfig,
    room: &str,
    identity: &str,
) -> Option<EnigmaSession> {
    let mut bytes = std::fs::read(state_path(app, config, room, identity)).ok()?;
    let result = EnigmaSession::import_state(&bytes).ok();
    bytes.zeroize();
    result
}

fn wipe_file(app: &AppHandle, config: &AppConfig, room: &str, identity: &str) {
    let path = state_path(app, config, room, identity);

    // Best-effort overwrite before delete.
    if let Ok(meta) = std::fs::metadata(&path) {
        let mut zeros = vec![0u8; meta.len() as usize];
        let _ = std::fs::write(&path, &zeros);
        zeros.zeroize();
    }

    let _ = std::fs::remove_file(path);
}

// ── Tauri commands ────────────────────────────────────────────────────────────

#[tauri::command]
fn current_profile(config: State<AppConfigState>) -> Result<String, String> {
    Ok(active_config(&config)?.profile)
}

#[tauri::command]
fn current_identity(config: State<AppConfigState>) -> Result<String, String> {
    Ok(active_config(&config)?.identity)
}

#[tauri::command]
fn app_get_runtime_config(
    app: AppHandle,
    config: State<AppConfigState>,
) -> Result<AppRuntimeConfig, String> {
    Ok(runtime_config(&app, &active_config(&config)?))
}

#[tauri::command]
fn app_set_active_profile(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    profile: String,
    identity: Option<String>,
) -> Result<AppRuntimeConfig, String> {
    let profile = profile.trim();
    if profile.is_empty() {
        return Err("profile is required".to_string());
    }

    let identity = identity
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| profile.to_string());

    let next_config = AppConfig {
        profile: profile.to_string(),
        identity,
    };

    {
        let mut guard = config.lock().map_err(|e| e.to_string())?;
        *guard = next_config.clone();
    }

    if let Ok(mut sessions) = sessions.lock() {
        sessions.clear();
    }

    let state = load_profile_state(&app, &next_config);
    save_profile_state(&app, &next_config, &state)?;

    Ok(runtime_config(&app, &next_config))
}

#[tauri::command]
fn app_get_profile_state(
    app: AppHandle,
    config: State<AppConfigState>,
) -> Result<AppProfileState, String> {
    let config = active_config(&config)?;
    Ok(load_profile_state(&app, &config))
}

#[tauri::command]
fn app_set_server_url(
    app: AppHandle,
    config: State<AppConfigState>,
    server_url: String,
) -> Result<(), String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);
    state.server_url = server_url.trim_end_matches('/').to_string();
    save_profile_state(&app, &config, &state)
}

#[tauri::command]
fn app_get_server_url(app: AppHandle, config: State<AppConfigState>) -> Result<String, String> {
    let config = active_config(&config)?;
    Ok(load_profile_state(&app, &config).server_url)
}

#[tauri::command]
fn app_set_auth_token(
    app: AppHandle,
    config: State<AppConfigState>,
    auth_token: String,
) -> Result<(), String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);
    state.auth_token = Some(auth_token);
    save_profile_state(&app, &config, &state)
}

#[tauri::command]
fn app_get_auth_token(
    app: AppHandle,
    config: State<AppConfigState>,
) -> Result<Option<String>, String> {
    let config = active_config(&config)?;
    Ok(load_profile_state(&app, &config).auth_token)
}

#[tauri::command]
fn app_clear_auth_token(app: AppHandle, config: State<AppConfigState>) -> Result<(), String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);
    state.auth_token = None;
    save_profile_state(&app, &config, &state)
}

#[tauri::command]
fn app_clear_profile_state(
    app: AppHandle,
    config: State<AppConfigState>,
) -> Result<AppProfileState, String> {
    let config = active_config(&config)?;
    let state = normalize_profile_state(AppProfileState::default(), &config);
    save_profile_state(&app, &config, &state)?;
    Ok(state)
}

#[tauri::command]
fn app_get_offline_profile(
    app: AppHandle,
    config: State<AppConfigState>,
) -> Result<OfflineProfile, String> {
    let config = active_config(&config)?;
    Ok(load_profile_state(&app, &config).offline_profile)
}

#[tauri::command]
fn app_set_offline_profile(
    app: AppHandle,
    config: State<AppConfigState>,
    offline_profile: OfflineProfile,
) -> Result<(), String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);
    if let Some(selected) = state
        .offline_profiles
        .iter_mut()
        .find(|profile| profile.id == state.selected_offline_profile_id)
    {
        selected.profile = offline_profile.clone();
    }
    state.offline_profile = offline_profile;
    save_profile_state(&app, &config, &state)
}

#[tauri::command]
fn app_clear_offline_profile(
    app: AppHandle,
    config: State<AppConfigState>,
) -> Result<AppProfileState, String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);
    let empty_profile = OfflineProfile::default();

    if let Some(selected) = state
        .offline_profiles
        .iter_mut()
        .find(|profile| profile.id == state.selected_offline_profile_id)
    {
        selected.profile = empty_profile.clone();
    }

    state.offline_profile = empty_profile;
    state = normalize_profile_state(state, &config);
    save_profile_state(&app, &config, &state)?;

    Ok(state)
}

#[tauri::command]
fn app_set_selected_offline_profile(
    app: AppHandle,
    config: State<AppConfigState>,
    selected_offline_profile_id: String,
) -> Result<AppProfileState, String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);

    if !state
        .offline_profiles
        .iter()
        .any(|profile| profile.id == selected_offline_profile_id)
    {
        return Err("offline profile not found".to_string());
    }

    state.selected_offline_profile_id = selected_offline_profile_id;
    state = normalize_profile_state(state, &config);
    save_profile_state(&app, &config, &state)?;

    Ok(state)
}

#[tauri::command]
fn app_load_offline_profile(
    app: AppHandle,
    config: State<AppConfigState>,
    label: String,
) -> Result<AppProfileState, String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);
    let label = label.trim().to_string();

    if label.is_empty() {
        return Err("offline profile name is required".to_string());
    }

    let offline_profile_id = safe_filename_component(&label);
    if offline_profile_id.is_empty() {
        return Err("offline profile name must contain at least one safe character".to_string());
    }

    if let Some(existing) = state
        .offline_profiles
        .iter()
        .find(|profile| profile.id == offline_profile_id || profile.label == label)
    {
        state.selected_offline_profile_id = existing.id.clone();
    } else {
        state.offline_profiles.push(OfflineProfileOption {
            id: offline_profile_id.clone(),
            label,
            profile: OfflineProfile::default(),
        });
        state.selected_offline_profile_id = offline_profile_id;
    }

    state = normalize_profile_state(state, &config);
    save_profile_state(&app, &config, &state)?;

    Ok(state)
}

#[tauri::command]
fn app_upsert_offline_profile(
    app: AppHandle,
    config: State<AppConfigState>,
    offline_profile_id: String,
    label: String,
    offline_profile: OfflineProfile,
) -> Result<AppProfileState, String> {
    let config = active_config(&config)?;
    let mut state = load_profile_state(&app, &config);
    let offline_profile_id = safe_filename_component(&offline_profile_id);
    let label = label.trim().to_string();

    if offline_profile_id.is_empty() {
        return Err("offline profile id is required".to_string());
    }

    if label.is_empty() {
        return Err("offline profile label is required".to_string());
    }

    if let Some(existing) = state
        .offline_profiles
        .iter_mut()
        .find(|profile| profile.id == offline_profile_id)
    {
        existing.label = label;
        existing.profile = offline_profile.clone();
    } else {
        state.offline_profiles.push(OfflineProfileOption {
            id: offline_profile_id.clone(),
            label,
            profile: offline_profile.clone(),
        });
    }

    state.selected_offline_profile_id = offline_profile_id;
    state.offline_profile = offline_profile;
    state = normalize_profile_state(state, &config);
    save_profile_state(&app, &config, &state)?;

    Ok(state)
}

/// Initialise or restore an MLS session.
///
/// Returns:
/// - `true` if a previous session was restored or already existed in memory
/// - `false` if a fresh group was created
#[tauri::command]
fn mls_init_session(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    room: String,
    identity: String,
) -> Result<bool, String> {
    let config = active_config(&config)?;
    let key = session_key(&config.profile, &room, &identity);
    let mut map = sessions.lock().map_err(|e| e.to_string())?;

    if map.contains_key(&key) {
        return Ok(true);
    }

    if let Some(session) = restore(&app, &config, &room, &identity) {
        map.insert(key, session);
        return Ok(true);
    }

    let mut session = EnigmaSession::new(room.clone(), identity.clone())?;
    session.create_group()?;

    persist(&app, &config, &session, &room, &identity);
    map.insert(key, session);

    Ok(false)
}

/// Generate a fresh key package for this session.
#[tauri::command]
fn mls_key_package(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    room: String,
    identity: String,
) -> Result<Vec<u8>, String> {
    let config = active_config(&config)?;
    let key = session_key(&config.profile, &room, &identity);
    let mut map = sessions.lock().map_err(|e| e.to_string())?;

    let session = map
        .get_mut(&key)
        .ok_or_else(|| "session not initialised".to_string())?;

    let kp = session.key_package()?;

    persist(&app, &config, session, &room, &identity);

    Ok(kp)
}

/// Add a remote peer using their key package bytes.
///
/// Returns:
/// - `welcome_bytes`
/// - `commit_bytes`
#[tauri::command]
fn mls_add_member(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    room: String,
    identity: String,
    key_package: Vec<u8>,
) -> Result<(Vec<u8>, Vec<u8>), String> {
    let config = active_config(&config)?;
    let key = session_key(&config.profile, &room, &identity);
    let mut map = sessions.lock().map_err(|e| e.to_string())?;

    let session = map
        .get_mut(&key)
        .ok_or_else(|| "session not initialised".to_string())?;

    let result = session.add_member(&key_package)?;

    persist(&app, &config, session, &room, &identity);

    Ok(result)
}

/// Join a group using a Welcome message received from the group creator.
#[tauri::command]
fn mls_join_from_welcome(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    room: String,
    identity: String,
    welcome: Vec<u8>,
) -> Result<(), String> {
    let config = active_config(&config)?;
    let key = session_key(&config.profile, &room, &identity);
    let mut map = sessions.lock().map_err(|e| e.to_string())?;

    let session = map
        .get_mut(&key)
        .ok_or_else(|| "session not initialised".to_string())?;

    session.join_from_welcome(&welcome)?;

    persist(&app, &config, session, &room, &identity);

    Ok(())
}

/// Process an opaque MLS message: commit, proposal, or application message.
///
/// Returns:
/// - `Some(plaintext)` for application messages
/// - `None` for commits/proposals/control messages
#[tauri::command]
fn mls_process_message(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    room: String,
    identity: String,
    bytes: Vec<u8>,
) -> Result<Option<Vec<u8>>, String> {
    let config = active_config(&config)?;
    let key = session_key(&config.profile, &room, &identity);
    let mut map = sessions.lock().map_err(|e| e.to_string())?;

    let session = map
        .get_mut(&key)
        .ok_or_else(|| "session not initialised".to_string())?;

    let plaintext = session.process_message(&bytes)?;

    persist(&app, &config, session, &room, &identity);

    Ok(plaintext)
}

/// Encrypt a plaintext application message.
///
/// The plaintext is zeroized in Rust memory immediately after encryption.
#[tauri::command]
fn mls_encrypt(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    room: String,
    identity: String,
    mut plaintext: Vec<u8>,
) -> Result<Vec<u8>, String> {
    let config = active_config(&config)?;
    let key = session_key(&config.profile, &room, &identity);
    let mut map = sessions.lock().map_err(|e| e.to_string())?;

    let session = map
        .get_mut(&key)
        .ok_or_else(|| "session not initialised".to_string())?;

    let ciphertext = session.encrypt_app(&plaintext)?;

    plaintext.zeroize();

    persist(&app, &config, session, &room, &identity);

    Ok(ciphertext)
}

/// Remove a session from memory and wipe its on-disk state.
#[tauri::command]
fn mls_clear_state(
    app: AppHandle,
    config: State<AppConfigState>,
    sessions: State<Sessions>,
    room: String,
    identity: String,
) -> Result<(), String> {
    let config = active_config(&config)?;
    let key = session_key(&config.profile, &room, &identity);

    if let Ok(mut map) = sessions.lock() {
        map.remove(&key);
    }

    wipe_file(&app, &config, &room, &identity);
    Ok(())
}

// ── App entry point ───────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run(config: AppConfig) {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppConfigState::new(config))
        .manage(Sessions::default())
        .invoke_handler(tauri::generate_handler![
            current_profile,
            current_identity,
            app_get_runtime_config,
            app_set_active_profile,
            app_get_profile_state,
            app_set_server_url,
            app_get_server_url,
            app_set_auth_token,
            app_get_auth_token,
            app_clear_auth_token,
            app_clear_profile_state,
            app_get_offline_profile,
            app_set_offline_profile,
            app_clear_offline_profile,
            app_set_selected_offline_profile,
            app_load_offline_profile,
            app_upsert_offline_profile,
            mls_init_session,
            mls_key_package,
            mls_add_member,
            mls_join_from_welcome,
            mls_process_message,
            mls_encrypt,
            mls_clear_state,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
