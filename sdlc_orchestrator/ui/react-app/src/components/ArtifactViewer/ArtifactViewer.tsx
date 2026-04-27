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

  const renderMarkdown = (text: string) => {
    // Simple markdown rendering for common patterns
    return text
      .split('\n')
      .map((line, idx) => {
        // Headers
        if (line.startsWith('# ')) {
          return <h1 key={idx} className="md-h1">{line.substring(2)}</h1>;
        }
        if (line.startsWith('## ')) {
          return <h2 key={idx} className="md-h2">{line.substring(3)}</h2>;
        }
        if (line.startswith('### ')) {
          return <h3 key={idx} className="md-h3">{line.substring(4)}</h3>;
        }

        // Lists
        if (line.match(/^[-*]\s/)) {
          return <li key={idx} className="md-li">{line.substring(2)}</li>;
        }

        // Code blocks
        if (line.startsWith('```')) {
          return null; // Handle in preprocessing
        }

        // Paragraphs
        if (line.trim()) {
          return <p key={idx} className="md-p">{line}</p>;
        }

        return <br key={idx} />;
      });
  };

  return (
    <div className="artifact-overlay" onClick={onClose}>
      <div className="artifact-modal" onClick={(e) => e.stopPropagation()}>
        <div className="artifact-header">
          <div className="artifact-header-content">
            <h2 className="artifact-title">{artifactLabel}</h2>
            <span className="artifact-type">{artifactKey.replace(/_/g, ' ')}</span>
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
