<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  value: string
  tone?: 'default' | 'accent' | 'warning' | 'danger'
  hint?: string
}>()

const isCompactValue = computed(() => props.value.length >= 11 || props.value.includes('_'))
</script>

<template>
  <article class="metric-card" :class="`tone-${tone ?? 'default'}`">
    <div class="metric-label">{{ label }}</div>
    <div class="metric-value" :class="{ 'metric-value-compact': isCompactValue }" :title="value">
      {{ value }}
    </div>
    <div v-if="hint" class="metric-hint">{{ hint }}</div>
  </article>
</template>

<style scoped>
.metric-card {
  position: relative;
  display: grid;
  align-content: start;
  overflow: hidden;
  min-width: 0;
  min-height: 116px;
  padding: 1.05rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow);
}

.metric-card::after {
  position: absolute;
  inset: auto 0 0 auto;
  width: 82px;
  height: 82px;
  content: "";
  border-radius: 999px;
  background: rgba(47, 107, 255, 0.10);
  filter: blur(12px);
  transform: translate(24px, 28px);
}

.metric-card > * {
  position: relative;
  z-index: 1;
  min-width: 0;
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
  line-height: 1.06;
  letter-spacing: 0;
  overflow-wrap: anywhere;
  word-break: normal;
}

.metric-value-compact {
  font-size: clamp(1.35rem, 2vw, 1.85rem);
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
  background: rgba(224, 167, 46, 0.22);
}

.tone-danger::after {
  background: rgba(224, 85, 106, 0.22);
}
</style>
