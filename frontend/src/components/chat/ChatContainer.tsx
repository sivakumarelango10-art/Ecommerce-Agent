import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Send,
  Package,
  ShoppingBag,
  HelpCircle,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api, ApiError } from '../../api/client';
import { ChatMessage, SupervisorRouteResponse } from '../../types/api';
import { ChatMessageItem } from './ChatMessageItem';
import { Button } from '../ui/Button';

interface ChatContainerProps {
  onInspectOrderNumber: (orderNumber: string) => void;
  onOpenAuth: (pendingQuestion?: string, reason?: string) => void;
  onOpenGuestPortal: () => void;
  authCancelSignal?: number;
}

interface PendingChatQuestion {
  id: string;
  text: string;
}

const ORDER_INTENT_PATTERNS = [
  /\bORD-[A-Za-z0-9-]+\b/i,
  /\bmy\s+(?:recent\s+|past\s+|previous\s+|latest\s+)?orders?\b/i,
  /\bwhere\s+is\s+my\s+(?:order|package|shipment|delivery)\b/i,
  /\b(?:track|tracking|check|status\s+of|cancel|items?\s+in)\s+(?:my\s+|the\s+|latest\s+)?(?:order|shipment|delivery|package)\b/i,
];

function createMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).substring(2, 11);
}

function isProtectedOrderQuestion(question: string): boolean {
  if (ORDER_INTENT_PATTERNS.some((pattern) => pattern.test(question))) {
    return true;
  }
  const lower = question.toLowerCase();
  if (lower.includes('policy') || lower.includes('in order to')) {
    return false;
  }
  return /\b(?:order|shipment|delivery|tracking)\b/i.test(question);
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
  onInspectOrderNumber,
  onOpenAuth,
  onOpenGuestPortal,
  authCancelSignal = 0,
}) => {
  const { accessToken, isAuthenticated, logout } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<PendingChatQuestion | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const replayedQuestionIdsRef = useRef<Set<string>>(new Set());
  const prevCancelSignalRef = useRef<number>(authCancelSignal);

  const scrollToBottom = () => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  // Clear queued auto-replay when user cancels/closes login without authenticating
  useEffect(() => {
    if (authCancelSignal !== prevCancelSignalRef.current) {
      prevCancelSignalRef.current = authCancelSignal;
      if (!isAuthenticated) {
        setPendingQuestion(null);
      }
    }
  }, [authCancelSignal, isAuthenticated]);

  const queueAuthRequiredFlow = useCallback(
    (questionText: string, options: { appendUserMessage?: boolean; reason?: string } = {}) => {
      const { appendUserMessage = true, reason } = options;
      const nowTime = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

      const nextMessages: ChatMessage[] = [];
      if (appendUserMessage) {
        nextMessages.push({
          id: createMessageId(),
          sender: 'user',
          timestamp: nowTime,
          text: questionText,
        });
      }
      nextMessages.push({
        id: createMessageId(),
        sender: 'assistant',
        timestamp: nowTime,
        destination: 'order_support',
        orderAnswer:
          'To access personal order details, please sign in to your account. If you checked out as a guest, use our secure Guest Order Lookup portal to verify your order with a 6-digit email code.',
        requiresAuthPrompt: true,
        pendingQuestionText: questionText,
      });

      setMessages((prev) => [...prev, ...nextMessages]);
      setPendingQuestion({ id: createMessageId(), text: questionText });
      onOpenAuth(questionText, reason);
    },
    [onOpenAuth]
  );

  const executeQuestion = useCallback(
    async (
      text: string,
      options: { skipUserMessage?: boolean; tokenOverride?: string | null } = {}
    ) => {
      const { skipUserMessage = false, tokenOverride } = options;
      const activeToken = tokenOverride !== undefined ? tokenOverride : accessToken;

      if (!skipUserMessage) {
        const userMessage: ChatMessage = {
          id: createMessageId(),
          sender: 'user',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          text,
        };
        setMessages((prev) => [...prev, userMessage]);
        setInputValue('');
      }

      setIsLoading(true);
      setErrorBanner(null);

      try {
        const response: SupervisorRouteResponse = await api.chat.sendQuestion(text, activeToken);

        // Defensive check: if backend gated an unauthenticated request to order_support,
        // preserve the question and initiate login + seamless replay.
        if (!activeToken && response.destination === 'order_support') {
          setMessages((prev) => [
            ...prev,
            {
              id: createMessageId(),
              sender: 'assistant',
              timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              destination: 'order_support',
              orderAnswer:
                response.order_answer ||
                'To access personal order details, please sign in to your account.',
              requiresAuthPrompt: true,
              pendingQuestionText: text,
            },
          ]);
          setPendingQuestion({ id: createMessageId(), text });
          onOpenAuth(text, 'Sign in to access your personal order details');
          return;
        }

        const botMessage: ChatMessage = {
          id: createMessageId(),
          sender: 'assistant',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          destination: response.destination,
          orderAnswer: response.order_answer,
          shoppingResponse: response.shopping_response,
          generalAnswer: response.general_answer,
        };

        setMessages((prev) => [...prev, botMessage]);
      } catch (err: unknown) {
        if (err instanceof ApiError && err.status === 401) {
          logout();
          queueAuthRequiredFlow(text, {
            appendUserMessage: false,
            reason: 'Your session expired. Sign in to complete your question.',
          });
          return;
        }

        const msg = err instanceof ApiError ? err.message : 'Failed to get a response from the AI supervisor.';
        setErrorBanner(msg);

        const errorBotMessage: ChatMessage = {
          id: createMessageId(),
          sender: 'assistant',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          text: `Sorry, I encountered an issue: ${msg}`,
          isError: true,
        };
        setMessages((prev) => [...prev, errorBotMessage]);
      } finally {
        setIsLoading(false);
      }
    },
    [accessToken, logout, onOpenAuth, queueAuthRequiredFlow]
  );

  // Automatically resume pending question exactly once after successful login
  useEffect(() => {
    if (!isAuthenticated || !accessToken || !pendingQuestion) {
      return;
    }
    if (replayedQuestionIdsRef.current.has(pendingQuestion.id)) {
      return;
    }
    replayedQuestionIdsRef.current.add(pendingQuestion.id);
    const questionText = pendingQuestion.text;
    setPendingQuestion(null);
    void executeQuestion(questionText, {
      skipUserMessage: true,
      tokenOverride: accessToken,
    });
  }, [accessToken, executeQuestion, isAuthenticated, pendingQuestion]);

  const handleTriggerSignInForQuestion = (questionText?: string) => {
    if (questionText && !isAuthenticated) {
      setPendingQuestion({ id: createMessageId(), text: questionText });
      onOpenAuth(questionText, 'Sign in to continue your order inquiry');
      return;
    }
    onOpenAuth(questionText);
  };

  const quickPrompts = [
    { label: 'What are my recent orders?', icon: <Package className="w-3 h-3" /> },
    { label: 'Check status of my latest shipment', icon: <Package className="w-3 h-3" /> },
    { label: 'Recommend popular electronics', icon: <ShoppingBag className="w-3 h-3" /> },
    { label: 'What is your return policy?', icon: <HelpCircle className="w-3 h-3" /> },
  ];

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend ?? inputValue).trim();
    if (!text || isLoading) return;

    setErrorBanner(null);

    if (!isAuthenticated && isProtectedOrderQuestion(text)) {
      setInputValue('');
      queueAuthRequiredFlow(text, {
        appendUserMessage: true,
        reason: 'Sign in to check personal order details',
      });
      return;
    }

    await executeQuestion(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleSendMessage();
    }
  };

  return (
    <div className="flex flex-col h-[72vh] min-h-[460px] lg:h-full bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
      {/* Subtle Assistant Context Header */}
      <div className="px-4 sm:px-5 py-3 bg-white border-b border-slate-100 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-500" aria-hidden="true"></span>
          <h2 className="text-xs sm:text-sm font-semibold text-slate-900 tracking-tight">
            StoreFlow AI Assistant
          </h2>
        </div>
        <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200">
          {isAuthenticated ? 'Order & Shopping Access' : 'Guest Mode · Products & Policies'}
        </span>
      </div>

      {/* Chat Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-2">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col justify-center items-center text-center p-6 max-w-lg mx-auto">
            {/* Official StoreFlow Logo */}
            <div className="mb-4">
              <img
                src="/storeflow-logo.svg"
                alt="StoreFlow - Order Support & Shopping Agent"
                className="h-11 sm:h-12 w-auto object-contain mx-auto"
              />
            </div>
            <h3 className="text-lg font-bold text-slate-900 tracking-tight mb-1">
              How can I assist your shopping today?
            </h3>
            <p className="text-xs text-slate-500 leading-relaxed mb-6">
              Ask product recommendations or general questions right away, or sign in to track personal deliveries and order history.
            </p>

            {/* Quick Prompt Chips */}
            <div className="flex flex-wrap justify-center gap-2 max-w-md">
              {quickPrompts.map((p, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => void handleSendMessage(p.label)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-50 hover:bg-slate-100 border border-slate-200 text-slate-700 transition-colors"
                >
                  <span className="text-slate-400">{p.icon}</span>
                  <span>{p.label}</span>
                </button>
              ))}
            </div>

            {/* Guest / Auth Banner if unauthenticated */}
            {!isAuthenticated && (
              <div className="mt-8 p-3.5 bg-slate-50/80 rounded-xl border border-slate-200/80 text-xs text-slate-600 max-w-md w-full flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="text-left">
                  <p className="font-semibold text-slate-800">Looking for a guest checkout?</p>
                  <p className="text-[11px] text-slate-500">Track orders without logging in.</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={onOpenGuestPortal} className="text-xs">
                    Guest Lookup
                  </Button>
                  <Button variant="secondary" size="sm" onClick={() => onOpenAuth()} className="text-xs">
                    Sign In
                  </Button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <>
            {messages.map((m) => (
              <ChatMessageItem
                key={m.id}
                message={m}
                onInspectOrderNumber={onInspectOrderNumber}
                onTriggerSignIn={handleTriggerSignInForQuestion}
                onOpenGuestPortal={onOpenGuestPortal}
              />
            ))}
            {isLoading && (
              <div className="flex items-center gap-2 text-xs text-slate-500 p-2 italic animate-pulse" aria-live="polite">
                <div className="w-2 h-2 rounded-full bg-emerald-500"></div>
                <span>StoreFlow AI supervisor is analyzing your request...</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input Box Area */}
      <div className="p-3 sm:p-4 bg-slate-50/60 border-t border-slate-100">
        {errorBanner && (
          <div className="mb-2 p-2 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-700 flex items-center justify-between">
            <span>{errorBanner}</span>
            <button onClick={() => setErrorBanner(null)} className="text-rose-600 font-bold ml-2" aria-label="Dismiss error">
              ×
            </button>
          </div>
        )}

        <div className="relative flex items-end gap-2 bg-white rounded-xl border border-slate-200 focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-100 transition-all p-1.5 shadow-xs">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              isAuthenticated
                ? 'Ask about your orders, track shipments, or request product advice...'
                : 'Ask about products or policies as a guest, or ask about orders to sign in...'
            }
            aria-label="Ask StoreFlow Assistant"
            rows={1}
            className="flex-1 max-h-32 min-h-[38px] p-2 text-sm bg-transparent resize-none border-none focus:outline-none text-slate-900 placeholder-slate-400 leading-snug"
          />
          <Button
            type="button"
            variant="primary"
            size="sm"
            aria-label="Send message"
            onClick={() => void handleSendMessage()}
            disabled={!inputValue.trim() || isLoading}
            isLoading={isLoading}
            className="h-9 px-3 rounded-lg flex-shrink-0"
          >
            <Send className="w-4 h-4" />
          </Button>
        </div>
      </div>
    </div>
  );
};
