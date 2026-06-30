<script setup lang="ts">
import type { Checkpoint } from '../../types/monitor'
import { formatDateTime, formatNumber, shortId } from '../../utils/format'

defineProps<{
  checkpoints: Checkpoint[]
}>()
</script>

<template>
  <div class="checkpoint-list">
    <article v-for="checkpoint in checkpoints" :key="`${checkpoint.sink}:${checkpoint.partition_key}`">
      <div>
        <strong>{{ checkpoint.sink }}</strong>
        <span>{{ checkpoint.partition_key }}</span>
      </div>
      <div class="meta">
        <span>{{ formatNumber(checkpoint.rows_written) }} rows</span>
        <span class="mono">{{ shortId(checkpoint.last_key) }}</span>
        <span>{{ formatDateTime(checkpoint.updated_at) }}</span>
      </div>
    </article>
  </div>
</template>

<style scoped>
.checkpoint-list {
  display: grid;
  gap: 0.65rem;
  padding: 1rem;
}

article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.8rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-soft);
}

strong {
  display: block;
}

span {
  color: var(--muted);
  font-size: 0.8rem;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.75rem;
}
</style>
