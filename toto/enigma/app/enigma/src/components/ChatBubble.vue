<script setup lang="ts">
import type { Message } from "../stores/chat";

defineProps<{
  darkMode: boolean;
  message: Message;
}>();
</script>

<template>
  <article
    class="flex min-w-0 gap-3"
    :class="message.self ? 'justify-end' : 'justify-start'"
  >
    <div
      v-if="!message.self"
      :class="
        message.type === 'system_error' || message.type === 'system_info'
          ? 'border-current/20 opacity-50'
          : darkMode ? 'border-accent-1' : 'border-accent-2'
      "
      class="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border text-xs font-bold"
    >
      <img
        v-if="message.avatar_url"
        :src="message.avatar_url"
        :alt="message.user"
        class="h-full w-full object-cover"
      />
      <span v-else>{{ message.initials }}</span>
    </div>

    <div
      :class="[
        message.type === 'system_error'
          ? 'border-warn-dark bg-warn-dark/10 text-warn-dark'
          : message.type === 'system_info'
            ? 'border-current/10 opacity-60 italic'
            : darkMode
              ? 'bg-bubble-bg-dark border-accent-1'
              : 'bg-bubble-bg-light border-accent-2',
        message.self ? 'text-right' : '',
      ]"
      class="min-w-0 max-w-[calc(100%-3.25rem)] overflow-hidden rounded-lg border p-3 shadow-sm sm:max-w-[78%] xl:max-w-[42rem]"
    >
      <p class="text-xs font-semibold opacity-60">{{ message.user }}</p>
      <p class="mt-1 whitespace-pre-wrap break-words text-sm opacity-90 [overflow-wrap:anywhere]">
        {{ message.content }}
      </p>
      <p
        class="mt-1 flex items-center gap-1 text-[10px] opacity-40"
        :class="message.self ? 'justify-end' : ''"
      >
        <font-awesome-icon
          v-if="message.encrypted"
          icon="lock"
          class="text-success-dark opacity-80"
          title="End-to-end encrypted"
        />
        {{ message.timestamp.toLocaleTimeString() }}
      </p>
    </div>

    <div
      v-if="message.self"
      :class="darkMode ? 'border-accent-1' : 'border-accent-2'"
      class="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border text-xs font-bold"
    >
      <img
        v-if="message.avatar_url"
        :src="message.avatar_url"
        :alt="message.user"
        class="h-full w-full object-cover"
      />
      <span v-else>{{ message.initials }}</span>
    </div>
  </article>
</template>
