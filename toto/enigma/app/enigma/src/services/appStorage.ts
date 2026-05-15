import { invoke } from "@tauri-apps/api/core";

export interface OfflineProfile {
  display_name: string;
  email: string;
  phone: string;
  bio: string;
  date_of_birth: string;
}

export interface AppProfileState {
  server_url: string;
  auth_token: string | null;
  offline_profile: OfflineProfile;
  offline_profiles: OfflineProfileOption[];
  selected_offline_profile_id: string;
}

export interface OfflineProfileOption {
  id: string;
  label: string;
  profile: OfflineProfile;
}

export interface AppRuntimeConfig {
  profile: string;
  identity: string;
  profile_state_path: string;
}

export async function getRuntimeConfig(): Promise<AppRuntimeConfig> {
  return await invoke<AppRuntimeConfig>("app_get_runtime_config");
}

export async function setActiveProfile(
  profile: string,
  identity?: string,
): Promise<AppRuntimeConfig> {
  return await invoke<AppRuntimeConfig>("app_set_active_profile", {
    profile,
    identity,
  });
}

export async function getProfileState(): Promise<AppProfileState> {
  return await invoke<AppProfileState>("app_get_profile_state");
}

export async function getServerUrl(): Promise<string> {
  return await invoke<string>("app_get_server_url");
}

export async function setServerUrl(serverUrl: string): Promise<void> {
  await invoke("app_set_server_url", { serverUrl });
}

export async function getAuthToken(): Promise<string | null> {
  return await invoke<string | null>("app_get_auth_token");
}

export async function setAuthToken(authToken: string): Promise<void> {
  await invoke("app_set_auth_token", { authToken });
}

export async function clearAuthToken(): Promise<void> {
  await invoke("app_clear_auth_token");
}

export async function clearProfileState(): Promise<AppProfileState> {
  return await invoke<AppProfileState>("app_clear_profile_state");
}

export async function getOfflineProfile(): Promise<OfflineProfile> {
  return await invoke<OfflineProfile>("app_get_offline_profile");
}

export async function setOfflineProfile(
  offlineProfile: OfflineProfile,
): Promise<void> {
  await invoke("app_set_offline_profile", { offlineProfile });
}

export async function clearOfflineProfile(): Promise<AppProfileState> {
  return await invoke<AppProfileState>("app_clear_offline_profile");
}

export async function setSelectedOfflineProfile(
  selectedOfflineProfileId: string,
): Promise<AppProfileState> {
  return await invoke<AppProfileState>("app_set_selected_offline_profile", {
    selectedOfflineProfileId,
  });
}

export async function loadOfflineProfile(label: string): Promise<AppProfileState> {
  return await invoke<AppProfileState>("app_load_offline_profile", { label });
}

export async function upsertOfflineProfile(
  offlineProfileId: string,
  label: string,
  offlineProfile: OfflineProfile,
): Promise<AppProfileState> {
  return await invoke<AppProfileState>("app_upsert_offline_profile", {
    offlineProfileId,
    label,
    offlineProfile,
  });
}
