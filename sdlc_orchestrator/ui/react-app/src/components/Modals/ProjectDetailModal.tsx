import { useState, useEffect } from 'react';
import { projectsApi } from '../../services/api';
import type { Project, PhaseData, Story } from '../../types';
import './ProjectDetailModal.css';

interface ProjectDetailModalProps {
  project: Project | null;
  isOpen: boolean;
  onClose: () => void;
  onRefresh: () => void;
}

const PHASE_LABELS: Record<string, string> = {
  requirement: 'Requirements',
  design: 'Design',
  planning: 'Planning',
  implementation: 'Implementation',
  testing: 'Testing',
  documentation: 'Documentation',
};

const ProjectDetailModal = ({ project, isOpen, onClose, onRefresh }: ProjectDetailModalProps) => {
  const [expandedPhases, setExpandedPhases] = useState<Set<string>>(new Set());
  const [expandedStories, setExpandedStories] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [phaseApprovals, setPhaseApprovals] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (isOpen && project) {
      // Auto-expand current phase
      setExpandedPhases(new Set([project.phase]));
      // Initialize phase approvals from project
      setPhaseApprovals(project.phase_approvals || {});
    }
  }, [isOpen, project]);

  if (!isOpen || !project) return null;

  const togglePhase = (phaseName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const newExpanded = new Set(expandedPhases);
    if (newExpanded.has(phaseName)) {
      newExpanded.delete(phaseName);
    } else {
      newExpanded.add(phaseName);
    }
    setExpandedPhases(newExpanded);
  };

  const toggleStory = (storyId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const newExpanded = new Set(expandedStories);
    if (newExpanded.has(storyId)) {
      newExpanded.delete(storyId);
    } else {
      newExpanded.add(storyId);
    }
    setExpandedStories(newExpanded);
  };

  const handleAction = async (action: 'approve' | 'hold' | 'resume' | 'no-approvals' | 'approvals') => {
    setLoading(true);
    try {
      switch (action) {
        case 'approve':
          await projectsApi.approve(project.name);
          break;
        case 'hold':
          await projectsApi.hold(project.name);
          break;
        case 'resume':
          await projectsApi.resume(project.name);
          break;
        case 'no-approvals':
          await projectsApi.noApprovals(project.name);
          break;
        case 'approvals':
          await projectsApi.restoreApprovals(project.name);
          break;
      }
      onRefresh();
    } catch (error) {
      console.error(`Failed to ${action}:`, error);
      alert(`Failed to ${action}`);
    } finally {
      setLoading(false);
    }
  };

  const handlePhaseApprovalToggle = async (phase: string, value: boolean) => {
    const updated = { ...phaseApprovals, [phase]: value };
    setPhaseApprovals(updated);

    try {
      await projectsApi.updatePhaseApprovals(project.name, updated);
      onRefresh();
    } catch (error) {
      console.error('Failed to update phase approvals:', error);
      alert('Failed to update phase approvals');
      // Revert on error
      setPhaseApprovals(phaseApprovals);
    }
  };

  const renderPhaseStatus = (phase: PhaseData) => {
    const statusClass = phase.status === 'done' ? 'done' : phase.status === 'in_progress' ? 'active' : 'pending';
    return <span className={`phase-status-badge ${statusClass}`}>{phase.status}</span>;
  };

  const renderStory = (story: Story) => {
    const isExpanded = expandedStories.has(story.id);
    const completedTasks = story.tasks.filter(t => t.status === 'done').length;
    const totalTasks = story.tasks.length;

    return (
      <div key={story.id} className="story-item">
        <div className="story-header" onClick={(e) => toggleStory(story.id, e)}>
          <div className="story-info">
            <span className="story-id">{story.id}</span>
            <span className="story-name">{story.name}</span>
          </div>
          <div className="story-meta">
            {totalTasks > 0 && (
              <span className="task-count">{completedTasks}/{totalTasks} tasks</span>
            )}
            <span className={`story-status ${story.status}`}>{story.status}</span>
            {story.pr_url && (
              <a href={story.pr_url} target="_blank" rel="noopener noreferrer" className="pr-link" onClick={(e) => e.stopPropagation()}>
                PR
              </a>
            )}
            <span className="expand-icon">{isExpanded ? '▼' : '▶'}</span>
          </div>
        </div>

        {isExpanded && story.tasks.length > 0 && (
          <div className="story-tasks">
            {story.tasks.map((task) => (
              <div key={task.id} className="task-item">
                <div className={`task-check ${task.status === 'done' ? 'done' : ''}`} />
                <div className="task-info">
                  <span className={`task-text ${task.status === 'done' ? 'done' : ''}`}>
                    {task.id}
                  </span>
                  {task.commit_urls && task.commit_urls.length > 0 && (
                    <div className="task-links">
                      {task.commit_urls.map((url, idx) => (
                        <a key={idx} href={url} target="_blank" rel="noopener noreferrer" className="commit-link">
                          Commit
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="project-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-header-content">
            <h2 className="modal-title">{project.display_name}</h2>
            <div className="project-detail-meta">
              {project.repo && (
                <span className="meta-item">📦 {project.repo}</span>
              )}
              {project.branch && (
                <span className="meta-item">🌿 {project.branch}</span>
              )}
              {project.at_gate && (
                <span className="meta-item">⏸ Waiting for approval</span>
              )}
            </div>
          </div>
          <div className="modal-header-badges">
            <span className={`process-status-badge ${project.pipeline_status.status}`}>
              {project.pipeline_status.status}
            </span>
            <button className="modal-close-btn" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="modal-body">
          {/* Phases as Progress Bar */}
          <div className="phases-section">
            {project.phases.map((phase) => (
              <div key={phase.name} className={`phase-detail phase-${phase.status}`}>
                <div className="phase-detail-header" onClick={(e) => togglePhase(phase.name, e)}>
                  <div className="phase-title-row">
                    <span className="expand-icon">{expandedPhases.has(phase.name) ? '▼' : '▶'}</span>
                    <h4 className="phase-name">{PHASE_LABELS[phase.name] || phase.name}</h4>
                    {renderPhaseStatus(phase)}
                  </div>
                  <div className="phase-meta">
                    {phase.stories.length > 0 && (
                      <span>{phase.stories.length} stories</span>
                    )}
                    {phase.pr_url && (
                      <a href={phase.pr_url} target="_blank" rel="noopener noreferrer" className="pr-link" onClick={(e) => e.stopPropagation()}>
                        PR
                      </a>
                    )}
                  </div>
                </div>

                {expandedPhases.has(phase.name) && (
                  <div className="phase-detail-content">
                    {phase.artifact_items.length > 0 && (
                      <div className="phase-artifacts">
                        {phase.artifact_items.map((artifact) => (
                          <a
                            key={artifact.key}
                            href={artifact.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="artifact-chip"
                          >
                            {artifact.label}
                          </a>
                        ))}
                      </div>
                    )}

                    {phase.stories.length > 0 && (
                      <div className="stories-list">
                        {phase.stories.map(renderStory)}
                      </div>
                    )}

                    {phase.commit_urls.length > 0 && (
                      <div className="commits-list">
                        <span className="commits-label">Commits:</span>
                        {phase.commit_urls.map((url, idx) => (
                          <a key={idx} href={url} target="_blank" rel="noopener noreferrer" className="commit-chip">
                            {url.split('/').pop()?.substring(0, 7)}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Quick Actions Bar */}
          {project.at_gate && (
            <div className="quick-action-bar">
              <button
                className="action-btn btn-approve"
                onClick={() => handleAction('approve')}
                disabled={loading}
              >
                ✓ Approve {PHASE_LABELS[project.phase]}
              </button>
            </div>
          )}

          {/* Settings Grid */}
          <div className="settings-grid">
            {/* Phase Approvals */}
            <div className="settings-card">
              <h3 className="settings-card-title">Phase Approval Gates</h3>
              <div className="settings-card-note">
                Toggle which phases require manual approval before proceeding.
              </div>
              <div className="approval-controls">
                {Object.entries(PHASE_LABELS).map(([phase, label]) => (
                  <label key={phase} className="approval-checkbox-label">
                    <input
                      type="checkbox"
                      checked={phaseApprovals[phase] || false}
                      onChange={(e) => handlePhaseApprovalToggle(phase, e.target.checked)}
                      disabled={loading}
                    />
                    <span className="approval-phase-name">{label}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Pipeline Controls */}
            <div className="settings-card">
              <h3 className="settings-card-title">Pipeline Controls</h3>
              <div className="settings-card-note">
                {project.last_updated && `Updated ${new Date(project.last_updated).toLocaleString()}`}
                {project.pipeline_status.pid && ` • PID: ${project.pipeline_status.pid}`}
              </div>
              <div className="control-buttons">
                {project.held ? (
                  <button
                    className="action-btn btn-resume"
                    onClick={() => handleAction('resume')}
                    disabled={loading}
                  >
                    ▶ Resume
                  </button>
                ) : (
                  <button
                    className="action-btn btn-hold"
                    onClick={() => handleAction('hold')}
                    disabled={loading}
                  >
                    ⏸ Hold Pipeline
                  </button>
                )}
                <button
                  className="action-btn btn-secondary"
                  onClick={() => handleAction('no-approvals')}
                  disabled={loading}
                  title="Disable all approval gates - pipeline runs fully autonomous"
                >
                  Disable All Gates
                </button>
                <button
                  className="action-btn btn-secondary"
                  onClick={() => handleAction('approvals')}
                  disabled={loading}
                  title="Enable all approval gates - pipeline pauses at each phase"
                >
                  Enable All Gates
                </button>
                <a
                  href={project.state_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="action-btn btn-secondary"
                >
                  View State JSON
                </a>
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
};

export default ProjectDetailModal;
