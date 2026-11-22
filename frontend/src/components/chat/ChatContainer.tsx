import React, { useState, useRef, useEffect } from 'react';
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
  onOpenAuth: () => void;
  onOpenGuestPortal: () => void;
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
  onInspectOrderNumber,
  onOpenAuth,
  onOpenGuestPortal,
}) => {
  const { accessToken, isAuthenticated } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const quickPrompts = [
    { label: 'What are my recent orders?', icon: <Package className="w-3 h-3" /> },
    { label: 'Check status of my latest shipment', icon: <Package className="w-3 h-3" /> },
    { label: 'Recommend popular electronics', icon: <ShoppingBag className="w-3 h-3" /> },
    { label: 'What is your return policy?', icon: <HelpCircle className="w-3 h-3" /> },
  ];

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend || inputValue).trim();
    if (!text || isLoading) return;

    setErrorBanner(null);

    // If not authenticated and asking an order question, prompt user
    if (!isAuthenticated) {
      const lower = text.toLowerCase();
      const isOrderIntent =
        lower.includes('order') ||
        lower.includes('track') ||
        lower.includes('shipment') ||
        lower.includes('delivery');

      if (isOrderIntent) {
        // Add user question
        const userMsg: ChatMessage = {
          id: Math.random().toString(36).substring(2, 9),
          sender: 'user',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          text: text,
        };

        // Add assistant explanation
        const botMsg: ChatMessage = {
          id: Math.random().toString(36).substring(2, 9),
          sender: 'assistant',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          destination: 'order_support',
          orderAnswer:
            'To access personal order details, please sign in to your account. If you checked out as a guest, use our secure Guest Order Lookup portal to verify your order with a 6-digit email code.',
        };

        setMessages((prev) => [...prev, userMsg, botMsg]);
        setInputValue('');
        return;
      }
    }

    if (!accessToken) {
      setErrorBanner('Please sign in to chat with the supervisor agent.');
      return;
    }

    const userMessage: ChatMessage = {
      id: Math.random().toString(36).substring(2, 9),
      sender: 'user',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      text: text,
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const response: SupervisorRouteResponse = await api.chat.sendQuestion(text, accessToken);

      const botMessage: ChatMessage = {
        id: Math.random().toString(36).substring(2, 9),
        sender: 'assistant',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        destination: response.destination,
        orderAnswer: response.order_answer,
        shoppingResponse: response.shopping_response,
        generalAnswer: response.general_answer,
      };

      setMessages((prev) => [...prev, botMessage]);
    } catch (err: unknown) {
      let msg = 'Failed to get a response from the AI supervisor.';
      if (err instanceof ApiError) {
        msg = err.message;
      }
      setErrorBanner(msg);

      const errorBotMessage: ChatMessage = {
        id: Math.random().toString(36).substring(2, 9),
        sender: 'assistant',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        text: `Sorry, I encountered an issue: ${msg}`,
        isError: true,
      };
      setMessages((prev) => [...prev, errorBotMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
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
              Ask about your order statuses, track recent deliveries, discover new products, or ask general shopping questions.
            </p>

            {/* Quick Prompt Chips */}
            <div className="flex flex-wrap justify-center gap-2 max-w-md">
              {quickPrompts.map((p, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => handleSendMessage(p.label)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-50 hover:bg-slate-100 border border-slate-200 text-slate-700 transition-colors"
                >
                  <span className="text-slate-400">{p.icon}</span>
                  <span>{p.label}</span>
                </button>
              ))}
            </div>

            {/* Guest / Auth Banner if unauthenticated */}
            {!isAuthenticated && (
              <div className="mt-8 p-3.5 bg-slate-50/80 rounded-xl border border-slate-200/80 text-xs text-slate-600 max-w-md w-full flex items-center justify-between gap-3">
                <div className="text-left">
                  <p className="font-semibold text-slate-800">Looking for a guest checkout?</p>
                  <p className="text-[11px] text-slate-500">Track orders without logging in.</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={onOpenGuestPortal} className="text-xs">
                    Guest Lookup
                  </Button>
                  <Button variant="secondary" size="sm" onClick={onOpenAuth} className="text-xs">
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
              />
            ))}
            {isLoading && (
              <div className="flex items-center gap-2 text-xs text-slate-500 p-2 italic animate-pulse">
                <div className="w-2 h-2 rounded-full bg-emerald-500"></div>
                <span>Gemini supervisor is routing and analyzing your request...</span>
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
            <button onClick={() => setErrorBanner(null)} className="text-rose-600 font-bold ml-2">
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
                : 'Ask a question or sign in to track personal orders...'
            }
            rows={1}
            className="flex-1 max-h-32 min-h-[38px] p-2 text-sm bg-transparent resize-none border-none focus:outline-none text-slate-900 placeholder-slate-400 leading-snug"
          />
          <Button
            type="button"
            variant="primary"
            size="sm"
            aria-label="Send message"
            onClick={() => handleSendMessage()}
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
