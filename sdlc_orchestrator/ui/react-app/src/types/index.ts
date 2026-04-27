export type Phase = 'requirement' | 'design' | 'planning' | 'implementation' | 'testing' | 'documentation';
export type Status = 'pending' | 'in_progress' | 'done' | 'blocked' | 'failed';
export type ProcessStatus = 'running' | 'stopped' | 'waiting' | 'held' | 'done' | 'stale';
export type AgentStatus = 'available' | 'no_credits' | 'cooldown' | 'error' | 'disabled';

export interface Project {
  name: string;
  display_name: string;
  phase: Phase;
  status: Status;
  label: string;
  phase_total: number;
  phases: PhaseData[];
  stories: Story[];
  at_gate: boolean;
  bypassed_phases: string[];
  current_story: string | null;
  story_status: Status | null;
  current_task: string | null;
  completed_stories: string[];
  last_updated: string;
  repo: string;
  branch: string;
  base_branch: string;
  state_url: string;
  pr_links: Record<string, string>;
  commit_links: Record<string, string[]>;
  artifact_links: Record<string, string>;
  artifact_items: ArtifactItem[];
  held: boolean;
  pipeline_status: {
    status: ProcessStatus;
    pid: number | null;
    last_tick: number | null;
    is_running: boolean;
    at_gate?: boolean;
    held?: boolean;
    job_id?: string | null;
    job_status?: string | null;
    job_type?: string | null;
    run_id?: string | null;
    agent_name?: string | null;
    skill?: string | null;
    error?: string | null;
  };
  agent_registry?: AgentRegistry;
  source?: ProjectSource;
  repo_binding?: RepoBinding;
  archived?: boolean;
  phase_approvals?: Record<string, boolean>;
}

export interface PhaseData {
  name: Phase;
  status: Status;
  pr_url?: string;
  artifact_url?: string;
  artifact_items: ArtifactItem[];
  commit_urls: string[];
  stories: Story[];
}

export interface Story {
  id: string;
  name: string;
  status: Status;
  github_issue?: string;
  github_pr?: string;
  pr_url?: string;
  commit_urls: string[];
  current_task?: string;
  tasks: Task[];
}

export interface Task {
  id: string;
  status: Status;
  commit_urls: string[];
  [key: string]: any;
}

export interface ArtifactItem {
  key: string;
  label: string;
  group: string;
  phase: string;
  url: string;
}

export interface Agent {
  name: string;
  provider: string;
  status: AgentStatus;
  priority: number;
  supports_headless: boolean;
  last_used: string | null;
  last_error: string | null;
  last_credit_error: string | null;
  health_reason: string | null;
  cooldown_until: string | null;
  reset_at: string | null;
  success_count: number;
  failure_count: number;
  // Usage tracking
  total_api_calls: number;
  total_tokens_used: number;
  total_input_tokens: number;
  total_output_tokens: number;
  estimated_cost_usd: number;
  credits_remaining: number | null;
  credits_limit: number | null;
  daily_usage: Record<string, DailyUsage>;
}

export interface DailyUsage {
  api_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface AgentRegistry {
  active_agent: string | null;
  agents: Agent[];
  history: HistoryEvent[];
  counts: {
    total: number;
    available: number;
    blocked: number;
  };
}

export interface HistoryEvent {
  timestamp: string;
  event: string;
  message: string;
  metadata?: Record<string, any>;
}

export interface ProjectSource {
  type: string;
  label: string;
  location: string;
  content_text: string;
  metadata: Record<string, any>;
  updated_at: string;
}

export interface RepoBinding {
  provider: string;
  mode: string;
  repo_name: string;
  repo_url: string;
  local_path: string;
  is_new: boolean;
  metadata: Record<string, any>;
  updated_at: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

export interface ChatExecutor {
  requested_executor: string;
  resolved_executor: string | null;
  executor: string | null;
  label: string;
  available: boolean;
  resume: boolean;
  options: ChatOption[];
  placeholder: string;
  greeting: string;
}

export interface ChatOption {
  name: string;
  label: string;
  available: boolean;
  resume: boolean;
}

export interface EvidenceFile {
  file: string;
  test_id: string | null;
  type: string;
  size: number;
}

export interface GitHubRepo {
  name: string;
  full_name: string;
  url: string;
  private: boolean;
  description?: string;
}

export interface RuntimeMeta {
  version: string;
  source_mode: string;
  package_root: string;
  server_file: string;
  ui_entry_file: string;
  project_dir: string;
}
