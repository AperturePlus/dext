<script setup lang="ts">
defineProps<{
  label: string
  value: string
  tone?: 'default' | 'accent' | 'warning' | 'danger'
  hint?: string
}>()
</script>

<template>
  <article class="metric-card" :class="`tone-${tone ?? 'default'}`">
    <div class="metric-label">{{ label }}</div>
    <div class="metric-value">{{ value }}</div>
    <div v-if="hint" class="metric-hint">{{ hint }}</div>
  </article>
</template>

<style scoped>
.metric-card {
  position: relative;
  overflow: hidden;
  min-height: 116px;
  padding: 1.05rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: linear-gradient(160deg, rgba(19, 34, 59, 0.95), rgba(10, 20, 37, 0.88));
  box-shadow: var(--shadow);
}

.metric-card::after {
  position: absolute;
  inset: auto 0 0 auto;
  width: 82px;
  height: 82px;
  content: "";
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.14);
  filter: blur(12px);
  transform: translate(24px, 28px);
}

.metric-label {
  color: var(--muted);
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.metric-value {
  margin-top: 0.4rem;
  color: var(--text);
  font-size: clamp(1.65rem, 3vw, 2.35rem);
  font-weight: 800;
  letter-spacing: -0.04em;
}

.metric-hint {
  margin-top: 0.3rem;
  color: var(--subtle);
  font-size: 0.82rem;
}

.tone-accent::after {
  background: rgba(62, 230, 181, 0.22);
}

.tone-warning::after {
  background: rgba(246, 200, 95, 0.2);
}

.tone-danger::after {
  background: rgba(255, 107, 122, 0.2);
}
</style>
