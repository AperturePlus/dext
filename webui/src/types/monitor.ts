export interface ApiEnvelope<T> {
  data?: T
  error?: {
    type: string
    message: string
  }
}

export interface StageItem {
  name: string
  state: 'completed' | 'current' | 'pending' | 'failed'
}

export interface BuildSummary {
  source_count: number
  rows_read: number
  observations_written: number
  documents_seen: number
  canonical_active: number
  unresolved_findings: number
  graph_export_rows: number
}

export interface MonitorBuild {
  id: string
  status: string
  started_at: string | null
  finished_at: string | null
  last_error: string | null
  summary: BuildSummary
  is_active: boolean
  stage: StageItem[]
}

export interface BuildsResponse {
  catalog_path: string
  schema_version: number
  builds: MonitorBuild[]
  latest_build_id: string | null
}

export interface HealthResponse {
  catalog_path: string
  server_time: number
  readable: boolean
  schema_version: number | null
  build_count?: number
  error?: string
}

export interface SourceTask {
  build_id: string
  university_id: string
  university_name: string
  abbr: string
  source_path: string
  ordinal: number
  status: string
  rows_read: number
  observations_written: number
  documents_seen: number
  findings: number
  last_error: string | null
  updated_at: string
}

export interface Checkpoint {
  build_id: string
  sink: string
  partition_key: string
  last_key: string | null
  last_batch_id: string | null
  rows_written: number
  updated_at: string
}

export interface ExportPartition {
  build_id?: string
  partition_key: string
  row_kind: 'node' | 'relationship'
  label_or_type: string
  row_count: number
  generated_at?: string
}

export interface RunDetail {
  id: string
  build_id: string
  status: string
  summary_json: Record<string, unknown>
  started_at: string | null
  finished_at: string | null
  last_error: string | null
}

export interface BuildDetailResponse {
  catalog_path: string
  build: MonitorBuild
  sources: SourceTask[]
  checkpoints: Checkpoint[]
  export_partitions: ExportPartition[]
  unresolved_findings: Record<string, number>
  curation: RunDetail | null
  graph: RunDetail | null
}

export interface SourceMetric {
  university_id: string
  university_name: string
  abbr: string
  status: string
  rows_read: number
  observations_written: number
  documents_seen: number
  findings: number
}

export interface MetricsResponse {
  build_id: string
  stage: StageItem[]
  source_status_counts: Record<string, number>
  finding_counts: Record<string, number>
  role_counts: Record<string, number>
  title_family_counts: Record<string, number>
  export_partitions: ExportPartition[]
  observations_by_source: SourceMetric[]
}

export interface GraphNode {
  id: string
  row_key: string
  label: string
  category: string
  partition_key: string
}

export interface GraphLink {
  id: string
  source: string
  target: string
  label: string
}

export interface GraphPreviewResponse {
  build_id: string
  limit: number
  nodes: GraphNode[]
  links: GraphLink[]
  total_nodes: number
  total_relationships: number
  truncated: boolean
  export_pruned?: boolean
}

export interface Finding {
  id: string
  build_id: string
  severity: string
  code: string
  entity_id: string | null
  observation_id: string | null
  details_json: Record<string, unknown>
  resolved: boolean
}

export interface FindingsResponse {
  findings: Finding[]
  limit: number
}

export interface UniversitySummary {
  graph_key: string
  name: string
  logical_id: string
  orgunit_count: number
  professor_count: number
}

export interface UniversityTopologyNode {
  id: string
  label: string
  category: 'University' | 'OrgUnit'
  professor_count: number
  orgunit_count?: number
  kind?: string
  university?: string
}

export interface UniversityTopologyLink {
  source: string
  target: string
  label: 'PART_OF'
}

export interface UniversityTopologyResponse {
  build_id: string
  universities: UniversitySummary[]
  nodes: UniversityTopologyNode[]
  links: UniversityTopologyLink[]
  export_pruned?: boolean
}

export interface ProfessorNode {
  graph_key: string
  name: string
  title: string | null
  title_family: string | null
  role_status: string
}

export interface ProfessorLink {
  source: string
  target: string
  label: 'AFFILIATED_WITH'
}

export interface OrgUnitProfessorResponse {
  build_id: string
  orgunit: {
    graph_key: string
    label: string
    kind: string
    university: string
  }
  professors: ProfessorNode[]
  links: ProfessorLink[]
}

export type TopicRelationLabel =
  | 'PRIMARY_TOPIC'
  | 'USES_METHOD'
  | 'APPLIED_TO'
  | 'TARGETS_TASK'
  | 'STUDIES'

export interface TopicNode {
  graph_key: string
  logical_id: string
  canonical_name: string
  normalized_name: string
  kind: string
  status: string
  taxonomy_version: string
}

export interface ProfessorTopicLink {
  source: string
  target: string
  label: TopicRelationLabel
  evidence_count: number
  confidence: number | null
}

export interface ProfessorTopicResponse {
  build_id: string
  professor: ProfessorNode
  topics: TopicNode[]
  links: ProfessorTopicLink[]
}
