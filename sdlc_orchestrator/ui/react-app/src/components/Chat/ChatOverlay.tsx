import { useState, useRef, useEffect } from 'react';
import { useChat } from '../../hooks/useChat';
import { filesystemApi } from '../../services/api';
import './ChatOverlay.css';

interface ChatOverlayProps {
  isOpen: boolean;
  onClose: () => void;
}

interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

const ChatOverlay = ({ isOpen, onClose }: ChatOverlayProps) => {
  const [input, setInput] = useState('');
  const [showFolderBrowser, setShowFolderBrowser] = useState(false);
  const [currentPath, setCurrentPath] = useState('~');
  const [fsEntries, setFsEntries] = useState<FsEntry[]>([]);
  const [selectedExecutor, setSelectedExecutor] = useState<string>('auto');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { messages, isLoading, sendMessage, clearChat, cwd, setCwd, executor } = useChat();

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  useEffect(() => {
    if (showFolderBrowser) {
      browsePath(currentPath);
    }
  }, [showFolderBrowser]);

  const browsePath = async (path: string) => {
    try {
      const data = await filesystemApi.browse(path, false);
      setCurrentPath(data.path);
      setFsEntries(data.entries);
    } catch (error) {
      console.error('Failed to browse path:', error);
    }
  };

  const handleSelectPath = async (path: string, isDir: boolean) => {
    if (isDir) {
      browsePath(path);
    }
  };

  const handleConfirmPath = async () => {
    await setCwd(currentPath);
    setShowFolderBrowser(false);
  };

  const handleSend = () => {
    if (!input.trim() || isLoading) return;

    sendMessage(input, selectedExecutor !== 'auto' ? selectedExecutor : undefined);
    setInput('');
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!isOpen) return null;

  return (
    <div className="chat-overlay-wrapper">
      <div className={`chat-overlay ${isOpen ? 'open' : ''}`}>
        <div className="chat-head">
          <div className="chat-avatar">🤖</div>
          <div className="chat-head-info">
            <div className="chat-agent-row">
              <div className="chat-head-title">{executor?.label || 'Assistant'}</div>
              {executor?.options && executor.options.length > 0 && (
                <select
                  className="chat-agent-select"
                  value={selectedExecutor}
                  onChange={(e) => setSelectedExecutor(e.target.value)}
                >
                  <option value="auto">Auto</option>
                  {executor.options.map((opt) => (
                    <option key={opt.name} value={opt.name} disabled={!opt.available}>
                      {opt.label} {!opt.available && '(unavailable)'}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <div
              className="chat-head-cwd"
              title={cwd}
              onClick={() => setShowFolderBrowser(true)}
            >
              📁 {cwd.split('/').pop() || cwd}
            </div>
          </div>
          <div className="chat-online" />
          <button className="chat-close" onClick={onClose}>
            ✕
          </button>
        </div>

        {showFolderBrowser && (
          <div className="fb-overlay">
            <div className="fb-header">
              <button
                className="fb-up"
                onClick={() => {
                  const parentPath = currentPath.split('/').slice(0, -1).join('/') || '/';
                  browsePath(parentPath);
                }}
              >
                ↑ Up
              </button>
              <div className="fb-path">{currentPath}</div>
            </div>
            <div className="fb-list">
              {fsEntries.map((entry) => (
                <div
                  key={entry.path}
                  className="fb-item"
                  onClick={() => handleSelectPath(entry.path, entry.is_dir)}
                >
                  {entry.is_dir ? '📁' : '📄'} {entry.name}
                </div>
              ))}
            </div>
            <div className="fb-footer">
              <button className="fb-select" onClick={handleConfirmPath}>
                ✓ Select This Folder
              </button>
              <button className="fb-cancel" onClick={() => setShowFolderBrowser(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="chat-welcome">
              <p>{executor?.greeting || 'Hello! How can I help you today?'}</p>
            </div>
          )}

          {messages.map((msg, idx) => (
            <div key={idx} className={`chat-bubble-wrap ${msg.role}`}>
              <div className={`chat-bubble ${msg.role}`}>
                {msg.content}
              </div>
              <div className="chat-time">
                {new Date(msg.timestamp).toLocaleTimeString()}
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="chat-bubble-wrap assistant">
              <div className="chat-bubble thinking">
                Thinking...
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-row">
          <button
            className="chat-new-btn"
            onClick={clearChat}
            title="Clear conversation"
          >
            🗑️
          </button>

          <div className="chat-input-wrap">
            <textarea
              className="chat-input"
              placeholder={executor?.placeholder || 'Type a message...'}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              rows={1}
            />
          </div>

          <button
            className="chat-send"
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            aria-label="Send message"
          >
            ➤
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatOverlay;
