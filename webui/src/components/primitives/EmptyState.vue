<script setup lang="ts">
defineProps<{
  title: string
  message?: string
}>()
</script>

<template>
  <section class="empty-state">
    <div class="orb" />
    <strong>{{ title }}</strong>
    <p v-if="message">{{ message }}</p>
  </section>
</template>

<style scoped>
.empty-state {
  display: grid;
  place-items: center;
  min-height: 220px;
  padding: 2rem;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--surface-soft);
  color: var(--muted);
  text-align: center;
}

.orb {
  width: 42px;
  height: 42px;
  margin-bottom: 0.8rem;
  border: 1px solid rgba(62, 230, 181, 0.46);
  border-radius: 999px;
  background: radial-gradient(circle, rgba(62, 230, 181, 0.4), transparent 60%);
}

strong {
  color: var(--text);
}

p {
  max-width: 420px;
  margin: 0.4rem 0 0;
}
</style>
