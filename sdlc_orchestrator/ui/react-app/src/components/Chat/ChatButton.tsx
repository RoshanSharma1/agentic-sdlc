import './ChatButton.css';

interface ChatButtonProps {
  onClick: () => void;
}

const ChatButton = ({ onClick }: ChatButtonProps) => {
  return (
    <button className="chat-fab" onClick={onClick} aria-label="Open chat">
      💬
    </button>
  );
};

export default ChatButton;
