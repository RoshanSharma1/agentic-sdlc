import type { Project } from '../types';
import { projectsApi } from '../services/api';
import './ProjectCard.css';

interface ProjectCardProps {
  project: Project;
  onClick?: () => void;
  onRefresh?: () => void;
}

const ProjectCard = ({ project, onClick, onRefresh }: ProjectCardProps) => {
  const phaseIndex = ['requirement', 'design', 'planning', 'implementation', 'testing', 'documentation'].indexOf(project.phase);
  const progress = phaseIndex >= 0 ? ((phaseIndex + 1) / 6) * 100 : 100;
  const isDone = project.status === 'done' || phaseIndex < 0;
  const isGate = project.at_gate;

  const statusClass = isDone ? 'done' : isGate ? 'gate' : 'active';
  const statusLabel = isDone ? 'Done' : isGate ? 'At Gate' : 'Active';

  const handleAction = async (e: React.MouseEvent, action: 'approve' | 'hold' | 'resume') => {
    e.stopPropagation();
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
      }
      onRefresh?.();
    } catch (error) {
      console.error(`Failed to ${action}:`, error);
    }
  };

  return (
    <div className="project-card" onClick={onClick}>
      <div className={`project-mark ${statusClass}`}>
        <div className="project-status-text">{statusLabel}</div>
        <div className="project-progress-text">{Math.round(progress)}%</div>
      </div>

      <div className="project-main">
        <div className="project-title-row">
          <h3 className="project-name">{project.display_name}</h3>
          <span className="project-phase-chip">{project.phase}</span>
        </div>

        <div className="project-meta">
          {project.repo && <span>📦 {project.repo}</span>}
          {project.branch && <span>🌿 {project.branch}</span>}
        </div>

        <div className="project-stats">
          {project.completed_stories.length > 0 && (
            <span>✓ {project.completed_stories.length} stories</span>
          )}
          {project.current_story && <span>📌 {project.current_story}</span>}
        </div>

        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${progress}%` }} />
        </div>
      </div>

      <div className="project-side">
        <div className="project-actions">
          {project.at_gate && (
            <button
              className="action-btn-card btn-approve"
              onClick={(e) => handleAction(e, 'approve')}
              title="Approve"
            >
              ✓
            </button>
          )}
          {project.held ? (
            <button
              className="action-btn-card btn-resume"
              onClick={(e) => handleAction(e, 'resume')}
              title="Resume"
            >
              ▶
            </button>
          ) : !isDone && (
            <button
              className="action-btn-card btn-hold"
              onClick={(e) => handleAction(e, 'hold')}
              title="Hold"
            >
              ⏸
            </button>
          )}
        </div>
        <div className="project-meta-side">
          {project.last_updated && (
            <span>{new Date(project.last_updated).toLocaleDateString()}</span>
          )}
        </div>
      </div>
    </div>
  );
};

export default ProjectCard;
