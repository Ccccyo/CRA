export type WorkflowStatus =
  | 'ready'
  | 'running'
  | 'awaiting_human'
  | 'blocked'
  | 'completed';

export type Workflow = {
  case_id: string;
  status: WorkflowStatus;
  current_stage: string;
  stage_index: number;
  current_task: string;
  blocker: string;
  completed_tasks: number;
  total_tasks: number;
  total_stages: number;
  stages: string[];
  updated_at: string;
};

export type HumanTask = {
  id: number;
  case_id: string;
  task_type: string;
  title: string;
  description: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type AgentEvent = {
  id: number;
  event_type: string;
  title: string;
  detail: string;
  sources: string[];
  created_at: string;
};

export type StageSnapshot = {
  id: number;
  case_id: string;
  stage: string;
  stage_index: number;
  title: string;
  payload: {
    workflow?: Workflow;
    notes?: Record<string, unknown>;
    history_status?: 'captured' | 'reconstructed';
  };
  completed_at: string;
};

export type CraCase = {
  case_id: string;
  title: string;
  bp: string;
  credit_segment: string;
  requested_cl_usd: number;
  statement_currency: string;
  customer: {
    name?: string;
    country?: string;
    state?: string;
    city?: string;
  };
  business: string;
  new_year: Record<string, unknown>;
  inputs: Record<string, string>;
  workflow: Workflow;
  human_tasks: HumanTask[];
  events: AgentEvent[];
  stage_history: StageSnapshot[];
  notes: Record<string, unknown>;
  bad_cases: Array<{
    id: number;
    node: string;
    failure_type: string;
    exposure: string;
    impact: string;
    priority: string;
    detection_source: string;
    response: string;
    status: string;
    created_at: string;
  }>;
};

export type CraCaseSummary = Pick<
  CraCase,
  'case_id' | 'title' | 'bp' | 'requested_cl_usd' | 'customer' | 'workflow'
>;

export type UploadPreview = {
  filename: string;
  checksum: string;
  valid: boolean;
  errors: string[];
  meta: Record<string, string>;
  case: Record<string, string>;
  years: Array<{ period_end: string; switch: string; source: string }>;
  sap_periods: string[];
  fx_count: number;
  ar: Record<string, string>;
  duplicate: null | { case_id: string; title: string };
};

export type RagDocument = {
  id: string;
  title: string;
  citation: string;
  preview: string;
  chunk_count: number;
  chunks: Array<{
    id: string;
    citation: string;
    section: string;
    page: number | null;
    text: string;
  }>;
};

export type RagLibrary = {
  id: 'policy' | 'public_reference' | 'case_history';
  label: string;
  description: string;
  required_role: string;
  accessible: boolean;
  document_count: number;
  chunk_count: number;
  documents: RagDocument[];
};

export type KnowledgeCatalog = {
  principal: {
    user_id: string;
    name: string;
    role: string;
  };
  permission_model: 'role_based';
  can_manage: boolean;
  granted_library_count: number;
  libraries: RagLibrary[];
};

export type KnowledgeAdminDocument = {
  id: string;
  library_id: RagLibrary['id'];
  title: string;
  citation: string;
  source_content: string;
  source_filename: string;
  mime_type: string;
  metadata: Record<string, unknown>;
  chunk_count: number;
  updated_at: string;
  source_reconstructed: boolean;
};
