<script setup lang="ts">
import type { Finding } from '../../types/monitor'
import StatusBadge from '../primitives/StatusBadge.vue'

defineProps<{
  counts: Record<string, number>
  findings: Finding[]
}>()
</script>

<template>
  <div class="finding-summary">
    <div class="chips">
      <span v-for="[severity, count] in Object.entries(counts)" :key="severity" class="finding-chip">
        <StatusBadge :status="severity" />
        <strong>{{ count }}</strong>
      </span>
      <span v-if="Object.keys(counts).length === 0" class="clean">No unresolved findings</span>
    </div>
    <div class="finding-list">
      <article v-for="finding in findings.slice(0, 5)" :key="finding.id">
        <StatusBadge :status="finding.severity" />
        <strong>{{ finding.code }}</strong>
        <span class="mono">{{ finding.id }}</span>
      </article>
    </div>
  </div>
</template>

<style scoped>
.finding-summary {
  display: grid;
  gap: 1rem;
  padding: 1rem;
}

.chips,
.finding-list {
  display: grid;
  gap: 0.65rem;
}

.chips {
  display: flex;
  flex-wrap: wrap;
}

.finding-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding-right: 0.55rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface-soft);
}

.finding-chip strong {
  font-size: 0.78rem;
}

.clean {
  color: var(--success);
  font-weight: 800;
}

article {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 0.7rem;
  padding: 0.72rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-soft);
}

strong {
  font-size: 0.88rem;
}

article > span:last-child {
  color: var(--subtle);
  font-size: 0.76rem;
}
</style>
