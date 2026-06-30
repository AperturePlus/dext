<script setup lang="ts">
import type { StageItem } from '../../types/monitor'
import StatusBadge from '../primitives/StatusBadge.vue'

defineProps<{
  stages: StageItem[]
}>()
</script>

<template>
  <ol class="stage-timeline" aria-label="Build stages">
    <li v-for="stage in stages" :key="stage.name" :class="`stage-${stage.state}`">
      <span class="rail-dot" />
      <span class="stage-name">{{ stage.name }}</span>
      <StatusBadge v-if="stage.state === 'current' || stage.state === 'failed'" :status="stage.state" />
    </li>
  </ol>
</template>

<style scoped>
.stage-timeline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.8rem;
  padding: 0;
  margin: 0;
  list-style: none;
}

li {
  display: grid;
  gap: 0.55rem;
  align-content: start;
  min-width: 0;
  min-height: 92px;
  padding: 0.85rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-soft);
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.stage-name {
  min-width: 0;
  line-height: 1.2;
  overflow-wrap: anywhere;
  word-break: normal;
}

.rail-dot {
  width: 0.72rem;
  height: 0.72rem;
  border-radius: 999px;
  background: var(--subtle);
}

.stage-completed {
  color: var(--success);
}

.stage-current {
  border-color: rgba(112, 167, 255, 0.4);
  color: var(--accent-2);
}

.stage-failed {
  border-color: rgba(255, 107, 122, 0.42);
  color: var(--danger);
}

.stage-completed .rail-dot,
.stage-current .rail-dot {
  background: currentColor;
  box-shadow: 0 0 16px currentColor;
}
</style>
