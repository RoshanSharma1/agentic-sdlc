import { useState, useEffect } from 'react';
import { projectsApi, githubApi, filesystemApi } from '../../services/api';
import './StartProjectModal.css';

interface StartProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

type SourceMode = 'manual' | 'prd_file';
type RepoMode = 'existing' | 'new';
type PrdInputMode = 'browse' | 'url';

const StartProjectModal = ({ isOpen, onClose, onSuccess }: StartProjectModalProps) => {
  const [sourceMode, setSourceMode] = useState<SourceMode>('manual');
  const [repoMode, setRepoMode] = useState<RepoMode>('existing');
  const [prdInputMode, setPrdInputMode] = useState<PrdInputMode>('browse');
  const [submitting, setSubmitting] = useState(false);

  // Form fields
  const [projectName, setProjectName] = useState('');
  const [executor, setExecutor] = useState('claude-code');
  const [description, setDescription] = useState('');
  const [techStack, setTechStack] = useState('');
  const [sourceLabel, setSourceLabel] = useState('');
  const [sourceText, setSourceText] = useState('');
  const [sourcePath, setSourcePath] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [repo, setRepo] = useState('');
  const [repoName, setRepoName] = useState('');
  const [repoPrivate, setRepoPrivate] = useState(true);
  const [agentOrder, setAgentOrder] = useState('claude-code, kiro, codex');
  const [phaseApprovals, setPhaseApprovals] = useState({
    requirement: false,
    design: false,
    planning: false,
    implementation: false,
    testing: false,
    documentation: false,
  });
  const [agentFallback, setAgentFallback] = useState(true);
  const [startPipeline, setStartPipeline] = useState(true);

  // GitHub repos
  const [githubRepos, setGithubRepos] = useState<any[]>([]);
  const [loadingRepos, setLoadingRepos] = useState(false);

  // File browser
  const [currentPath, setCurrentPath] = useState('~');
  const [browserEntries, setBrowserEntries] = useState<any[]>([]);
  const [loadingBrowser, setLoadingBrowser] = useState(false);
  const [showFileBrowser, setShowFileBrowser] = useState(false);
  const [tempSelectedPath, setTempSelectedPath] = useState('');

  useEffect(() => {
    if (isOpen && repoMode === 'existing') {
      loadGithubRepos();
    }
  }, [isOpen, repoMode]);

  const loadGithubRepos = async () => {
    setLoadingRepos(true);
    try {
      const data = await githubApi.listRepos();
      if (data.available) {
        setGithubRepos(data.repos);
      }
    } catch (error) {
      console.error('Failed to load GitHub repos:', error);
    } finally {
      setLoadingRepos(false);
    }
  };

  const loadFileBrowser = async (path: string) => {
    setLoadingBrowser(true);
    try {
      const data = await filesystemApi.browse(path, true);
      setBrowserEntries(data.entries);
      setCurrentPath(data.path);
    } catch (error) {
      console.error('Failed to load file browser:', error);
    } finally {
      setLoadingBrowser(false);
    }
  };

  const handleFileSelect = (path: string, isDir: boolean) => {
    if (isDir) {
      loadFileBrowser(path);
    } else {
      setTempSelectedPath(path);
    }
  };

  const handleConfirmFile = () => {
    if (tempSelectedPath) {
      setSourcePath(tempSelectedPath);
      setShowFileBrowser(false);
      setTempSelectedPath('');
    }
  };

  const handleCancelFileBrowser = () => {
    setShowFileBrowser(false);
    setTempSelectedPath('');
  };

  const handleOpenFileBrowser = () => {
    setShowFileBrowser(true);
    loadFileBrowser(currentPath);
  };

  const handleSubmit = async () => {
    if (!projectName.trim()) {
      alert('Project name is required');
      return;
    }

    setSubmitting(true);
    try {
      const data: any = {
        project_name: projectName,
        executor,
        description,
        tech_stack: techStack,
        source_label: sourceLabel,
        source_type: sourceMode,
        phase_approvals: phaseApprovals,
        agent_fallback: agentFallback,
        agent_order: agentOrder,
        start_pipeline: startPipeline,
        repo_mode: repoMode,
      };

      // Add source data
      if (sourceMode === 'manual') {
        data.source_text = sourceText;
      } else if (prdInputMode === 'url') {
        data.source_path = sourceUrl;
      } else {
        data.source_path = sourcePath;
      }

      // Add repo data
      if (repoMode === 'existing') {
        data.repo = repo;
      } else if (repoMode === 'new') {
        data.repo_name = repoName;
        data.repo_private = repoPrivate;
      }

      await projectsApi.createProject(data);
      onSuccess();
      resetForm();
      onClose();
    } catch (error: any) {
      alert(`Failed to create project: ${error.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setProjectName('');
    setExecutor('claude-code');
    setDescription('');
    setTechStack('');
    setSourceLabel('');
    setSourceText('');
    setSourcePath('');
    setSourceUrl('');
    setRepo('');
    setRepoName('');
    setRepoPrivate(true);
    setAgentOrder('claude-code, kiro, codex');
    setPhaseApprovals({
      requirement: false,
      design: false,
      planning: false,
      implementation: false,
      testing: false,
      documentation: false,
    });
    setAgentFallback(true);
    setStartPipeline(true);
    setSourceMode('manual');
    setRepoMode('existing');
    setPrdInputMode('browse');
    setCurrentPath('~');
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card start-project-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">Start Project</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          <div className="modal-field">
            <label className="modal-label">Project Name</label>
            <input
              className="modal-input"
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              placeholder="Customer Portal"
            />
          </div>

          <div className="modal-field">
            <label className="modal-label">Preferred Agent</label>
            <select className="modal-select" value={executor} onChange={(e) => setExecutor(e.target.value)}>
              <option value="claude-code">claude-code</option>
              <option value="kiro">kiro</option>
              <option value="codex">codex</option>
              <option value="gemini">gemini</option>
            </select>
          </div>

          <div className="modal-field full">
            <label className="modal-label">Description</label>
            <textarea
              className="modal-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What problem does this project solve?"
            />
          </div>

          <div className="modal-field">
            <label className="modal-label">Tech Stack</label>
            <input
              className="modal-input"
              value={techStack}
              onChange={(e) => setTechStack(e.target.value)}
              placeholder="Python, FastAPI, React"
            />
          </div>

          <div className="modal-field">
            <label className="modal-label">Intake Label</label>
            <input
              className="modal-input"
              value={sourceLabel}
              onChange={(e) => setSourceLabel(e.target.value)}
              placeholder="Customer Portal PRD"
            />
          </div>

          {/* Source Mode Section */}
          <div className="modal-section">
            <div className="modal-section-head">
              <div>
                <div className="modal-section-title">Project Source</div>
                <div className="modal-section-copy">
                  Start from a manual idea or point to a PRD file.
                </div>
              </div>
            </div>

            <div className="modal-segmented">
              <button
                className={sourceMode === 'manual' ? 'active' : ''}
                onClick={() => setSourceMode('manual')}
              >
                Manual Idea
              </button>
              <button
                className={sourceMode === 'prd_file' ? 'active' : ''}
                onClick={() => setSourceMode('prd_file')}
              >
                PRD/File
              </button>
            </div>

            {sourceMode === 'manual' ? (
              <div className="modal-field">
                <label className="modal-label">Idea / Brief</label>
                <textarea
                  className="modal-textarea"
                  value={sourceText}
                  onChange={(e) => setSourceText(e.target.value)}
                  placeholder="Describe the product idea, user problem, constraints, and what success looks like."
                />
              </div>
            ) : (
              <>
                <div className="modal-segmented">
                  <button
                    className={prdInputMode === 'browse' ? 'active' : ''}
                    onClick={() => setPrdInputMode('browse')}
                  >
                    Browse Files
                  </button>
                  <button
                    className={prdInputMode === 'url' ? 'active' : ''}
                    onClick={() => setPrdInputMode('url')}
                  >
                    URL
                  </button>
                </div>

                {prdInputMode === 'browse' && (
                  <div className="modal-field">
                    <label className="modal-label">Selected File</label>
                    <div
                      className="modal-input file-browser-trigger"
                      onClick={handleOpenFileBrowser}
                    >
                      {sourcePath || 'Click to browse files...'}
                    </div>
                    {sourcePath && (
                      <div className="modal-note">Selected: {sourcePath}</div>
                    )}
                  </div>
                )}

                {prdInputMode === 'url' && (
                  <div className="modal-field">
                    <label className="modal-label">File URL</label>
                    <input
                      className="modal-input"
                      value={sourceUrl}
                      onChange={(e) => setSourceUrl(e.target.value)}
                      placeholder="https://example.com/prd.md"
                    />
                    <div className="modal-note">
                      Enter a URL to a publicly accessible PRD or source file.
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Repository Binding Section */}
          <div className="modal-section">
            <div className="modal-section-head">
              <div>
                <div className="modal-section-title">Repository Binding</div>
                <div className="modal-section-copy">
                  Bind to an existing GitHub repo or create a new one.
                </div>
              </div>
              <button className="modal-mini-btn" onClick={loadGithubRepos} disabled={loadingRepos}>
                {loadingRepos ? 'Loading...' : 'Refresh Repos'}
              </button>
            </div>

            <div className="modal-segmented">
              <button
                className={repoMode === 'existing' ? 'active' : ''}
                onClick={() => setRepoMode('existing')}
              >
                Use Existing
              </button>
              <button
                className={repoMode === 'new' ? 'active' : ''}
                onClick={() => setRepoMode('new')}
              >
                Create New
              </button>
            </div>

            {repoMode === 'existing' && (
              <div className="modal-field">
                <label className="modal-label">Select GitHub Repo</label>
                {githubRepos.length > 0 ? (
                  <select
                    className="modal-select"
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                  >
                    <option value="">Choose a repository...</option>
                    {githubRepos.map((r) => (
                      <option key={r.full_name} value={r.full_name}>
                        {r.full_name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="modal-input"
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    placeholder="owner/repo"
                  />
                )}
                <div className="modal-note">
                  {loadingRepos ? 'Loading repositories...' : 'Pick from your GitHub repos or type owner/repo manually.'}
                </div>
              </div>
            )}

            {repoMode === 'new' && (
              <div className="modal-compact-grid">
                <div className="modal-field">
                  <label className="modal-label">New Repo Name</label>
                  <input
                    className="modal-input"
                    value={repoName}
                    onChange={(e) => setRepoName(e.target.value)}
                    placeholder="customer-portal"
                  />
                </div>
                <div className="modal-field">
                  <label className="modal-label">Visibility</label>
                  <select
                    className="modal-select"
                    value={repoPrivate ? 'private' : 'public'}
                    onChange={(e) => setRepoPrivate(e.target.value === 'private')}
                  >
                    <option value="private">Private</option>
                    <option value="public">Public</option>
                  </select>
                </div>
              </div>
            )}
          </div>

          <div className="modal-field full">
            <label className="modal-label">Fallback Order</label>
            <input
              className="modal-input"
              value={agentOrder}
              onChange={(e) => setAgentOrder(e.target.value)}
              placeholder="claude-code, kiro, codex"
            />
          </div>

          <div className="modal-section">
            <div className="modal-section-head">
              <div>
                <div className="modal-section-title">Phase Approvals</div>
                <div className="modal-section-copy">
                  Select which phases require human approval before proceeding.
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  className="modal-mini-btn"
                  onClick={() => setPhaseApprovals({
                    requirement: true,
                    design: true,
                    planning: true,
                    implementation: true,
                    testing: true,
                    documentation: true,
                  })}
                >
                  All
                </button>
                <button
                  className="modal-mini-btn"
                  onClick={() => setPhaseApprovals({
                    requirement: false,
                    design: false,
                    planning: false,
                    implementation: false,
                    testing: false,
                    documentation: false,
                  })}
                >
                  None
                </button>
              </div>
            </div>

            <div className="modal-checks" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px' }}>
              <label className="modal-check">
                <input
                  type="checkbox"
                  checked={phaseApprovals.requirement}
                  onChange={(e) => setPhaseApprovals({...phaseApprovals, requirement: e.target.checked})}
                />
                Requirements
              </label>
              <label className="modal-check">
                <input
                  type="checkbox"
                  checked={phaseApprovals.design}
                  onChange={(e) => setPhaseApprovals({...phaseApprovals, design: e.target.checked})}
                />
                Design
              </label>
              <label className="modal-check">
                <input
                  type="checkbox"
                  checked={phaseApprovals.planning}
                  onChange={(e) => setPhaseApprovals({...phaseApprovals, planning: e.target.checked})}
                />
                Planning
              </label>
              <label className="modal-check">
                <input
                  type="checkbox"
                  checked={phaseApprovals.implementation}
                  onChange={(e) => setPhaseApprovals({...phaseApprovals, implementation: e.target.checked})}
                />
                Implementation
              </label>
              <label className="modal-check">
                <input
                  type="checkbox"
                  checked={phaseApprovals.testing}
                  onChange={(e) => setPhaseApprovals({...phaseApprovals, testing: e.target.checked})}
                />
                Testing
              </label>
              <label className="modal-check">
                <input
                  type="checkbox"
                  checked={phaseApprovals.documentation}
                  onChange={(e) => setPhaseApprovals({...phaseApprovals, documentation: e.target.checked})}
                />
                Documentation
              </label>
            </div>
          </div>

          <div className="modal-checks">
            <label className="modal-check">
              <input
                type="checkbox"
                checked={agentFallback}
                onChange={(e) => setAgentFallback(e.target.checked)}
              />
              Enable agent fallback
            </label>
            <label className="modal-check">
              <input
                type="checkbox"
                checked={startPipeline}
                onChange={(e) => setStartPipeline(e.target.checked)}
              />
              Start pipeline immediately
            </label>
          </div>
        </div>

        <div className="modal-foot">
          <button className="modal-cancel" onClick={onClose}>Cancel</button>
          <button className="modal-submit" onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Creating...' : 'Create Project'}
          </button>
        </div>
      </div>

      {/* File Browser Popup */}
      {showFileBrowser && (
        <div className="file-browser-overlay" onClick={handleCancelFileBrowser}>
          <div className="file-browser-modal" onClick={(e) => e.stopPropagation()}>
            <div className="file-browser-header">
              <h3>Select File</h3>
              <button className="modal-close" onClick={handleCancelFileBrowser}>✕</button>
            </div>
            <div className="file-browser">
              <div className="file-browser-path">
                📁 {currentPath}
                {currentPath !== '~' && (
                  <button
                    className="modal-mini-btn"
                    onClick={() => loadFileBrowser(currentPath + '/..')}
                  >
                    ⬆ Up
                  </button>
                )}
              </div>
              <div className="file-browser-list">
                {loadingBrowser ? (
                  <div className="file-browser-loading">Loading...</div>
                ) : (
                  browserEntries.map((entry) => (
                    <div
                      key={entry.path}
                      className={`file-browser-item ${entry.is_dir ? 'dir' : 'file'} ${tempSelectedPath === entry.path ? 'selected' : ''}`}
                      onClick={() => handleFileSelect(entry.path, entry.is_dir)}
                    >
                      <span className="file-icon">{entry.is_dir ? '📁' : '📄'}</span>
                      <span className="file-name">{entry.name}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
            {tempSelectedPath && (
              <div className="file-browser-selected">Selected: {tempSelectedPath}</div>
            )}
            <div className="file-browser-footer">
              <button className="modal-cancel" onClick={handleCancelFileBrowser}>Cancel</button>
              <button
                className="modal-submit"
                onClick={handleConfirmFile}
                disabled={!tempSelectedPath}
              >
                Select File
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default StartProjectModal;
