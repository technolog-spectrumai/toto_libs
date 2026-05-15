<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { api } from "../services/api";
import { getServerUrl } from "../services/appStorage";
import { useUserStore } from "../stores/user";
import OyaButton from "../components/OyaButton.vue";
import logo2Dark from "../assets/logo2_dark.png";
import logo2Light from "../assets/logo2_light.png";

defineProps<{ darkMode: boolean }>();

const router = useRouter();
const userStore = useUserStore();

const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");
const showPassword = ref(false);

const serverUrl = ref("");

onMounted(async () => {
  serverUrl.value = await getServerUrl();
});

const canSubmit = computed(
  () => username.value.trim().length > 0 && password.value.length > 0
);

async function submit() {
  if (!canSubmit.value || loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    await api.login(username.value.trim(), password.value);
    await userStore.fetchMe();
    router.push("/");
  } catch (e) {
    error.value = (e as Error).message;
    password.value = "";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="flex flex-1 items-center justify-center px-4 py-12">
    <div class="w-full max-w-sm space-y-8">

      <!-- Branding -->
      <div class="flex flex-col items-center gap-3 text-center">
        <img
          :src="darkMode ? logo2Dark : logo2Light"
          alt="Enigma"
          class="h-36 w-36 object-contain"
        />
        <div>
          <h1 class="text-2xl font-bold tracking-tight">Sign in to Enigma</h1>
          <p class="mt-1 text-sm opacity-50">
            Connected to
            <span
              v-if="serverUrl"
              class="font-mono"
            >{{ serverUrl }}</span>
            <button
              v-else
              class="underline hover:opacity-80"
              @click="router.push('/config')"
            >configure server</button>
          </p>
        </div>
      </div>

      <!-- Form card -->
      <div
        :class="
          darkMode
            ? 'bg-primary-bg-dark border-accent-1 shadow-black/30'
            : 'bg-primary-bg-light border-accent-2 shadow-black/10'
        "
        class="rounded-2xl border p-6 shadow-xl space-y-5"
      >

        <!-- Error banner -->
        <div
          v-if="error"
          :class="darkMode ? 'bg-warn-dark/10 text-warn-dark' : 'bg-warn-light/10 text-warn-light'"
          class="flex items-start gap-2.5 rounded-xl px-4 py-3 text-sm"
        >
          <font-awesome-icon icon="triangle-exclamation" class="mt-0.5 shrink-0" />
          <span class="break-all leading-snug">{{ error }}</span>
        </div>

        <div class="space-y-4">

          <!-- Username -->
          <div class="space-y-1.5">
            <label
              for="username"
              class="block text-xs font-semibold uppercase tracking-widest opacity-50"
            >
              Username
            </label>
            <input
              id="username"
              v-model="username"
              type="text"
              autocomplete="username"
              placeholder="your-username"
              :disabled="loading"
              :class="[
                darkMode
                  ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/25'
                  : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/25',
                loading ? 'opacity-50' : '',
              ]"
              class="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition focus:ring-2 focus:ring-current/20"
              @keyup.enter="submit"
            />
          </div>

          <!-- Password -->
          <div class="space-y-1.5">
            <label
              for="password"
              class="block text-xs font-semibold uppercase tracking-widest opacity-50"
            >
              Password
            </label>
            <div class="relative">
              <input
                id="password"
                v-model="password"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="current-password"
                placeholder="••••••••"
                :disabled="loading"
                :class="[
                  darkMode
                    ? 'bg-primary-bg-dark border-accent-1 text-text-main-dark placeholder:text-text-main-dark/25'
                    : 'bg-primary-bg-light border-accent-2 text-text-main-light placeholder:text-text-main-light/25',
                  loading ? 'opacity-50' : '',
                ]"
                class="w-full rounded-xl border px-4 py-2.5 pr-11 text-sm outline-none transition focus:ring-2 focus:ring-current/20"
                @keyup.enter="submit"
              />
              <button
                type="button"
                class="absolute inset-y-0 right-0 flex w-11 items-center justify-center opacity-40 hover:opacity-70 transition"
                tabindex="-1"
                @click="showPassword = !showPassword"
              >
                <font-awesome-icon :icon="showPassword ? 'eye-slash' : 'eye'" />
              </button>
            </div>
          </div>

        </div>

        <!-- Submit -->
        <OyaButton
          :dark-mode="darkMode"
          :class="[
            'w-full justify-center text-base py-2.5',
            (!canSubmit || loading) ? 'pointer-events-none opacity-40' : '',
          ]"
          @click="submit"
        >
          <font-awesome-icon
            :icon="loading ? 'lock' : 'right-to-bracket'"
            :class="loading ? 'animate-pulse' : ''"
          />
          {{ loading ? "Signing in…" : "Sign in" }}
        </OyaButton>

      </div>

      <!-- Footer links -->
      <div class="flex justify-center gap-6 text-xs opacity-40">
        <button class="hover:opacity-80 transition" @click="router.push('/config')">
          <font-awesome-icon icon="gear" class="mr-1" />
          Settings
        </button>
        <button class="hover:opacity-80 transition" @click="router.push('/')">
          <font-awesome-icon icon="arrow-left" class="mr-1" />
          Continue offline
        </button>
      </div>

    </div>
  </div>
</template>
