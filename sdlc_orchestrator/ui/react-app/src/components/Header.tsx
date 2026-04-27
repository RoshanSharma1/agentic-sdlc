import { useState, useEffect } from 'react';
import { metaApi } from '../services/api';
import type { RuntimeMeta } from '../types';
import './Header.css';

interface HeaderProps {
  onRefresh: () => void;
  onStartProject?: () => void;
  onAgentStatus?: () => void;
}

const Header = ({ onRefresh, onStartProject, onAgentStatus }: HeaderProps) => {
  const [meta, setMeta] = useState<RuntimeMeta | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  useEffect(() => {
    metaApi.getMeta().then(setMeta).catch(console.error);

    const interval = setInterval(() => {
      setLastUpdate(new Date());
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  const timeSinceUpdate = Math.floor((Date.now() - lastUpdate.getTime()) / 1000);
  const timeText = timeSinceUpdate < 60
    ? `${timeSinceUpdate}s ago`
    : `${Math.floor(timeSinceUpdate / 60)}m ago`;

  return (
    <header className="header">
      <div className="header-main">
        <h1>Chorus</h1>
        <span className="header-timer">Updated {timeText}</span>
      </div>

      {meta && (
        <div className="runtime-banner">
          <span className={`runtime-mode ${meta.source_mode}`}>
            {meta.source_mode}
          </span>
          <strong>v{meta.version}</strong>
          {' • '}
          {meta.project_dir}
        </div>
      )}

      <div className="header-actions">
        {onAgentStatus && (
          <button className="header-btn" onClick={onAgentStatus}>
            🤖 Agent Status
          </button>
        )}
        {onStartProject && (
          <button className="header-btn" onClick={onStartProject}>
            ＋ Start Project
          </button>
        )}
        <button className="header-btn" onClick={onRefresh}>
          ↻ Refresh
        </button>
      </div>
    </header>
  );
};

export default Header;
