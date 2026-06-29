<script setup lang="ts">
import type { MonitorBuild } from '../../types/monitor'
import { formatDateTime, shortId } from '../../utils/format'
import StatusBadge from '../primitives/StatusBadge.vue'

defineProps<{
  builds: MonitorBuild[]
  selectedBuildId: string | null
}>()

defineEmits<{
  select: [buildId: string]
}>()
</script>

<template>
  <div class="build-list">
    <button
      v-for="build in builds"
      :key="build.id"
      type="button"
      :class="{ active: build.id === selectedBuildId }"
      @click="$emit('select', build.id)"
    >
      <span class="mono">{{ shortId(build.id) }}</span>
      <StatusBadge :status="build.status" />
      <small>{{ formatDateTime(build.started_at) }}</small>
    </button>
  </div>
</template>

<style scoped>
.build-list {
  display: grid;
  gap: 0.65rem;
}

button {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.5rem;
  width: 100%;
  padding: 0.82rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: rgba(13, 25, 45, 0.62);
  color: var(--text);
  text-align: left;
}

button.active {
  border-color: rgba(62, 230, 181, 0.44);
  background: rgba(62, 230, 181, 0.1);
}

small {
  grid-column: 1 / -1;
  color: var(--muted);
}
</style>
