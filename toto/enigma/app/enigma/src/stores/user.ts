import { ref } from "vue";
import { defineStore } from "pinia";
import { api, type Me } from "../services/api";
import {
  getServerUrl,
  getProfileState,
  setOfflineProfile,
  clearOfflineProfile,
  clearProfileState,
  setSelectedOfflineProfile,
  upsertOfflineProfile,
  type OfflineProfile,
  type OfflineProfileOption,
  type AppProfileState,
} from "../services/appStorage";

export interface FetchLogEntry {
  timestamp: Date;
  url: string;
  ok: boolean;
  message: string;
  detail?: string;
}

const EMPTY_PROFILE: OfflineProfile = {
  display_name: "",
  email: "",
  phone: "",
  bio: "",
  date_of_birth: "",
};

export const useUserStore = defineStore("user", () => {
  const me = ref<Me | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const fetchLog = ref<FetchLogEntry[]>([]);

  const offlineProfile = ref<OfflineProfile>({ ...EMPTY_PROFILE });
  const offlineProfiles = ref<OfflineProfileOption[]>([]);
  const selectedOfflineProfileId = ref("");
  const offlineProfileLoaded = ref(false);

  function applyProfileState(state: AppProfileState) {
    offlineProfile.value = {
      ...EMPTY_PROFILE,
      ...state.offline_profile,
    };
    offlineProfiles.value = state.offline_profiles;
    selectedOfflineProfileId.value = state.selected_offline_profile_id;
  }

  async function loadOfflineProfile() {
    applyProfileState(await getProfileState());
    offlineProfileLoaded.value = true;
  }

  async function selectOfflineProfile(id: string) {
    applyProfileState(await setSelectedOfflineProfile(id));
    offlineProfileLoaded.value = true;
  }

  async function serverBase(): Promise<string> {
    return (await getServerUrl()).replace(/\/$/, "");
  }

  async function fetchMe() {
    loading.value = true;
    error.value = null;
    const base = await serverBase();
    const url = `${base}/enigma/api/me/`;
    const ts = new Date();

    try {
      me.value = await api.getMe();
      fetchLog.value.push({ timestamp: ts, url, ok: true, message: `OK — ${me.value.full_name}` });
    } catch (e) {
      const msg = (e as Error).message;
      error.value = msg;
      fetchLog.value.push({
        timestamp: ts,
        url,
        ok: false,
        message: msg,
        detail: (e as Error).stack,
      });
    } finally {
      loading.value = false;
    }
  }

  async function saveOfflineProfile() {
    await setOfflineProfile(offlineProfile.value);
    await loadOfflineProfile();
  }

  async function saveOfflineProfileAs(id: string, label: string) {
    applyProfileState(await upsertOfflineProfile(id, label, offlineProfile.value));
    offlineProfileLoaded.value = true;
  }

  async function clearSelectedOfflineProfile() {
    applyProfileState(await clearOfflineProfile());
    offlineProfileLoaded.value = true;
  }

  async function clearAllProfileData() {
    applyProfileState(await clearProfileState());
    me.value = null;
    error.value = null;
    fetchLog.value = [];
    offlineProfileLoaded.value = true;
  }

  return {
    me,
    loading,
    error,
    fetchLog,
    offlineProfile,
    offlineProfiles,
    selectedOfflineProfileId,
    offlineProfileLoaded,
    loadOfflineProfile,
    selectOfflineProfile,
    fetchMe,
    saveOfflineProfile,
    saveOfflineProfileAs,
    clearSelectedOfflineProfile,
    clearAllProfileData,
  };
});
