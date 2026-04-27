import { useState, useEffect } from 'react';
import { projectsApi } from '../services/api';
import type { AgentRegistry } from '../types';

export const useProject = (projectName: string | null) => {
  const [state, setState] = useState<any>(null);
  const [agents, setAgents] = useState<AgentRegistry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!projectName) {
      setLoading(false);
      return;
    }

    const fetchProjectData = async () => {
      try {
        setLoading(true);
        setError(null);

        const [stateData, agentsData] = await Promise.all([
          projectsApi.getState(projectName),
          projectsApi.getAgents(projectName).catch(() => null),
        ]);

        setState(stateData);
        setAgents(agentsData);
      } catch (err) {
        setError(err as Error);
      } finally {
        setLoading(false);
      }
    };

    fetchProjectData();
  }, [projectName]);

  return { state, agents, loading, error };
};
