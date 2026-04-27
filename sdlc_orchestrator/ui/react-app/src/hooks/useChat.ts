import { useState, useCallback, useRef, useEffect } from 'react';
import { chatApi } from '../services/api';
import type { ChatMessage, ChatExecutor } from '../types';

export const useChat = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [cwd, setCwdState] = useState<string>('');
  const [executor, setExecutor] = useState<ChatExecutor | null>(null);
  const pollingRef = useRef<number | null>(null);

  useEffect(() => {
    const fetchMeta = async () => {
      try {
        const cwdData = await chatApi.getCwd();
        setCwdState(cwdData.cwd);

        const metaData = await chatApi.getMeta();
        setExecutor(metaData);
      } catch (error) {
        console.error('Failed to fetch chat metadata:', error);
      }
    };

    fetchMeta();
  }, []);

  const setCwd = useCallback(async (path: string) => {
    try {
      const data = await chatApi.setCwd(path);
      setCwdState(data.cwd);
    } catch (error) {
      console.error('Failed to set cwd:', error);
    }
  }, []);

  const pollJobUpdates = useCallback(async (jobId: string, offset: number = 0) => {
    try {
      const data = await chatApi.pollJob(jobId, offset);

      if (data.lines.length > 0) {
        const newMessage: ChatMessage = {
          role: 'assistant',
          content: data.lines.join('\n'),
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, newMessage]);
      }

      if (!data.done) {
        pollingRef.current = window.setTimeout(() => pollJobUpdates(jobId, data.offset), 500);
      } else {
        setIsLoading(false);
      }
    } catch (error) {
      console.error('Failed to poll job:', error);
      setIsLoading(false);
    }
  }, []);

  const sendMessage = useCallback(
    async (content: string, executorName?: string) => {
      const userMessage: ChatMessage = {
        role: 'user',
        content,
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMessage]);
      setIsLoading(true);

      try {
        const response = await chatApi.sendMessage(content, executorName);
        pollJobUpdates(response.job_id);
      } catch (error) {
        console.error('Failed to send message:', error);
        setIsLoading(false);
      }
    },
    [pollJobUpdates]
  );

  const clearChat = useCallback(async () => {
    try {
      await chatApi.clear();
      setMessages([]);
    } catch (error) {
      console.error('Failed to clear chat:', error);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (pollingRef.current !== null) {
        window.clearTimeout(pollingRef.current);
      }
    };
  }, []);

  return {
    messages,
    isLoading,
    sendMessage,
    clearChat,
    cwd,
    setCwd,
    executor,
  };
};
