<script setup lang="ts">
import type { RunDetail } from '../../types/monitor'
import { formatDateTime } from '../../utils/format'
import StatusBadge from '../primitives/StatusBadge.vue'

defineProps<{
  label: string
  run: RunDetail | null
}>()
</script>

<template>
  <article class="run-summary">
    <div>
      <h3>{{ label }}</h3>
      <StatusBadge :status="run?.status ?? 'not_started'" />
    </div>
    <dl v-if="run">
      <div>
        <dt>Started</dt>
        <dd>{{ formatDateTime(run.started_at) }}</dd>
      </div>
      <div>
        <dt>Finished</dt>
        <dd>{{ formatDateTime(run.finished_at) }}</dd>
      </div>
      <div v-if="run.last_error">
        <dt>Error</dt>
        <dd>{{ run.last_error }}</dd>
      </div>
    </dl>
  </article>
</template>

<style scoped>
.run-summary {
  display: grid;
  gap: 0.85rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: rgba(13, 25, 45, 0.56);
}

.run-summary > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
}

h3 {
  margin: 0;
  font-size: 0.94rem;
}

dl {
  display: grid;
  gap: 0.55rem;
  margin: 0;
}

dl > div {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 0.8rem;
}

dt {
  color: var(--subtle);
  font-size: 0.76rem;
  text-transform: uppercase;
}

dd {
  margin: 0;
  color: var(--muted);
  font-size: 0.84rem;
}
</style>
