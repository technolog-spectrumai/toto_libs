<script setup lang="ts">
defineProps<{
  darkMode: boolean;
  variant?: 'primary' | 'secondary';
  type?: 'button' | 'submit' | 'reset';
}>();
</script>

<template>
  <button
    :type="type || 'button'"
    :class="
      variant === 'secondary'
        ? darkMode
          ? 'border-accent-1 bg-bubble-bg-dark text-text-main-dark'
          : 'border-accent-2 bg-bubble-bg-light text-text-main-light'
        : darkMode
          ? 'bg-accent-dark text-primary-bg-dark'
          : 'bg-accent-light text-primary-bg-light'
    "
    class="inline-flex items-center justify-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold shadow-sm transition hover:opacity-90"
  >
    <slot />
  </button>
</template>