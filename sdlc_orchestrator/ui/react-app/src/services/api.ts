import axios from 'axios';
import type {
  Project,
  AgentRegistry,
  EvidenceFile,
  GitHubRepo,
  RuntimeMeta,
  ChatExecutor,
  ProjectSource,
  RepoBinding
} from '../types';

const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

export const projectsApi = {
  // Get all projects
  getProjects: async () => {
    const { data } = await api.get<{ active: Project[]; closed: Project[] }>('/projects');
    return data;
  },

  // Get project intake data
  getIntake: async (name: string) => {
    const { data } = await api.get<{ source: ProjectSource | null; repo_binding: RepoBinding | null }>(
      `/projects/${encodeURIComponent(name)}/intake`
    );
    return data;
  },

  // Get project state JSON
  getState: async (name: string) => {
    const { data } = await api.get(`/projects/${encodeURIComponent(name)}/state`, {
      responseType: 'text',
    });
    return JSON.parse(data);
  },

  // Get agents for a project
  getAgents: async (name: string) => {
    const { data } = await api.get<AgentRegistry>(`/projects/${encodeURIComponent(name)}/agents`);
    return data;
  },

  // Reset all agents
  resetAllAgents: async (name: string) => {
    const { data } = await api.post(`/projects/${encodeURIComponent(name)}/agents/reset`);
    return data;
  },

  // Reset specific agent
  resetAgent: async (name: string, agentName: string) => {
    const { data } = await api.post(
      `/projects/${encodeURIComponent(name)}/agents/${encodeURIComponent(agentName)}/reset`
    );
    return data;
  },

  // Get artifact
  getArtifact: async (name: string, key: string, raw = false) => {
    const { data } = await api.get(
      `/projects/${encodeURIComponent(name)}/artifact/${encodeURIComponent(key)}`,
      { params: { raw } }
    );
    return data;
  },

  // Approve project
  approve: async (name: string) => {
    const { data } = await api.post(`/projects/${encodeURIComponent(name)}/approve`);
    return data;
  },

  // Disable approvals
  noApprovals: async (name: string) => {
    const { data } = await api.post(`/projects/${encodeURIComponent(name)}/no-approvals`);
    return data;
  },

  // Restore approvals
  restoreApprovals: async (name: string) => {
    const { data } = await api.post(`/projects/${encodeURIComponent(name)}/approvals`);
    return data;
  },

  // Hold project
  hold: async (name: string) => {
    const { data } = await api.post(`/projects/${encodeURIComponent(name)}/hold`);
    return data;
  },

  // Resume project
  resume: async (name: string) => {
    const { data } = await api.post(`/projects/${encodeURIComponent(name)}/resume`);
    return data;
  },

  // Start pipeline
  startPipeline: async (name: string) => {
    const { data } = await api.post(`/projects/${encodeURIComponent(name)}/start-pipeline`);
    return data;
  },

  // Get process status
  getStatus: async (name: string) => {
    const { data } = await api.get(`/projects/${encodeURIComponent(name)}/status`);
    return data;
  },

  // Get PRs
  getPRs: async (name: string) => {
    const { data } = await api.get(`/projects/${encodeURIComponent(name)}/prs`);
    return data;
  },

  // Get history
  getHistory: async (name: string) => {
    const { data } = await api.get(`/projects/${encodeURIComponent(name)}/history`);
    return data;
  },

  // Get evidence index
  getEvidence: async (name: string) => {
    const { data } = await api.get<{ evidence: EvidenceFile[]; evidence_dir: string | null }>(
      `/projects/${encodeURIComponent(name)}/evidence`
    );
    return data;
  },

  // Get evidence file
  getEvidenceFile: async (name: string, filename: string) => {
    const { data } = await api.get(
      `/projects/${encodeURIComponent(name)}/evidence/${encodeURIComponent(filename)}`
    );
    return data;
  },

  // Create project
  createProject: async (projectData: any) => {
    const { data } = await api.post('/projects/start', projectData);
    return data;
  },
};

export const chatApi = {
  // Get current working directory
  getCwd: async () => {
    const { data } = await api.get<{ cwd: string }>('/chat/cwd');
    return data;
  },

  // Set current working directory
  setCwd: async (path: string) => {
    const { data } = await api.post('/chat/cwd', { path });
    return data;
  },

  // Get chat metadata
  getMeta: async (executor?: string) => {
    const { data } = await api.get<ChatExecutor>('/chat/meta', {
      params: executor ? { executor } : undefined,
    });
    return data;
  },

  // Send chat message
  sendMessage: async (message: string, executor?: string) => {
    const { data } = await api.get<{
      job_id: string;
      executor: string | null;
      resolved_executor: string | null;
      requested_executor: string;
      label: string;
    }>('/chat', {
      params: { message, executor },
    });
    return data;
  },

  // Poll chat job
  pollJob: async (jobId: string, offset = 0) => {
    const { data } = await api.get<{ lines: string[]; done: boolean; offset: number }>(
      `/chat/${encodeURIComponent(jobId)}`,
      { params: { offset } }
    );
    return data;
  },

  // Clear chat
  clear: async () => {
    const { data } = await api.post('/chat/clear');
    return data;
  },

  // Get running jobs
  getJobs: async () => {
    const { data } = await api.get<{ running: number; job_ids: string[] }>('/chat/jobs');
    return data;
  },
};

export const githubApi = {
  // List repositories
  listRepos: async (limit = 100) => {
    const { data } = await api.get<{ available: boolean; repos: GitHubRepo[] }>(
      '/intake/github/repos',
      { params: { limit } }
    );
    return data;
  },

  // Create repository
  createRepo: async (repoData: { name: string; description?: string; private?: boolean }) => {
    const { data } = await api.post('/intake/github/repos', repoData);
    return data;
  },
};

export const filesystemApi = {
  // Browse filesystem
  browse: async (path = '~', includeFiles = false) => {
    const { data } = await api.get<{
      path: string;
      parent: string;
      dirs: string[];
      files: string[];
      entries: Array<{ name: string; path: string; is_dir: boolean; is_file: boolean }>;
    }>('/fs/browse', {
      params: { path, include_files: includeFiles },
    });
    return data;
  },
};

export const metaApi = {
  // Get runtime metadata
  getMeta: async () => {
    const { data } = await api.get<RuntimeMeta>('/meta');
    return data;
  },
};

export default api;
