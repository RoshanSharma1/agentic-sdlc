import { useState } from 'react';
import type { Project } from '../types';
import ProjectCard from '../components/ProjectCard';
import ProjectDetailModal from '../components/Modals/ProjectDetailModal';
import './ProjectsView.css';

interface ProjectsViewProps {
  projects: { active: Project[]; closed: Project[] };
  loading: boolean;
  onRefresh?: () => void;
}

const ProjectsView = ({ projects, loading, onRefresh }: ProjectsViewProps) => {
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  if (loading) {
    return (
      <div className="loading-state">
        <div className="spinner" />
        <p>Loading projects...</p>
      </div>
    );
  }

  return (
    <div className="projects-view">
      {projects.active.length > 0 && (
        <section className="projects-section">
          <h2 className="section-title">Active Projects</h2>
          <div className="projects-grid">
            {projects.active.map((project) => (
              <ProjectCard
                key={project.name}
                project={project}
                onClick={() => setSelectedProject(project)}
                onRefresh={onRefresh}
              />
            ))}
          </div>
        </section>
      )}

      {projects.closed.length > 0 && (
        <section className="projects-section">
          <h2 className="section-title">Closed Projects</h2>
          <div className="projects-grid">
            {projects.closed.map((project) => (
              <ProjectCard
                key={project.name}
                project={project}
                onClick={() => setSelectedProject(project)}
                onRefresh={onRefresh}
              />
            ))}
          </div>
        </section>
      )}

      {projects.active.length === 0 && projects.closed.length === 0 && (
        <div className="empty-state">
          <p>No projects found</p>
          <button className="btn-primary">Create Project</button>
        </div>
      )}

      <ProjectDetailModal
        project={selectedProject}
        isOpen={selectedProject !== null}
        onClose={() => setSelectedProject(null)}
        onRefresh={() => {
          onRefresh?.();
          setSelectedProject(null);
        }}
      />
    </div>
  );
};

export default ProjectsView;
