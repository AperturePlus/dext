<script setup lang="ts">
const props = defineProps<{
  status: string
}>()

const normalized = () => props.status.toLowerCase()
</script>

<template>
  <span class="status-badge" :class="`status-${normalized()}`">
    <span class="dot" />
    {{ status }}
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  min-width: max-content;
  padding: 0.32rem 0.62rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.12);
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.dot {
  width: 0.48rem;
  height: 0.48rem;
  border-radius: 999px;
  background: currentColor;
  box-shadow: 0 0 14px currentColor;
}

.status-writing_vector,
.status-writing_graph,
.status-curating,
.status-ingesting,
.status-snapshotting,
.status-embedding,
.status-validating,
.status-current,
.status-running {
  border-color: rgba(112, 167, 255, 0.38);
  background: rgba(112, 167, 255, 0.12);
  color: var(--accent-2);
}

.status-ready,
.status-active,
.status-completed,
.status-success,
.status-included {
  border-color: rgba(72, 217, 154, 0.38);
  background: rgba(72, 217, 154, 0.12);
  color: var(--success);
}

.status-failed,
.status-failed_validation,
.status-error,
.status-excluded {
  border-color: rgba(255, 107, 122, 0.42);
  background: rgba(255, 107, 122, 0.12);
  color: var(--danger);
}

.status-review,
.status-warning {
  border-color: rgba(246, 200, 95, 0.42);
  background: rgba(246, 200, 95, 0.12);
  color: var(--warning);
}

.status-catalog_readable {
  border-color: rgba(72, 217, 154, 0.38);
  background: rgba(72, 217, 154, 0.12);
  color: var(--success);
}

.status-catalog_unavailable {
  border-color: rgba(255, 107, 122, 0.42);
  background: rgba(255, 107, 122, 0.12);
  color: var(--danger);
}
</style>
