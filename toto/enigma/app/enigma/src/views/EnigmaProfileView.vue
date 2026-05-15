<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { storeToRefs } from "pinia";
import { useUserStore } from "../stores/user";
import { useChatStore } from "../stores/chat";
import { getAuthToken, getServerUrl } from "../services/appStorage";
import { api } from "../services/api";
import OyaCard from "../components/OyaCard.vue";
import OyaButton from "../components/OyaButton.vue";
import { useRouter } from "vue-router";

defineProps<{ darkMode: boolean }>();

const router = useRouter();
const userStore = useUserStore();
const chatStore = useChatStore();
const {
  me,
  loading,
  error,
  fetchLog,
  offlineProfile,
  offlineProfiles,
  selectedOfflineProfileId,
} = storeToRefs(userStore);
const { rooms, loadingRooms } = storeToRefs(chatStore);

const showFetchLog = ref(false);
const showDetail = ref<string | null>(null);
const savedOffline = ref(false);
const clearedOffline = ref(false);
const downloadingAvatar = ref(false);
const clearingAllData = ref(false);
const clearAllError = ref("");
const profileUrlBase = ref("");
const newOfflineProfileLabel = ref("");

const selectedOfflineProfileLabel = computed(() =>
  offlineProfiles.value.find((profile) => profile.id === selectedOfflineProfileId.value)?.label
  ?? "Selected profile"
);

async function downloadAvatar() {
  if (!me.value?.avatar_url || downloadingAvatar.value) return;
  downloadingAvatar.value = true;
  try {
    const token = await getAuthToken();
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const res = await fetch(me.value.avatar_url, { credentials: "include", headers });
    if (!res.ok) throw new Error(`${res.status}`);

    const blob = await res.blob();
    const ext = blob.type.split("/")[1] ?? "jpg";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${me.value.username}-avatar.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error("Avatar download failed:", e);
  } finally {
    downloadingAvatar.value = false;
  }
}

onMounted(async () => {
  profileUrlBase.value = (await getServerUrl()).replace(/\/$/, "");
  await userStore.loadOfflineProfile();
  void userStore.fetchMe();
  if (!rooms.value.length) chatStore.fetchRooms();
});

async function saveOffline() {
  if (!offlineProfile.value.display_name.trim()) return;
  await userStore.saveOfflineProfile();
  savedOffline.value = true;
  setTimeout(() => (savedOffline.value = false), 2000);
}

async function chooseOfflineProfile(event: Event) {
  const id = (event.target as HTMLSelectElement).value;
  if (!id || id === selectedOfflineProfileId.value) return;
  await userStore.selectOfflineProfile(id);
}

async function saveAsNewOfflineProfile() {
  const label = newOfflineProfileLabel.value.trim();
  if (!label) return;
  const id = crypto.randomUUID?.() ?? `${Date.now()}`;
  await userStore.saveOfflineProfileAs(id, label);
  newOfflineProfileLabel.value = "";
  savedOffline.value = true;
  setTimeout(() => (savedOffline.value = false), 2000);
}

async function clearOfflineData() {
  await userStore.clearSelectedOfflineProfile();
  clearedOffline.value = true;
  savedOffline.value = false;
  setTimeout(() => (clearedOffline.value = false), 2000);
}

async function clearAllDataAndLogout() {
  if (clearingAllData.value) return;

  clearingAllData.value = true;
  clearAllError.value = "";
  try {
    await chatStore.leaveAllChatsOnServer().catch(() => {});
    await api.logout().catch(() => {});
    await userStore.clearAllProfileData();
    chatStore.clearLocalChatState("Cleared profile data, left all chats, and logged out.");
    router.push("/login");
  } catch (e) {
    clearAllError.value = (e as Error).message;
  } finally {
    clearingAllData.value = false;
  }
}
</script>

<template>
  <section class="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6">

    <!-- Header -->
    <header class="space-y-2">
      <button
        class="inline-flex items-center gap-2 text-sm underline hover:opacity-80"
        @click="router.push('/')"
      >
        <font-awesome-icon icon="arrow-left" />
        Back
      </button>

      <div>
        <p
          :class="darkMode ? 'text-accent-dark' : 'text-accent-light'"
          class="text-xs font-semibold uppercase tracking-wide"
        >
          Oya Studio
        </p>
        <h1 class="mt-1 flex items-center gap-3 text-3xl font-bold">
          <font-awesome-icon icon="user" />
          My Profile
        </h1>
      </div>
    </header>

    <!-- Loading -->
    <OyaCard v-if="loading" :dark-mode="darkMode" :padded="true">
      <p class="text-sm opacity-50">Loading profile…</p>
    </OyaCard>

    <!-- ── Online profile ── -->
    <OyaCard v-else-if="me" :dark-mode="darkMode" :padded="true">
      <div class="flex flex-col gap-5 sm:flex-row sm:items-start">

        <!-- Avatar -->
        <div class="flex shrink-0 flex-col items-center gap-2">
          <div
            :class="darkMode ? 'border-accent-1 bg-primary-bg-dark' : 'border-accent-2 bg-primary-bg-light'"
            class="flex h-24 w-24 items-center justify-center overflow-hidden rounded-2xl border shadow-md"
          >
            <img
              v-if="me.avatar_url"
              :src="me.avatar_url"
              :alt="me.full_name"
              class="h-full w-full object-cover"
            />
            <font-awesome-icon v-else icon="user" class="text-4xl opacity-30" />
          </div>
          <button
            v-if="me.avatar_url"
            :class="{ 'opacity-40 pointer-events-none': downloadingAvatar }"
            class="inline-flex items-center gap-1.5 text-xs opacity-50 hover:opacity-90 transition"
            @click="downloadAvatar"
          >
            <font-awesome-icon :icon="downloadingAvatar ? 'lock' : 'arrow-down'" />
            {{ downloadingAvatar ? "Saving…" : "Download" }}
          </button>
        </div>

        <!-- Info -->
        <div class="flex-1 space-y-3">
          <div>
            <h2 class="text-2xl font-bold">{{ me.full_name }}</h2>
            <p class="mt-0.5 text-sm opacity-60">@{{ me.username }}</p>
          </div>

          <div
            :class="darkMode ? 'border-accent-1 bg-primary-bg-dark divide-accent-1' : 'border-accent-2 bg-primary-bg-light divide-accent-2'"
            class="divide-y rounded-xl border"
          >
            <div class="flex items-center justify-between px-4 py-3 text-sm">
              <span class="opacity-60">User ID</span>
              <span class="font-mono font-semibold">{{ me.id }}</span>
            </div>
            <div class="flex items-center justify-between px-4 py-3 text-sm">
              <span class="opacity-60">Username</span>
              <span class="font-semibold">{{ me.username }}</span>
            </div>
            <div class="flex items-center justify-between px-4 py-3 text-sm">
              <span class="opacity-60">Full name</span>
              <span class="font-semibold">{{ me.full_name }}</span>
            </div>
          </div>

          <a
            v-if="me.profile_url"
            :href="`${profileUrlBase}${me.profile_url}`"
            target="_blank"
            rel="noopener noreferrer"
            :class="darkMode ? 'border-accent-1 hover:bg-primary-bg-dark' : 'border-accent-2 hover:bg-primary-bg-light'"
            class="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold transition"
          >
            <font-awesome-icon icon="arrow-left" class="rotate-[135deg]" />
            View full profile on server
          </a>
        </div>
      </div>
    </OyaCard>

    <!-- ── Offline profile form ── -->
    <template>

      <!-- Auth error notice -->
      <div
        v-if="!me && error"
        :class="darkMode ? 'border-warn-dark/30 bg-warn-dark/5' : 'border-warn-light/30 bg-warn-light/5'"
        class="flex items-start gap-3 rounded-xl border px-4 py-3 text-sm"
      >
        <font-awesome-icon icon="triangle-exclamation" class="mt-0.5 shrink-0 text-warn-dark" />
        <div class="min-w-0 flex-1">
          <p class="font-semibold text-warn-dark">Not connected to server</p>
          <p class="mt-0.5 break-all font-mono text-xs opacity-70">{{ error }}</p>
        </div>
        <div class="flex shrink-0 gap-2">
          <OyaButton :dark-mode="darkMode" variant="secondary" @click="userStore.fetchMe()">
            Retry
          </OyaButton>
          <OyaButton :dark-mode="darkMode" variant="secondary" @click="router.push('/login')">
            Login
          </OyaButton>
        </div>
      </div>

      <!-- Offline profile form card -->
      <OyaCard :dark-mode="darkMode" :padded="true" class="space-y-5">
        <div>
          <div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p
                :class="darkMode ? 'text-accent-dark' : 'text-accent-light'"
                class="text-xs font-semibold uppercase tracking-wide"
              >
                Offline Profile
              </p>
              <p class="mt-1 text-sm opacity-60">
                Stored locally on this device. Used while disconnected from the server.
              </p>
            </div>

            <label class="w-full space-y-1 sm:w-64">
              <span class="text-xs font-semibold uppercase tracking-wide opacity-60">
                Use data from
              </span>
              <select
                :value="selectedOfflineProfileId"
                :class="
                  darkMode
                    ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark'
                    : 'bg-primary-bg-light border-accent-2 text-text-main-light'
                "
                class="w-full rounded-lg border px-3 py-2 text-sm font-semibold outline-none transition"
                @change="chooseOfflineProfile"
              >
                <option
                  v-for="profile in offlineProfiles"
                  :key="profile.id"
                  :value="profile.id"
                >
                  {{ profile.label }}
                </option>
              </select>
            </label>
          </div>
        </div>

        <div class="space-y-4">

          <!-- Display name (required) -->
          <div class="space-y-1">
            <label class="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide opacity-60">
              Display name
              <span class="text-warn-dark">*</span>
            </label>
            <input
              v-model="offlineProfile.display_name"
              type="text"
              placeholder="Your name"
              maxlength="150"
              :class="
                darkMode
                  ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/30'
                  : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/30'
              "
              class="w-full rounded-lg border px-3 py-2 text-sm outline-none transition"
            />
          </div>

          <!-- Email -->
          <div class="space-y-1">
            <label class="text-xs font-semibold uppercase tracking-wide opacity-60">Email</label>
            <input
              v-model="offlineProfile.email"
              type="email"
              placeholder="you@example.com"
              :class="
                darkMode
                  ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/30'
                  : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/30'
              "
              class="w-full rounded-lg border px-3 py-2 text-sm outline-none transition"
            />
          </div>

          <!-- Phone -->
          <div class="space-y-1">
            <label class="text-xs font-semibold uppercase tracking-wide opacity-60">Phone</label>
            <input
              v-model="offlineProfile.phone"
              type="tel"
              placeholder="+1 555 000 0000"
              :class="
                darkMode
                  ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/30'
                  : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/30'
              "
              class="w-full rounded-lg border px-3 py-2 text-sm outline-none transition"
            />
          </div>

          <!-- Date of birth -->
          <div class="space-y-1">
            <label class="text-xs font-semibold uppercase tracking-wide opacity-60">Date of birth</label>
            <input
              v-model="offlineProfile.date_of_birth"
              type="date"
              :class="
                darkMode
                  ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark'
                  : 'bg-primary-bg-light border-accent-2 text-text-main-light'
              "
              class="w-full rounded-lg border px-3 py-2 text-sm outline-none transition"
            />
          </div>

          <!-- Bio -->
          <div class="space-y-1">
            <label class="text-xs font-semibold uppercase tracking-wide opacity-60">Bio</label>
            <textarea
              v-model="offlineProfile.bio"
              rows="3"
              placeholder="A few words about yourself…"
              :class="
                darkMode
                  ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/30'
                  : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/30'
              "
              class="w-full resize-none rounded-lg border px-3 py-2 text-sm outline-none transition"
            />
          </div>

        </div>

        <div class="flex flex-col gap-2 sm:flex-row">
          <OyaButton
            :dark-mode="darkMode"
            :class="{ 'pointer-events-none opacity-50': !offlineProfile.display_name.trim() }"
            class="flex-1 justify-center"
            @click="saveOffline"
          >
            <font-awesome-icon :icon="savedOffline ? 'check' : 'floppy-disk'" />
            {{ savedOffline ? "Saved locally" : `Save ${selectedOfflineProfileLabel}` }}
          </OyaButton>

          <OyaButton
            :dark-mode="darkMode"
            variant="secondary"
            class="justify-center"
            @click="clearOfflineData"
          >
            <font-awesome-icon :icon="clearedOffline ? 'check' : 'trash'" />
            {{ clearedOffline ? "Cleared" : "Clear data" }}
          </OyaButton>
        </div>

        <div class="flex flex-col gap-2 sm:flex-row">
          <input
            v-model="newOfflineProfileLabel"
            type="text"
            placeholder="New offline profile name"
            maxlength="80"
            :class="
              darkMode
                ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/30'
                : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/30'
            "
            class="min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm outline-none transition"
          />
          <OyaButton
            :dark-mode="darkMode"
            variant="secondary"
            :class="{ 'pointer-events-none opacity-50': !newOfflineProfileLabel.trim() }"
            class="justify-center"
            @click="saveAsNewOfflineProfile"
          >
            <font-awesome-icon icon="plus" />
            Add profile
          </OyaButton>
        </div>
      </OyaCard>

      <!-- Fetch log (collapsible) -->
      <div v-if="fetchLog.length">
        <button
          class="flex w-full items-center gap-2 text-xs font-semibold uppercase tracking-wide opacity-40 hover:opacity-70 transition"
          @click="showFetchLog = !showFetchLog"
        >
          <font-awesome-icon
            icon="arrow-left"
            class="transition-transform"
            :class="showFetchLog ? '-rotate-90' : 'rotate-[225deg]'"
          />
          Connection log
        </button>

        <div v-if="showFetchLog" class="mt-2 space-y-1">
          <div
            v-for="(entry, i) in [...fetchLog].reverse()"
            :key="i"
            :class="
              entry.ok
                ? darkMode ? 'border-success-dark/40' : 'border-success-light/40'
                : darkMode ? 'border-warn-dark/40' : 'border-warn-light/40'
            "
            class="rounded-lg border font-mono text-xs"
          >
            <button
              class="flex w-full items-center gap-3 px-3 py-2 text-left"
              @click="showDetail = showDetail === `${i}` ? null : `${i}`"
            >
              <span
                :class="entry.ok ? 'text-success-dark' : 'text-warn-dark'"
                class="w-6 shrink-0 text-center font-bold"
              >
                {{ entry.ok ? "✓" : "✗" }}
              </span>
              <span class="shrink-0 opacity-50">{{ entry.timestamp.toLocaleTimeString() }}</span>
              <span class="min-w-0 flex-1 truncate opacity-80">{{ entry.url }}</span>
              <span class="shrink-0 opacity-60">{{ entry.message }}</span>
              <font-awesome-icon
                icon="arrow-left"
                class="shrink-0 opacity-40 transition-transform"
                :class="showDetail === `${i}` ? '-rotate-90' : 'rotate-[225deg]'"
              />
            </button>
            <div
              v-if="showDetail === `${i}` && entry.detail"
              :class="darkMode ? 'bg-primary-bg-dark' : 'bg-primary-bg-light'"
              class="border-t border-current/10 px-3 py-2"
            >
              <pre class="whitespace-pre-wrap break-all text-[10px] opacity-70">{{ entry.detail }}</pre>
            </div>
          </div>
        </div>
      </div>

    </template>

    <!-- Rooms card (only when authenticated) -->
    <OyaCard v-if="me" :dark-mode="darkMode" :padded="true">
      <p
        :class="darkMode ? 'text-accent-dark' : 'text-accent-light'"
        class="text-xs font-semibold uppercase tracking-wide"
      >
        Rooms
      </p>

      <div v-if="loadingRooms" class="mt-3 text-sm opacity-50">Loading rooms…</div>

      <div
        v-else-if="rooms.length"
        class="mt-3 divide-y"
        :class="darkMode ? 'divide-accent-1' : 'divide-accent-2'"
      >
        <div
          v-for="room in rooms"
          :key="room.slug"
          class="flex items-center justify-between py-3"
        >
          <div>
            <p class="text-sm font-semibold">{{ room.name }}</p>
            <p class="text-xs opacity-50">{{ room.participant_count }} participant{{ room.participant_count === 1 ? '' : 's' }}</p>
          </div>
          <OyaButton :dark-mode="darkMode" variant="secondary" @click="router.push('/chat')">
            <font-awesome-icon icon="comments" />
            Open
          </OyaButton>
        </div>
      </div>

      <p v-else class="mt-3 text-sm opacity-50">No rooms available.</p>
    </OyaCard>

    <!-- Danger zone -->
    <OyaCard :dark-mode="darkMode" :padded="true" class="space-y-4">
      <div>
        <p class="text-xs font-semibold uppercase tracking-wide text-warn-dark">
          Danger zone
        </p>
        <p class="mt-1 text-sm opacity-60">
          Clear local profile data, leave chats on the server, log out, and return to login.
        </p>
      </div>

      <div
        v-if="clearAllError"
        class="rounded-lg border border-warn-dark/30 bg-warn-dark/10 px-3 py-2 text-xs text-warn-dark"
      >
        {{ clearAllError }}
      </div>

      <OyaButton
        :dark-mode="darkMode"
        variant="secondary"
        :class="{ 'pointer-events-none opacity-50': clearingAllData }"
        class="justify-center border-warn-dark/50 text-warn-dark"
        @click="clearAllDataAndLogout"
      >
        <font-awesome-icon :icon="clearingAllData ? 'lock' : 'trash'" />
        {{ clearingAllData ? "Clearing…" : "Clear all data and logout" }}
      </OyaButton>
    </OyaCard>

  </section>
</template>
