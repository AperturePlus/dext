<script setup lang="ts">
defineProps<{
  title?: string
  message: string
}>()
</script>

<template>
  <section class="error-panel" role="alert">
    <strong>{{ title ?? 'Monitor request failed' }}</strong>
    <p>{{ message }}</p>
  </section>
</template>

<style scoped>
.error-panel {
  padding: 1rem;
  border: 1px solid rgba(255, 107, 122, 0.34);
  border-radius: var(--radius-md);
  background: rgba(255, 107, 122, 0.1);
  color: var(--danger);
}

.error-panel p {
  margin: 0.3rem 0 0;
  color: var(--muted);
}
</style>
