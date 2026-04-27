import { useState } from 'react';
import type { Project, Phase } from '../types';
import ProjectDetailModal from '../components/Modals/ProjectDetailModal';
import './PipelineView.css';

interface PipelineViewProps {
  projects: Project[];
  closedProjects: Project[];
}

const PHASES: Phase[] = ['requirement', 'design', 'planning', 'implementation', 'testing', 'documentation'];

const PipelineView = ({ projects, closedProjects }: PipelineViewProps) => {
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);

  // Group projects by phase
  const projectsByPhase = PHASES.reduce((acc, phase) => {
    acc[phase] = projects.filter((p) => p.phase === phase);
    return acc;
  }, {} as Record<Phase, Project[]>);

  return (
    <div className="pipeline-view">
      <div className="pipeline-board">
        {PHASES.map((phase) => (
          <div key={phase} className="pipeline-column">
            <div className="pipeline-header">
              <div className="pipeline-title">{phase}</div>
              <span className="pipeline-count">{projectsByPhase[phase].length}</span>
            </div>

            <div className="pipeline-list">
              {projectsByPhase[phase].length > 0 ? (
                projectsByPhase[phase].map((project) => (
                  <div
                    key={project.name}
                    className="pipeline-card"
                    onClick={() => setSelectedProject(project)}
                  >
                    <div className="pipeline-card-name">{project.display_name}</div>
                    <div className="pipeline-card-meta">
                      {project.current_story && <span>📌 {project.current_story}</span>}
                      {project.completed_stories.length > 0 && (
                        <span>✓ {project.completed_stories.length} stories</span>
                      )}
                    </div>
                    {project.at_gate && (
                      <div className="gate-badge">Waiting for approval</div>
                    )}
                  </div>
                ))
              ) : (
                <div className="pipeline-empty">No projects</div>
              )}
            </div>
          </div>
        ))}

        {/* Done/Launch column for completed projects */}
        <div className="pipeline-column pipeline-column-done">
          <div className="pipeline-header">
            <div className="pipeline-title">Done</div>
            <span className="pipeline-count">{closedProjects.length}</span>
          </div>

          <div className="pipeline-list">
            {closedProjects.length > 0 ? (
              closedProjects.map((project) => (
                <div
                  key={project.name}
                  className="pipeline-card pipeline-card-done"
                  onClick={() => setSelectedProject(project)}
                >
                  <div className="pipeline-card-name">{project.display_name}</div>
                  <div className="pipeline-card-meta">
                    {project.completed_stories.length > 0 && (
                      <span>✓ {project.completed_stories.length} stories</span>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <div className="pipeline-empty">No completed projects</div>
            )}
          </div>
        </div>
      </div>

      <ProjectDetailModal
        project={selectedProject}
        isOpen={selectedProject !== null}
        onClose={() => setSelectedProject(null)}
        onRefresh={() => setSelectedProject(null)}
      />
    </div>
  );
};

export default PipelineView;
