<script setup lang="ts">
defineProps<{
  paused: boolean
  lastUpdated?: Date | null
}>()

defineEmits<{
  refresh: []
  togglePause: []
}>()
</script>

<template>
  <div class="refresh-control">
    <span>
      {{ lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Waiting for first refresh' }}
    </span>
    <button type="button" @click="$emit('refresh')">Refresh</button>
    <button type="button" class="secondary" @click="$emit('togglePause')">
      {{ paused ? 'Resume polling' : 'Pause polling' }}
    </button>
  </div>
</template>

<style scoped>
.refresh-control {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  color: var(--muted);
  font-size: 0.86rem;
}

button {
  border: 1px solid rgba(62, 230, 181, 0.42);
  border-radius: 999px;
  padding: 0.5rem 0.82rem;
  background: rgba(62, 230, 181, 0.12);
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 700;
}

button.secondary {
  border-color: var(--border);
  background: rgba(148, 163, 184, 0.1);
  color: var(--muted);
}
</style>
