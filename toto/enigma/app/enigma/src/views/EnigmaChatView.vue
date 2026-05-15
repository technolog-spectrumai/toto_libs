<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue";
import { useRouter } from "vue-router";
import { storeToRefs } from "pinia";
import { useChatStore } from "../stores/chat";
import OyaButton from "../components/OyaButton.vue";
import ChatBubble from "../components/ChatBubble.vue";

defineProps<{ darkMode: boolean }>();

const router = useRouter();
const chat = useChatStore();

const {
  rooms,
  currentRoom,
  messages,
  participants,
  wsStatus,
  loadingRooms,
  error,
  isConnected,
  mlsStatus,
  mlsError,
  isEncrypted,
  isParticipant,
} = storeToRefs(chat);

const draft = ref("");
const messagesEl = ref<HTMLElement | null>(null);
const sidebarOpen = ref(false);
const joiningOrLeaving = ref(false);

onMounted(() => {
  if (!rooms.value.length) chat.fetchRooms();
});

watch(currentRoom, () => {
  sidebarOpen.value = false;
});

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
    }
  });
}

watch(messages, scrollToBottom, { deep: true });

function send() {
  if (!currentRoom.value) return;
  if (!isConnected.value) return;
  if (!isParticipant.value) return;
  if (!draft.value.trim()) return;

  chat.sendMessage(draft.value);
  draft.value = "";
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

async function handleJoin() {
  joiningOrLeaving.value = true;
  try {
    await chat.joinParticipant();
  } finally {
    joiningOrLeaving.value = false;
  }
}

async function handleLeave() {
  joiningOrLeaving.value = true;
  try {
    await chat.leaveParticipant();
  } finally {
    joiningOrLeaving.value = false;
  }
}

// ── Connection chip ───────────────────────────────────────────────────────────
const wsLabel = computed(
  () =>
    ({
      connected: "Connected",
      connecting: "Connecting…",
      disconnected: "Offline",
      error: "Error",
    })[wsStatus.value] ?? wsStatus.value,
);

const wsChipClass = computed(() => {
  if (wsStatus.value === "connected") {
    return "bg-success-dark text-primary-bg-dark border-success-dark";
  }

  if (wsStatus.value === "connecting") {
    return "border-current/20 animate-pulse";
  }

  if (wsStatus.value === "error") {
    return "bg-warn-dark text-primary-bg-dark border-warn-dark";
  }

  return "border-current/20 opacity-60";
});

// ── MLS encryption chip ───────────────────────────────────────────────────────
const mlsLabel = computed(
  () =>
    ({
      idle: "Not encrypted",
      initializing: "Initializing…",
      key_exchange: "Key exchange…",
      ready: "Encrypted",
      error: "Enc. error",
    })[mlsStatus.value] ?? mlsStatus.value,
);

const mlsChipClass = computed(() => {
  if (mlsStatus.value === "ready") {
    return "bg-success-dark text-primary-bg-dark border-success-dark";
  }

  if (mlsStatus.value === "initializing" || mlsStatus.value === "key_exchange") {
    return "border-current/20 animate-pulse";
  }

  if (mlsStatus.value === "error") {
    return "bg-warn-dark text-primary-bg-dark border-warn-dark";
  }

  return "border-current/20 opacity-50";
});
</script>

<template>
  <!-- ── Full-height layout ───────────────────────────────────────────────── -->
  <div
    class="mx-auto grid h-[calc(100vh-8rem)] w-full max-w-7xl flex-1 gap-4 overflow-hidden lg:grid-cols-[16rem_minmax(0,1fr)_16rem]"
  >
    <!-- ── ROOMS sidebar desktop ───────────────────────────────────────────── -->
    <aside
      :class="darkMode ? 'bg-bubble-bg-dark border-accent-1' : 'bg-bubble-bg-light border-accent-2'"
      class="hidden min-h-0 flex-col overflow-hidden rounded-lg border shadow-md lg:flex"
    >
      <div class="flex items-center justify-between border-b border-current/10 p-4">
        <h2 class="text-sm font-bold">
          <font-awesome-icon icon="comments" class="mr-2" />
          Rooms
        </h2>

        <button
          class="text-xs underline opacity-70 hover:opacity-100"
          @click="router.push('/')"
        >
          ← Back
        </button>
      </div>

      <div v-if="loadingRooms" class="p-4 text-xs opacity-50">
        Loading…
      </div>

      <nav v-else class="flex-1 space-y-2 overflow-y-auto p-4 pr-3">
        <button
          v-for="room in rooms"
          :key="room.slug"
          :class="
            currentRoom?.slug === room.slug
              ? darkMode
                ? 'bg-primary-bg-dark border-accent-dark'
                : 'bg-primary-bg-light border-accent-light'
              : darkMode
                ? 'border-accent-1 hover:bg-primary-bg-dark'
                : 'border-accent-2 hover:bg-primary-bg-light'
          "
          class="w-full rounded-lg border px-3 py-3 text-left transition"
          @click="chat.joinRoom(room.slug)"
        >
          <span class="block truncate text-sm font-semibold">
            {{ room.name }}
          </span>

          <span class="mt-1 block text-xs opacity-60">
            {{ room.participant_count }} participant{{ room.participant_count === 1 ? "" : "s" }}
          </span>
        </button>

        <p v-if="!rooms.length" class="text-xs opacity-50">
          No rooms found.
        </p>
      </nav>
    </aside>

    <!-- ── CHATBOX ─────────────────────────────────────────────────────────── -->
    <section
      :class="darkMode ? 'bg-bubble-bg-dark border-accent-1' : 'bg-bubble-bg-light border-accent-2'"
      class="grid min-h-0 grid-rows-[auto_auto_auto_minmax(0,1fr)_auto] overflow-hidden rounded-lg border shadow-md"
    >
      <!-- Header row -->
      <header class="flex min-w-0 flex-wrap items-center gap-3 border-b border-current/10 p-4">
        <!-- Mobile rooms button.
             IMPORTANT: this must show even when no room is selected. -->
        <button
          type="button"
          :class="darkMode ? 'border-accent-1' : 'border-accent-2'"
          class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border lg:hidden"
          aria-label="Open rooms"
          @click="sidebarOpen = true"
        >
          <font-awesome-icon icon="bars" />
        </button>

        <div class="min-w-0 flex-1">
          <p
            :class="darkMode ? 'text-accent-dark' : 'text-accent-light'"
            class="text-xs font-semibold uppercase tracking-wide"
          >
            {{ currentRoom ? "Active room" : "No room selected" }}
          </p>

          <h2 class="mt-1 truncate text-2xl font-bold">
            {{ currentRoom?.name ?? "Select a room" }}
          </h2>

          <div
            v-if="currentRoom"
            class="mt-1 flex flex-wrap gap-3 text-xs opacity-70"
          >
            <span>
              {{ currentRoom.participant_count }} participant{{ currentRoom.participant_count === 1 ? "" : "s" }}
            </span>

            <span>
              {{ isParticipant ? "Joined" : "Observer" }}
            </span>
          </div>
        </div>

        <!-- Encryption chip desktop -->
        <span
          v-if="currentRoom && isParticipant"
          :class="mlsChipClass"
          :title="mlsError ?? undefined"
          class="hidden items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold shadow-sm transition sm:inline-flex"
        >
          <font-awesome-icon :icon="isEncrypted ? 'lock' : 'lock-open'" />
          {{ mlsLabel }}
        </span>

        <!-- Connection chip desktop -->
        <span
          v-if="currentRoom && isParticipant"
          :class="wsChipClass"
          class="hidden items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold shadow-sm transition sm:inline-flex"
        >
          <font-awesome-icon :icon="wsStatus === 'connected' ? 'check' : 'xmark'" />
          {{ wsLabel }}
        </span>

        <!-- Join / Leave button desktop -->
        <template v-if="currentRoom">
          <OyaButton
            v-if="!isParticipant"
            :dark-mode="darkMode"
            :class="{ 'opacity-60 pointer-events-none': joiningOrLeaving }"
            class="hidden sm:inline-flex"
            @click="handleJoin"
          >
            <font-awesome-icon icon="right-to-bracket" />
            Join
          </OyaButton>

          <OyaButton
            v-else
            :dark-mode="darkMode"
            variant="secondary"
            :class="{ 'opacity-60 pointer-events-none': joiningOrLeaving }"
            class="hidden sm:inline-flex"
            @click="handleLeave"
          >
            <font-awesome-icon icon="right-from-bracket" />
            Leave
          </OyaButton>
        </template>
      </header>

      <!-- Observer banner -->
      <div
        v-if="currentRoom && !isParticipant"
        :class="darkMode ? 'bg-primary-bg-dark border-accent-1' : 'bg-primary-bg-light border-accent-2'"
        class="border-b px-4 py-3"
      >
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p class="text-sm opacity-75">
            You are observing. Join this room to send encrypted messages.
          </p>

          <OyaButton
            :dark-mode="darkMode"
            :class="{ 'opacity-60 pointer-events-none': joiningOrLeaving }"
            class="shrink-0"
            @click="handleJoin"
          >
            <font-awesome-icon icon="right-to-bracket" />
            Join Room
          </OyaButton>
        </div>
      </div>

      <!-- Mobile status strip -->
      <div
        v-if="currentRoom && isParticipant"
        :class="darkMode ? 'bg-primary-bg-dark border-accent-1' : 'bg-primary-bg-light border-accent-2'"
        class="flex flex-wrap items-center gap-2 border-b px-4 py-2 sm:hidden"
      >
        <span
          :class="mlsChipClass"
          :title="mlsError ?? undefined"
          class="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-bold transition"
        >
          <font-awesome-icon :icon="isEncrypted ? 'lock' : 'lock-open'" />
          {{ mlsLabel }}
        </span>

        <span
          :class="wsChipClass"
          class="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-bold transition"
        >
          <font-awesome-icon :icon="wsStatus === 'connected' ? 'check' : 'xmark'" />
          {{ wsLabel }}
        </span>

        <button
          :class="{ 'opacity-60 pointer-events-none': joiningOrLeaving }"
          class="ml-auto text-xs underline opacity-70 hover:opacity-100"
          @click="handleLeave"
        >
          Leave
        </button>
      </div>

      <!-- Error banner -->
      <div
        v-if="error"
        class="border-b border-warn-dark/30 bg-warn-dark/10 px-4 py-2 text-xs text-warn-dark"
      >
        <font-awesome-icon icon="triangle-exclamation" class="mr-1" />
        {{ error }}
      </div>

      <!-- Messages -->
      <div
        ref="messagesEl"
        :class="darkMode ? 'bg-primary-bg-dark' : 'bg-primary-bg-light'"
        class="min-h-0 space-y-4 overflow-y-auto p-4"
      >
        <div
          v-if="!currentRoom"
          class="flex h-full flex-col items-center justify-center gap-3 opacity-40"
        >
          <font-awesome-icon icon="comments" class="text-4xl" />

          <p class="text-sm">
            Pick a room from the sidebar to start chatting.
          </p>

          <OyaButton
            :dark-mode="darkMode"
            class="mt-2 lg:hidden"
            @click="sidebarOpen = true"
          >
            Select a room
          </OyaButton>
        </div>

        <ChatBubble
          v-for="msg in messages"
          :key="msg.id"
          :dark-mode="darkMode"
          :message="msg"
        />
      </div>

      <!-- Composer -->
      <div
        :class="darkMode ? 'bg-bubble-bg-dark' : 'bg-bubble-bg-light'"
        class="border-t border-current/10 p-4"
      >
        <div class="flex gap-3">
          <textarea
            v-model="draft"
            rows="1"
            :placeholder="
              !currentRoom
                ? 'Select a room to start chatting'
                : !isParticipant
                  ? 'Join this room to send messages'
                  : isEncrypted
                    ? 'Message (encrypted)…'
                    : 'Message…'
            "
            :disabled="!currentRoom || !isConnected || !isParticipant"
            :class="
              darkMode
                ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/30 disabled:opacity-30'
                : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/30 disabled:opacity-30'
            "
            class="max-h-32 min-h-[3rem] flex-1 resize-none rounded-lg border px-3 py-2.5 text-sm outline-none transition"
            @keydown="onKeydown"
          />

          <OyaButton
            :dark-mode="darkMode"
            :type="'button'"
            :disabled="!currentRoom || !isConnected || !isParticipant || !draft.trim()"
            :class="{
              'opacity-40 cursor-not-allowed':
                !currentRoom || !isConnected || !isParticipant || !draft.trim(),
            }"
            @click="send"
          >
            <font-awesome-icon icon="paper-plane" />
            Send
          </OyaButton>
        </div>
      </div>
    </section>

    <!-- ── PARTICIPANTS sidebar desktop ────────────────────────────────────── -->
    <aside
      :class="darkMode ? 'bg-bubble-bg-dark border-accent-1' : 'bg-bubble-bg-light border-accent-2'"
      class="hidden min-h-0 flex-col overflow-hidden rounded-lg border shadow-md lg:flex"
    >
      <div class="border-b border-current/10 p-4">
        <h2
          :class="darkMode ? 'text-accent-dark' : 'text-accent-light'"
          class="text-lg font-bold"
        >
          <font-awesome-icon icon="user" class="mr-2" />
          Participants
        </h2>
      </div>

      <div class="flex-1 space-y-3 overflow-y-auto p-4 pr-3">
        <div
          v-for="p in participants"
          :key="p.name"
          class="flex items-center gap-3"
        >
          <div
            :class="darkMode ? 'border-accent-1' : 'border-accent-2'"
            class="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border text-xs font-bold"
          >
            <img
              v-if="p.avatar_url"
              :src="p.avatar_url"
              :alt="p.name"
              class="h-full w-full object-cover"
            />

            <span v-else>
              {{ p.name.slice(0, 2).toUpperCase() }}
            </span>
          </div>

          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-semibold">
              {{ p.name }}
            </p>

            <p class="text-xs opacity-60">
              Human
            </p>
          </div>

          <span
            :class="darkMode ? 'bg-success-dark text-primary-bg-dark' : 'bg-success-light text-primary-bg-light'"
            class="rounded-full px-2 py-0.5 text-xs font-bold"
          >
            On
          </span>
        </div>

        <p v-if="!participants.length" class="text-xs opacity-40">
          No participants yet.
        </p>
      </div>
    </aside>
  </div>

  <!-- ── Mobile sidebar overlay ───────────────────────────────────────────── -->
  <Transition
    enter-active-class="transition-opacity duration-200"
    enter-from-class="opacity-0"
    enter-to-class="opacity-100"
    leave-active-class="transition-opacity duration-200"
    leave-from-class="opacity-100"
    leave-to-class="opacity-0"
  >
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-40 bg-black/40 lg:hidden"
      @click="sidebarOpen = false"
    />
  </Transition>

  <Transition
    enter-active-class="transition-transform duration-200"
    enter-from-class="-translate-x-full"
    enter-to-class="translate-x-0"
    leave-active-class="transition-transform duration-200"
    leave-from-class="translate-x-0"
    leave-to-class="-translate-x-full"
  >
    <aside
      v-if="sidebarOpen"
      :class="darkMode ? 'bg-bubble-bg-dark border-accent-1' : 'bg-bubble-bg-light border-accent-2'"
      class="fixed inset-y-0 left-0 z-50 flex w-72 flex-col border-r p-4 shadow-xl lg:hidden"
    >
      <div class="flex items-center justify-between border-b border-current/10 pb-3">
        <span class="text-sm font-bold">
          Rooms
        </span>

        <button
          type="button"
          :class="darkMode ? 'border-accent-1' : 'border-accent-2'"
          class="flex h-9 w-9 items-center justify-center rounded-lg border"
          aria-label="Close rooms"
          @click="sidebarOpen = false"
        >
          <font-awesome-icon icon="xmark" />
        </button>
      </div>

      <div v-if="loadingRooms" class="mt-4 text-xs opacity-50">
        Loading…
      </div>

      <nav v-else class="mt-4 flex-1 space-y-2 overflow-y-auto">
        <button
          v-for="room in rooms"
          :key="room.slug"
          :class="
            currentRoom?.slug === room.slug
              ? darkMode
                ? 'bg-primary-bg-dark border-accent-dark'
                : 'bg-primary-bg-light border-accent-light'
              : darkMode
                ? 'border-accent-1 hover:bg-primary-bg-dark'
                : 'border-accent-2 hover:bg-primary-bg-light'
          "
          class="w-full rounded-lg border px-3 py-3 text-left transition"
          @click="chat.joinRoom(room.slug)"
        >
          <span class="block truncate text-sm font-semibold">
            {{ room.name }}
          </span>

          <span class="mt-1 block text-xs opacity-60">
            {{ room.participant_count }} participant{{ room.participant_count === 1 ? "" : "s" }}
          </span>
        </button>

        <p v-if="!rooms.length" class="text-xs opacity-50">
          No rooms found.
        </p>
      </nav>

      <!-- Participants in mobile sidebar -->
      <div class="mt-4 border-t border-current/10 pt-4">
        <div class="flex items-center justify-between">
          <span class="text-sm font-bold">
            Participants
          </span>

          <span class="text-xs opacity-60">
            {{ participants.length }}
          </span>
        </div>

        <div class="mt-3 space-y-3">
          <div
            v-for="p in participants"
            :key="p.name"
            class="flex items-center gap-3"
          >
            <div
              :class="darkMode ? 'border-accent-1' : 'border-accent-2'"
              class="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg border text-xs font-bold"
            >
              <img
                v-if="p.avatar_url"
                :src="p.avatar_url"
                :alt="p.name"
                class="h-full w-full object-cover"
              />

              <span v-else>
                {{ p.name.slice(0, 2).toUpperCase() }}
              </span>
            </div>

            <p class="min-w-0 flex-1 truncate text-sm font-semibold">
              {{ p.name }}
            </p>
          </div>

          <p v-if="!participants.length" class="text-sm opacity-60">
            No participants.
          </p>
        </div>
      </div>

      <!-- Join / Leave in mobile sidebar -->
      <div v-if="currentRoom" class="mt-4 border-t border-current/10 pt-4">
        <OyaButton
          v-if="!isParticipant"
          :dark-mode="darkMode"
          :class="{ 'opacity-60 pointer-events-none': joiningOrLeaving }"
          class="w-full"
          @click="handleJoin"
        >
          <font-awesome-icon icon="right-to-bracket" />
          Join Room
        </OyaButton>

        <OyaButton
          v-else
          :dark-mode="darkMode"
          variant="secondary"
          :class="{ 'opacity-60 pointer-events-none': joiningOrLeaving }"
          class="w-full"
          @click="handleLeave"
        >
          <font-awesome-icon icon="right-from-bracket" />
          Leave Room
        </OyaButton>
      </div>
    </aside>
  </Transition>
</template>