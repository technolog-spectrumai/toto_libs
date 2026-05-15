<script setup lang="ts">
import { useRouter } from "vue-router";

defineProps<{ darkMode: boolean }>();

const router = useRouter();
</script>

<template>
  <!-- Navigation is now handled by vue-router; this component redirects to / -->
  <div class="flex flex-1 items-center justify-center text-sm opacity-50">
    <button @click="router.push('/')">Go to Welcome</button>
  </div>
</template>
