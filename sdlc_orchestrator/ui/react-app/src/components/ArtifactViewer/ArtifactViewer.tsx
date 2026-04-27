import { useState, useEffect } from 'react';
import { projectsApi } from '../../services/api';
import './ArtifactViewer.css';

interface ArtifactViewerProps {
  projectName: string | null;
  artifactKey: string | null;
  artifactLabel: string;
  isOpen: boolean;
  onClose: () => void;
}

const ArtifactViewer = ({ projectName, artifactKey, artifactLabel, isOpen, onClose }: ArtifactViewerProps) => {
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && projectName && artifactKey) {
      loadArtifact();
    }
  }, [isOpen, projectName, artifactKey]);

  const loadArtifact = async () => {
    if (!projectName || !artifactKey) return;

    setLoading(true);
    setError(null);

    try {
      const data = await projectsApi.getArtifact(projectName, artifactKey, true);
      setContent(typeof data === 'string' ? data : JSON.stringify(data, null, 2));
    } catch (err: any) {
      setError(err.message || 'Failed to load artifact');
      setContent('');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="artifact-overlay" onClick={onClose}>
      <div className="artifact-modal" onClick={(e) => e.stopPropagation()}>
        <div className="artifact-header">
          <div className="artifact-header-content">
            <h2 className="artifact-title">{artifactLabel}</h2>
            <span className="artifact-type">{artifactKey?.replace(/_/g, ' ') || ''}</span>
          </div>
          <button className="artifact-close" onClick={onClose}>✕</button>
        </div>

        <div className="artifact-body">
          {loading ? (
            <div className="artifact-loading">
              <div className="spinner" />
              <p>Loading artifact...</p>
            </div>
          ) : error ? (
            <div className="artifact-error">
              <p className="error-message">❌ {error}</p>
              <p className="error-hint">The artifact file may not exist or is not accessible.</p>
            </div>
          ) : (
            <div className="artifact-content">
              <pre>{content}</pre>
            </div>
          )}
        </div>

        <div className="artifact-footer">
          <button className="artifact-btn" onClick={onClose}>
            Close
          </button>
          {!error && content && (
            <button
              className="artifact-btn artifact-btn-primary"
              onClick={() => {
                const blob = new Blob([content], { type: 'text/plain' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${artifactKey}.txt`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              Download
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default ArtifactViewer;
