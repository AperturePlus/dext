<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <section class="panel-card">
    <header>
      <div>
        <h2>{{ title }}</h2>
        <p v-if="subtitle">{{ subtitle }}</p>
      </div>
      <slot name="actions" />
    </header>
    <slot />
  </section>
</template>

<style scoped>
.panel-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px);
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.05rem 1.1rem 0.7rem;
  border-bottom: 1px solid var(--border);
}

h2 {
  margin: 0;
  font-size: 1rem;
  letter-spacing: -0.02em;
}

p {
  margin: 0.25rem 0 0;
  color: var(--muted);
  font-size: 0.84rem;
}

:slotted(.panel-body) {
  padding: 1rem;
}
</style>
