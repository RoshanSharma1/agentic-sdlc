import { useState, useEffect } from 'react';
import axios from 'axios';
import './AgentStatusModal.css';

interface AgentStatusModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface AgentInfo {
  name: string;
  available: boolean;
  installed: boolean;
  authenticated: boolean | null;
  exhausted: boolean | null;
  state: string;
  next_reset_at: string | null;
  version: string | null;
  credits_remaining: number | null;
  credits_limit: number | null;
  subscription_tier: string | null;
  rate_limit_remaining: number | null;
  auth_method: string | null;
  account_label: string | null;
  status_command: string | null;
  interactive_status_command: string | null;
  interactive_usage_command: string | null;
  status_source: string | null;
  status_details: string | null;
  notes: string | null;
  error_message: string | null;
  last_checked: string | null;
  next_reset_date: string | null;
  usage_windows: UsageWindow[];
}

interface UsageWindow {
  label: string;
  used_percentage: number | null;
  remaining_percentage: number | null;
  reset_at: string | null;
  exhausted: boolean | null;
}

interface AgentStatusResponse {
  agents: Record<string, AgentInfo>;
  recommended_agent: string | null;
  total_available: number;
}

const AgentStatusModal = ({ isOpen, onClose }: AgentStatusModalProps) => {
  const [status, setStatus] = useState<AgentStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get<AgentStatusResponse>('/api/agents/status');
      setStatus(data);
      setLastChecked(new Date());
    } catch (error) {
      console.error('Failed to fetch agent status:', error);
    } finally {
      setLoading(false);
    }
  };

  const formatUsageValue = (window: UsageWindow) => {
    if (window.used_percentage !== null) return `${window.used_percentage}% used`;
    if (window.remaining_percentage !== null) return `${window.remaining_percentage}% left`;
    return 'Unavailable';
  };

  const formatUsageReset = (window: UsageWindow) => {
    if (!window.reset_at) return null;
    return `Resets ${window.reset_at}`;
  };

  useEffect(() => {
    if (isOpen) {
      fetchStatus();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const getStatusColor = (agent: AgentInfo) => {
    if (!agent.installed) return 'unavailable';
    if (agent.state === 'exhausted') return 'no-credits';
    if (agent.authenticated === false) return 'unavailable';
    if (!agent.available) return 'unavailable';
    return 'available';
  };

  return (
    <div className="agent-status-overlay" onClick={onClose}>
      <div className="agent-status-modal" onClick={(e) => e.stopPropagation()}>
        <div className="agent-status-header-bar">
          <div>
            <h2 className="agent-status-title">Agent Status</h2>
            <p className="agent-status-subtitle">Detect exhausted agents and show the next credit reset when the CLI exposes it</p>
          </div>
          <button className="agent-status-close" onClick={onClose}>✕</button>
        </div>

        <div className="agent-status-body">
          {loading && !status ? (
            <div className="loading-state">
              <div className="spinner" />
              <p>Checking agent status...</p>
            </div>
          ) : status ? (
            <>
              <div className="status-summary-section">
                <div className="status-summary">
                  <div className="summary-card">
                    <div className="summary-label">Available Agents</div>
                    <div className="summary-value">{status.total_available}/3</div>
                  </div>
                  {status.recommended_agent && (
                    <div className="summary-card recommended">
                      <div className="summary-label">Recommended</div>
                      <div className="summary-value">{status.recommended_agent}</div>
                    </div>
                  )}
                  <div className="summary-card">
                    <div className="summary-label">Last Checked</div>
                    <div className="summary-value summary-value-small">
                      {lastChecked?.toLocaleTimeString() ?? 'Waiting...'}
                    </div>
                  </div>
                  <div className="summary-card summary-action">
                    <div className="summary-label">Refresh</div>
                    <button className="btn-refresh summary-refresh" onClick={fetchStatus} disabled={loading}>
                      {loading ? 'Refreshing...' : 'Refresh'}
                    </button>
                  </div>
                </div>
              </div>

              <div className="agents-section">
                <div className="agents-grid">
                  {Object.values(status.agents).map((agent) => (
                    <div key={agent.name} className={`agent-status-card ${getStatusColor(agent)}`}>
                      <div className="agent-status-header">
                        <div className="agent-name">{agent.name}</div>
                      </div>

                      <div className="agent-details">
                        <div className="detail-row">
                          <span className="detail-label">Next Reset</span>
                          <span className="detail-value detail-value-right">{agent.next_reset_at ?? 'Unavailable'}</span>
                        </div>

                        {agent.usage_windows.map((window) => (
                          <div key={`${agent.name}-${window.label}`} className="detail-row">
                            <span className="detail-label">{window.label}</span>
                            <span className="detail-value-group">
                              <span className="detail-value">{formatUsageValue(window)}</span>
                              {formatUsageReset(window) && (
                                <span className="detail-meta">{formatUsageReset(window)}</span>
                              )}
                            </span>
                          </div>
                        ))}

                        {agent.error_message && (
                          <div className="error-message">
                            <span className="error-icon">⚠️</span>
                            {agent.error_message}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">
              <p>Failed to load agent status</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default AgentStatusModal;
