import { useState, useEffect, useCallback } from 'react';
import { projectsApi } from '../services/api';
import type { Project } from '../types';

export const useProjects = (autoRefresh = true, refreshInterval = 5000) => {
  const [projects, setProjects] = useState<{ active: Project[]; closed: Project[] }>({
    active: [],
    closed: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchProjects = useCallback(async () => {
    try {
      setError(null);
      const data = await projectsApi.getProjects();
      setProjects(data);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjects();

    if (autoRefresh) {
      const intervalId = setInterval(fetchProjects, refreshInterval);
      return () => clearInterval(intervalId);
    }
  }, [fetchProjects, autoRefresh, refreshInterval]);

  return { projects, loading, error, refetch: fetchProjects };
};
