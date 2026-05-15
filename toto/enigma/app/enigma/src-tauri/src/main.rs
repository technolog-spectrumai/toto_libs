#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let profile = std::env::var("APP_PROFILE").unwrap_or_else(|_| "default".to_string());

    let identity = std::env::var("CHAT_USER").unwrap_or_else(|_| profile.clone());

    let config = enigma_lib::AppConfig { profile, identity };

    enigma_lib::run(config)
}
