<script setup lang="ts">
defineProps<{
  darkMode: boolean;
  padded?: boolean;
}>();
</script>

<template>
  <section
    :class="[
      darkMode
        ? 'border-accent-1 bg-bubble-bg-dark'
        : 'border-accent-2 bg-bubble-bg-light',
      padded !== false ? 'p-4' : 'px-2 pt-4 pb-2'
    ]"
    class="rounded-lg border shadow-md transition-colors duration-300"
  >
    <slot />
  </section>
</template>