import { useState, useEffect } from 'react';
import { projectsApi } from '../../services/api';
import type { EvidenceFile } from '../../types';
import './EvidenceViewer.css';

interface EvidenceViewerProps {
  projectName: string | null;
  isOpen: boolean;
  onClose: () => void;
}

const EvidenceViewer = ({ projectName, isOpen, onClose }: EvidenceViewerProps) => {
  const [evidence, setEvidence] = useState<EvidenceFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<EvidenceFile | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [contentType, setContentType] = useState<'text' | 'image' | 'json'>('text');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isOpen && projectName) {
      loadEvidence();
    }
  }, [isOpen, projectName]);

  const loadEvidence = async () => {
    if (!projectName) return;

    setLoading(true);
    try {
      const data = await projectsApi.getEvidence(projectName);
      setEvidence(data.evidence);

      // Auto-select first file
      if (data.evidence.length > 0 && !selectedFile) {
        loadFile(data.evidence[0]);
      }
    } catch (error) {
      console.error('Failed to load evidence:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadFile = async (file: EvidenceFile) => {
    if (!projectName) return;

    setSelectedFile(file);
    setLoading(true);

    try {
      const content = await projectsApi.getEvidenceFile(projectName, file.file);

      // Determine content type
      if (file.type === 'screenshot') {
        setContentType('image');
        setFileContent(`/api/projects/${encodeURIComponent(projectName)}/evidence/${encodeURIComponent(file.file)}`);
      } else if (file.type === 'api_response' || file.type === 'metrics') {
        setContentType('json');
        setFileContent(typeof content === 'string' ? content : JSON.stringify(content, null, 2));
      } else {
        setContentType('text');
        setFileContent(typeof content === 'string' ? content : String(content));
      }
    } catch (error) {
      console.error('Failed to load file:', error);
      setFileContent('Failed to load file');
      setContentType('text');
    } finally {
      setLoading(false);
    }
  };

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'screenshot': return '🖼️';
      case 'api_response': return '📡';
      case 'logs': return '📝';
      case 'metrics': return '📊';
      default: return '📄';
    }
  };

  const groupedEvidence = evidence.reduce((acc, file) => {
    const testId = file.test_id || 'Other';
    if (!acc[testId]) {
      acc[testId] = [];
    }
    acc[testId].push(file);
    return acc;
  }, {} as Record<string, EvidenceFile[]>);

  if (!isOpen) return null;

  return (
    <div className="evidence-overlay" onClick={onClose}>
      <div className="evidence-modal" onClick={(e) => e.stopPropagation()}>
        <div className="evidence-header">
          <span className="evidence-title">Test Evidence</span>
          <button className="evidence-close" onClick={onClose}>✕</button>
        </div>

        <div className="evidence-body">
          <div className="evidence-sidebar">
            {loading && evidence.length === 0 ? (
              <div className="evidence-empty">Loading...</div>
            ) : evidence.length === 0 ? (
              <div className="evidence-empty">No evidence files</div>
            ) : (
              Object.entries(groupedEvidence).map(([testId, files]) => (
                <div key={testId} className="evidence-group">
                  <div className="evidence-group-title">{testId}</div>
                  {files.map((file) => (
                    <div
                      key={file.file}
                      className={`evidence-item ${selectedFile?.file === file.file ? 'active' : ''}`}
                      onClick={() => loadFile(file)}
                    >
                      <span className={`evidence-item-type ${file.type}`}>
                        {getTypeIcon(file.type)}
                      </span>
                      <span className="evidence-item-name">
                        {file.file.replace(/-/g, ' ').replace(/\.[^/.]+$/, '')}
                      </span>
                    </div>
                  ))}
                </div>
              ))
            )}
          </div>

          <div className="evidence-content">
            {loading && selectedFile ? (
              <div className="evidence-empty">Loading file...</div>
            ) : !selectedFile ? (
              <div className="evidence-empty">Select an evidence file to view</div>
            ) : contentType === 'image' ? (
              <img src={fileContent || ''} alt={selectedFile.file} />
            ) : contentType === 'json' ? (
              <pre>{fileContent}</pre>
            ) : (
              <pre>{fileContent}</pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default EvidenceViewer;
