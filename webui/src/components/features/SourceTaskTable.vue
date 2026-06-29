<script setup lang="ts">
import type { SourceTask } from '../../types/monitor'
import { formatNumber } from '../../utils/format'
import StatusBadge from '../primitives/StatusBadge.vue'

defineProps<{
  sources: SourceTask[]
}>()
</script>

<template>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Source</th>
          <th>Status</th>
          <th>Rows</th>
          <th>Observations</th>
          <th>Documents</th>
          <th>Findings</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="source in sources" :key="source.university_id">
          <td>
            <strong>{{ source.university_name }}</strong>
            <span>{{ source.abbr }} · {{ source.university_id }}</span>
          </td>
          <td><StatusBadge :status="source.status" /></td>
          <td>{{ formatNumber(source.rows_read) }}</td>
          <td>{{ formatNumber(source.observations_written) }}</td>
          <td>{{ formatNumber(source.documents_seen) }}</td>
          <td>{{ formatNumber(source.findings) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.table-wrap {
  overflow: auto;
  padding: 1rem;
}

table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
}

th,
td {
  padding: 0.78rem 0.7rem;
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.86rem;
  text-align: left;
}

th {
  color: var(--subtle);
  font-size: 0.74rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

td strong {
  display: block;
  color: var(--text);
  font-size: 0.92rem;
}

td span {
  color: var(--subtle);
  font-size: 0.78rem;
}
</style>
