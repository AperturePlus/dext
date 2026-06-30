<script setup lang="ts">
import type { HealthResponse } from '../../types/monitor'
import StatusBadge from '../primitives/StatusBadge.vue'
import RefreshControl from '../primitives/RefreshControl.vue'

defineProps<{
  health: HealthResponse | null
  paused: boolean
  lastUpdated: Date | null
}>()

defineEmits<{
  refresh: []
  togglePause: []
}>()
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-mark">dx</span>
      <div class="brand-text">
        <strong>dext</strong>
        <span>monitor</span>
      </div>
    </div>

    <nav class="nav">
      <router-link to="/" class="nav-link">概览</router-link>
      <router-link to="/data" class="nav-link">数据</router-link>
      <router-link to="/quality" class="nav-link">质量</router-link>
      <router-link to="/topology" class="nav-link">拓扑图</router-link>
    </nav>

    <div class="footer">
      <StatusBadge :status="health?.readable ? 'catalog_readable' : 'catalog_unavailable'" />
      <RefreshControl
        :paused="paused"
        :last-updated="lastUpdated"
        @refresh="$emit('refresh')"
        @toggle-pause="$emit('togglePause')"
      />
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 1.4rem;
  width: 220px;
  min-height: 100vh;
  padding: 1.4rem 1rem;
  background: var(--surface);
  border-right: 1px solid var(--border);
  position: sticky;
  top: 0;
}
.brand {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0 0.3rem;
}
.brand-mark {
  display: inline-grid; place-items: center;
  width: 34px; height: 34px;
  border: 1px solid var(--border-strong); border-radius: 10px;
  background: var(--surface-soft); color: var(--accent);
  font-weight: 900; font-size: 0.8rem;
}
.brand-text { display: flex; flex-direction: column; line-height: 1.1; }
.brand-text strong { font-size: 0.95rem; color: var(--text); }
.brand-text span { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
.nav { display: flex; flex-direction: column; gap: 0.25rem; }
.nav-link {
  padding: 0.55rem 0.8rem;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 0.86rem;
  font-weight: 600;
  text-decoration: none;
  transition: color 0.15s, background 0.15s;
}
.nav-link:hover { color: var(--text); background: var(--surface-soft); }
.nav-link.router-link-active {
  color: var(--accent);
  background: rgba(47, 107, 255, 0.10);
}
.footer {
  margin-top: auto;
  display: grid;
  gap: 0.7rem;
  align-content: end;
}
@media (max-width: 760px) {
  .sidebar {
    position: static;
    flex-direction: row;
    width: 100%;
    min-height: auto;
    align-items: center;
    gap: 0.8rem;
    padding: 0.7rem 1rem;
    overflow-x: auto;
  }
  .nav { flex-direction: row; }
  .footer { margin-top: 0; grid-auto-flow: column; }
}
</style>
