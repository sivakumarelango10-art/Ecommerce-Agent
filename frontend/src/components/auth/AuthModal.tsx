import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, ArrowRight, MessageSquareText } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Alert } from '../ui/Alert';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAuthSuccess?: () => void;
  pendingQuestion?: string | null;
  promptNotice?: string | null;
}

export const AuthModal: React.FC<AuthModalProps> = ({
  isOpen,
  onClose,
  onAuthSuccess,
  pendingQuestion,
  promptNotice,
}) => {
  const { login, register, isLoading, error, clearError } = useAuth();
  const [tab, setTab] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const handleClose = useCallback(() => {
    if (isLoading) return;
    setPassword('');
    setConfirmPassword('');
    setFormError(null);
    clearError();
    onClose();
  }, [clearError, isLoading, onClose]);

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    } else if (previousFocusRef.current) {
      previousFocusRef.current.focus?.();
      previousFocusRef.current = null;
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen, handleClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    clearError();

    if (!email || !password) {
      setFormError('Please enter both email and password.');
      return;
    }

    if (password.length < 12 || password.length > 128) {
      setFormError('Password must be between 12 and 128 characters long.');
      return;
    }

    if (tab === 'register') {
      if (!displayName.trim()) {
        setFormError('Display name is required.');
        return;
      }
      if (password !== confirmPassword) {
        setFormError('Passwords do not match.');
        return;
      }
    }

    try {
      if (tab === 'login') {
        await login(email, password);
      } else {
        await register(email, password, displayName.trim());
      }
      setPassword('');
      setConfirmPassword('');
      setFormError(null);
      onAuthSuccess?.();
      onClose();
    } catch {
      // Error is stored in auth context
    }
  };

  const handleTabSwitch = (newTab: 'login' | 'register') => {
    setTab(newTab);
    setPassword('');
    setConfirmPassword('');
    setFormError(null);
    clearError();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-sm animate-in fade-in duration-200"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) {
          handleClose();
        }
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-modal-title"
        aria-describedby={pendingQuestion ? 'auth-modal-pending-prompt' : undefined}
        className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-md overflow-hidden transform transition-all"
      >
        {/* Header */}
        <div className="relative p-6 pb-4 border-b border-slate-100 flex flex-col items-center text-center">
          <button
            type="button"
            onClick={handleClose}
            disabled={isLoading}
            className="absolute top-4 right-4 p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors disabled:opacity-40"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>

          {/* Official StoreFlow Logo */}
          <div className="mb-3">
            <img
              src="/storeflow-logo.svg"
              alt="StoreFlow - Order Support & Shopping Agent"
              className="h-9 sm:h-10 w-auto object-contain mx-auto"
            />
          </div>

          <h3 id="auth-modal-title" className="text-lg font-bold text-slate-900">
            {tab === 'login' ? 'Welcome Back' : 'Create Customer Account'}
          </h3>
          <p className="text-xs text-slate-500 mt-1 max-w-xs">
            {tab === 'login'
              ? 'Sign in to access your orders and personalized support.'
              : 'Sign up to track purchases and chat with your AI order assistant.'}
          </p>
        </div>

        {/* Pending Question Context Banner */}
        {(pendingQuestion || promptNotice) && (
          <div
            id="auth-modal-pending-prompt"
            className="mx-4 mt-4 p-3 rounded-xl bg-emerald-50 border border-emerald-200 text-left space-y-1"
          >
            <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-800">
              <MessageSquareText className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
              <span>{promptNotice || 'Sign in to continue your question'}</span>
            </div>
            {pendingQuestion && (
              <p className="text-xs text-emerald-900 italic line-clamp-2 pl-5">
                &ldquo;{pendingQuestion}&rdquo;
              </p>
            )}
          </div>
        )}

        {/* Tab Selector */}
        <div className="flex border-b border-slate-100 bg-slate-50/70 p-1 m-4 rounded-xl">
          <button
            type="button"
            aria-pressed={tab === 'login'}
            onClick={() => handleTabSwitch('login')}
            className={`flex-1 py-1.5 text-xs font-semibold rounded-lg transition-all ${
              tab === 'login'
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-500 hover:text-slate-800'
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            aria-pressed={tab === 'register'}
            onClick={() => handleTabSwitch('register')}
            className={`flex-1 py-1.5 text-xs font-semibold rounded-lg transition-all ${
              tab === 'register'
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-500 hover:text-slate-800'
            }`}
          >
            Register
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="px-6 pb-6 space-y-4">
          {(formError || error) && (
            <Alert variant="error" title="Authentication Error">
              {formError || error}
            </Alert>
          )}

          {tab === 'register' && (
            <div>
              <Input
                label="Display Name"
                type="text"
                placeholder="Jane Doe"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                required
                autoFocus
              />
            </div>
          )}

          <div>
            <Input
              label="Email Address"
              type="email"
              placeholder="customer@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus={tab === 'login'}
            />
          </div>

          <div>
            <Input
              label="Password"
              type="password"
              placeholder="••••••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <p className="mt-1 text-[11px] text-slate-500">
              Must be between 12 and 128 characters.
            </p>
          </div>

          {tab === 'register' && (
            <div>
              <Input
                label="Confirm Password"
                type="password"
                placeholder="••••••••••••"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
            </div>
          )}

          <div className="pt-2">
            <Button
              type="submit"
              variant="secondary"
              size="md"
              className="w-full bg-slate-900 hover:bg-slate-800 text-white font-medium"
              isLoading={isLoading}
            >
              <span>{tab === 'login' ? 'Sign In to Account' : 'Create Account'}</span>
              <ArrowRight className="w-4 h-4 ml-1.5" />
            </Button>
          </div>

          <div className="text-center">
            <span className="text-xs text-slate-500">
              {tab === 'login' ? "Don't have an account yet? " : 'Already have an account? '}
              <button
                type="button"
                onClick={() => handleTabSwitch(tab === 'login' ? 'register' : 'login')}
                className="font-semibold text-emerald-600 hover:text-emerald-700 underline underline-offset-2"
              >
                {tab === 'login' ? 'Register here' : 'Sign in here'}
              </button>
            </span>
          </div>
        </form>
      </div>
    </div>
  );
};
