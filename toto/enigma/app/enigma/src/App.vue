<script setup lang="ts">
import { ref, watch, onMounted } from "vue";
import { RouterView, RouterLink, useRouter, useRoute } from "vue-router";
import { storeToRefs } from "pinia";
import { useUserStore } from "./stores/user";
import { useChatStore } from "./stores/chat";
import { api } from "./services/api";
import appIcon from "./assets/icon.png";

const darkMode = ref(true);
const menuOpen = ref(false);
const router = useRouter();
const route = useRoute();

const userStore = useUserStore();
const chatStore = useChatStore();
const { me } = storeToRefs(userStore);

onMounted(() => {
  void chatStore.syncRuntimeIdentity();
  userStore.fetchMe();
});

watch(me, (val) => {
  if (val) chatStore.currentUserName = val.full_name;
});

// Close menu on navigation
watch(() => route.path, () => { menuOpen.value = false; });

async function handleLogout() {
  await api.logout();
  userStore.me = null;
  menuOpen.value = false;
  router.push("/login");
}
</script>

<template>
  <div
    :class="darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"
    class="min-h-screen flex flex-col font-sans transition-colors duration-300"
  >
    <!-- Header -->
    <header
      :class="darkMode ? 'bg-header-bg-dark text-appbar-text-dark' : 'bg-header-bg-light text-appbar-text-light'"
      class="sticky top-0 z-50 border-b border-current/10 shadow-md"
    >
      <div class="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">

        <!-- Logo -->
        <RouterLink to="/" class="flex items-center gap-3 hover:opacity-80 transition">
          <img :src="appIcon" alt="Enigma icon" class="h-9 w-9 rounded-lg object-contain" />
          <h1 class="text-xl font-bold tracking-wide sm:text-2xl">Enigma</h1>
        </RouterLink>

        <!-- Desktop nav -->
        <nav class="hidden sm:flex items-center gap-x-5 text-sm">
          <RouterLink to="/chat" class="inline-flex items-center gap-2 transition hover:opacity-70">
            <font-awesome-icon icon="comments" />
            Chat
          </RouterLink>

          <RouterLink to="/profile" class="inline-flex items-center gap-2 transition hover:opacity-70">
            <img
              v-if="me?.avatar_url"
              :src="me.avatar_url"
              :alt="me.full_name"
              class="h-5 w-5 rounded-full object-cover"
            />
            <font-awesome-icon v-else icon="user" />
            <span>{{ me?.full_name ?? "My Profile" }}</span>
          </RouterLink>

          <RouterLink to="/config" class="inline-flex items-center gap-2 transition hover:opacity-70">
            <font-awesome-icon icon="gear" />
            Settings
          </RouterLink>

          <button
            type="button"
            class="inline-flex items-center gap-2 transition hover:opacity-70"
            @click="darkMode = !darkMode"
          >
            <font-awesome-icon :icon="darkMode ? 'sun' : 'moon'" />
            {{ darkMode ? "Light" : "Dark" }}
          </button>

          <RouterLink
            v-if="!me"
            to="/login"
            class="inline-flex items-center gap-2 transition hover:opacity-70"
          >
            <font-awesome-icon icon="right-to-bracket" />
            Login
          </RouterLink>
          <button
            v-else
            class="inline-flex items-center gap-2 transition hover:opacity-70"
            @click="handleLogout"
          >
            <font-awesome-icon icon="right-from-bracket" />
            Logout
          </button>
        </nav>

        <!-- Mobile: avatar + hamburger -->
        <div class="flex items-center gap-3 sm:hidden">
          <RouterLink to="/profile">
            <img
              v-if="me?.avatar_url"
              :src="me.avatar_url"
              :alt="me.full_name"
              class="h-8 w-8 rounded-full object-cover border border-current/20"
            />
            <font-awesome-icon v-else icon="user" class="opacity-60" />
          </RouterLink>

          <button
            type="button"
            class="flex h-9 w-9 items-center justify-center rounded-lg border border-current/15 transition hover:opacity-70"
            :aria-label="menuOpen ? 'Close menu' : 'Open menu'"
            @click="menuOpen = !menuOpen"
          >
            <font-awesome-icon :icon="menuOpen ? 'xmark' : 'bars'" />
          </button>
        </div>

      </div>

      <!-- Mobile dropdown -->
      <Transition
        enter-active-class="transition-all duration-200 ease-out"
        enter-from-class="opacity-0 -translate-y-2"
        enter-to-class="opacity-100 translate-y-0"
        leave-active-class="transition-all duration-150 ease-in"
        leave-from-class="opacity-100 translate-y-0"
        leave-to-class="opacity-0 -translate-y-2"
      >
        <div
          v-if="menuOpen"
          :class="darkMode ? 'bg-header-bg-dark border-accent-1' : 'bg-header-bg-light border-accent-2'"
          class="sm:hidden border-t divide-y divide-current/10"
        >
          <RouterLink
            to="/chat"
            class="flex items-center gap-3 px-5 py-3.5 text-sm hover:opacity-70 transition"
          >
            <font-awesome-icon icon="comments" class="w-4 text-center" />
            Chat
          </RouterLink>

          <RouterLink
            to="/profile"
            class="flex items-center gap-3 px-5 py-3.5 text-sm hover:opacity-70 transition"
          >
            <font-awesome-icon icon="user" class="w-4 text-center" />
            {{ me?.full_name ?? "My Profile" }}
          </RouterLink>

          <RouterLink
            to="/config"
            class="flex items-center gap-3 px-5 py-3.5 text-sm hover:opacity-70 transition"
          >
            <font-awesome-icon icon="gear" class="w-4 text-center" />
            Settings
          </RouterLink>

          <button
            type="button"
            class="flex w-full items-center gap-3 px-5 py-3.5 text-sm hover:opacity-70 transition"
            @click="darkMode = !darkMode"
          >
            <font-awesome-icon :icon="darkMode ? 'sun' : 'moon'" class="w-4 text-center" />
            {{ darkMode ? "Light Mode" : "Dark Mode" }}
          </button>

          <RouterLink
            v-if="!me"
            to="/login"
            class="flex items-center gap-3 px-5 py-3.5 text-sm hover:opacity-70 transition"
          >
            <font-awesome-icon icon="right-to-bracket" class="w-4 text-center" />
            Login
          </RouterLink>
          <button
            v-else
            class="flex w-full items-center gap-3 px-5 py-3.5 text-sm hover:opacity-70 transition"
            @click="handleLogout"
          >
            <font-awesome-icon icon="right-from-bracket" class="w-4 text-center" />
            Logout
          </button>
        </div>
      </Transition>
    </header>

    <!-- Main -->
    <main class="flex flex-1 px-4 py-6 sm:px-6 lg:px-8">
      <RouterView v-slot="{ Component }">
        <component :is="Component" :dark-mode="darkMode" />
      </RouterView>
    </main>

    <!-- Footer -->
    <footer
      :class="darkMode ? 'bg-footer-bg-dark text-footer-text-dark' : 'bg-footer-bg-light text-footer-text-light'"
      class="border-t border-current/10 px-4 py-4 text-center text-sm shadow-lg"
    >
      <p class="opacity-90">© www.spectrumai.pl. All rights reserved.</p>
    </footer>
  </div>
</template>
