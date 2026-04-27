import type { Project } from '../types';
import './ViewTabs.css';

interface ViewTabsProps {
  activeView: 'projects' | 'pipeline' | 'agents';
  onViewChange: (view: 'projects' | 'pipeline' | 'agents') => void;
  projects: { active: Project[]; closed: Project[] };
}

const ViewTabs = ({ activeView, onViewChange, projects }: ViewTabsProps) => {
  const activeCount = projects.active.length;
  const closedCount = projects.closed.length;

  return (
    <div className="view-tabs">
      <div className="view-tabs-group">
        <button
          className={`view-tab ${activeView === 'pipeline' ? 'active' : ''}`}
          onClick={() => onViewChange('pipeline')}
        >
          Pipeline
        </button>
        <button
          className={`view-tab ${activeView === 'projects' ? 'active' : ''}`}
          onClick={() => onViewChange('projects')}
        >
          Projects
        </button>
        <button
          className={`view-tab ${activeView === 'agents' ? 'active' : ''}`}
          onClick={() => onViewChange('agents')}
        >
          Agents
        </button>
      </div>

      <div className="view-summary">
        <span>{activeCount} active</span>
        <span>{closedCount} closed</span>
      </div>
    </div>
  );
};

export default ViewTabs;
