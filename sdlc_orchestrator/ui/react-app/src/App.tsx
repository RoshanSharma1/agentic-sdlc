import { useState } from 'react';
import Header from './components/Header';
import ViewTabs from './components/ViewTabs';
import ProjectsView from './views/ProjectsView';
import PipelineView from './views/PipelineView';
import AgentsView from './views/AgentsView';
import ChatButton from './components/Chat/ChatButton';
import ChatOverlay from './components/Chat/ChatOverlay';
import StartProjectModal from './components/Modals/StartProjectModal';
import AgentStatusModal from './components/Modals/AgentStatusModal';
import { useProjects } from './hooks/useProjects';
import './App.css';

type View = 'projects' | 'pipeline' | 'agents';

function App() {
  const [activeView, setActiveView] = useState<View>('pipeline');
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isStartProjectOpen, setIsStartProjectOpen] = useState(false);
  const [isAgentStatusOpen, setIsAgentStatusOpen] = useState(false);
  const { projects, loading, refetch } = useProjects();

  return (
    <div className="app">
      <Header
        onRefresh={refetch}
        onStartProject={() => setIsStartProjectOpen(true)}
        onAgentStatus={() => setIsAgentStatusOpen(true)}
      />

      <div className="container">
        <ViewTabs activeView={activeView} onViewChange={setActiveView} projects={projects} />

        <div className="view-content">
          {activeView === 'projects' && <ProjectsView projects={projects} loading={loading} onRefresh={refetch} />}
          {activeView === 'pipeline' && <PipelineView projects={projects.active} closedProjects={projects.closed} />}
          {activeView === 'agents' && <AgentsView projects={[...projects.active, ...projects.closed]} />}
        </div>
      </div>

      <ChatButton onClick={() => setIsChatOpen(true)} />
      <ChatOverlay isOpen={isChatOpen} onClose={() => setIsChatOpen(false)} />
      <StartProjectModal
        isOpen={isStartProjectOpen}
        onClose={() => setIsStartProjectOpen(false)}
        onSuccess={refetch}
      />
      <AgentStatusModal
        isOpen={isAgentStatusOpen}
        onClose={() => setIsAgentStatusOpen(false)}
      />
    </div>
  );
}

export default App;
