import React, { useState, useEffect, useCallback } from 'react';
import {
  X,
  Sparkles,
  KeyRound,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  ExternalLink,
  ShieldCheck,
  Eye,
  EyeOff,
  Trash2,
  RefreshCw,
  ToggleLeft,
  ToggleRight,
  Info,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api, ApiError } from '../../api/client';
import {
  PersonalGeminiKeyResponse,
  ValidatePersonalGeminiKeyResponse,
} from '../../types/api';
import { Button } from '../ui/Button';
import { Alert } from '../ui/Alert';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return 'Never';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoString;
  }
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose }) => {
  const { accessToken, logout } = useAuth();

  // Key status state
  const [keyStatus, setKeyStatus] = useState<PersonalGeminiKeyResponse | null>(null);
  const [isLoadingStatus, setIsLoadingStatus] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Form mode: adding or replacing
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [showPlaintext, setShowPlaintext] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [validationResult, setValidationResult] = useState<ValidatePersonalGeminiKeyResponse | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // Toggle & delete states
  const [isToggling, setIsToggling] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);

  const fetchKeyStatus = useCallback(async () => {
    if (!accessToken) return;
    setIsLoadingStatus(true);
    setStatusError(null);
    try {
      const data = await api.gemini.getStatus(accessToken);
      setKeyStatus(data);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setStatusError('Your session has expired. Please sign in again.');
          logout();
          return;
        }
        setStatusError(err.message);
      } else if (err instanceof Error) {
        setStatusError(err.message);
      } else {
        setStatusError('Failed to load Gemini settings.');
      }
    } finally {
      setIsLoadingStatus(false);
    }
  }, [accessToken, logout]);

  useEffect(() => {
    if (isOpen) {
      fetchKeyStatus();
      setSuccessBanner(null);
      setIsFormOpen(false);
      setApiKeyInput('');
      setShowPlaintext(false);
      setValidationResult(null);
      setFormError(null);
      setShowDeleteConfirm(false);
    }
  }, [isOpen, fetchKeyStatus]);

  // Handle ESC key to close modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        handleClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen]);

  const handleClose = () => {
    // Clear sensitive key input on close
    setApiKeyInput('');
    setShowPlaintext(false);
    setValidationResult(null);
    setFormError(null);
    setShowDeleteConfirm(false);
    onClose();
  };

  const handleValidate = async () => {
    const key = apiKeyInput.trim();
    if (!key) {
      setFormError('Please enter an API key to validate.');
      return;
    }
    if (!accessToken) {
      setFormError('You must be signed in to validate an API key.');
      return;
    }

    setIsValidating(true);
    setFormError(null);
    setValidationResult(null);

    try {
      const res = await api.gemini.validateKey(key, accessToken);
      setValidationResult(res);
      if (!res.is_valid) {
        setFormError(res.message);
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setFormError('Your session has expired. Please sign in again.');
          logout();
          return;
        }
        setFormError(err.message);
      } else if (err instanceof Error) {
        setFormError(err.message);
      } else {
        setFormError('Validation request failed.');
      }
    } finally {
      setIsValidating(false);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    const key = apiKeyInput.trim();
    if (!key) {
      setFormError('Please enter an API key.');
      return;
    }
    if (!accessToken) {
      setFormError('You must be signed in to save an API key.');
      return;
    }

    setIsSaving(true);
    setFormError(null);

    try {
      const updated = await api.gemini.saveKey(key, accessToken);
      // Immediately clear the raw key input from React memory
      setApiKeyInput('');
      setShowPlaintext(false);
      setValidationResult(null);
      setIsFormOpen(false);
      setKeyStatus(updated);
      setSuccessBanner('Personal Gemini API key saved and activated successfully.');
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setFormError('Your session has expired. Please sign in again.');
          logout();
          return;
        }
        setFormError(err.message);
      } else if (err instanceof Error) {
        setFormError(err.message);
      } else {
        setFormError('Failed to save API key. Please check your credentials.');
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleToggle = async () => {
    if (!keyStatus || !accessToken || isToggling) return;
    setIsToggling(true);
    setStatusError(null);
    try {
      const nextState = !keyStatus.is_enabled;
      const updated = await api.gemini.toggleKey(nextState, accessToken);
      setKeyStatus(updated);
      setSuccessBanner(
        nextState
          ? 'Personal Gemini key enabled. Your requests will use this key first.'
          : 'Personal Gemini key disabled. Requests will use the StoreFlow platform pool.'
      );
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setStatusError('Your session has expired. Please sign in again.');
          logout();
          return;
        }
        setStatusError(err.message);
      } else {
        setStatusError('Failed to toggle personal key state.');
      }
    } finally {
      setIsToggling(false);
    }
  };

  const handleDelete = async () => {
    if (!accessToken || isDeleting) return;
    setIsDeleting(true);
    setStatusError(null);
    try {
      await api.gemini.deleteKey(accessToken);
      setKeyStatus({
        has_key: false,
        is_enabled: false,
        key_hint: null,
        status: null,
        last_validated_at: null,
        last_used_at: null,
        cooldown_until: null,
      });
      setShowDeleteConfirm(false);
      setSuccessBanner('Personal Gemini API key removed. StoreFlow will use platform keys.');
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setStatusError('Your session has expired. Please sign in again.');
          logout();
          return;
        }
        setStatusError(err.message);
      } else {
        setStatusError('Failed to remove API key.');
      }
    } finally {
      setIsDeleting(false);
    }
  };

  if (!isOpen) return null;

  const isCooldownActive = Boolean(
    keyStatus?.cooldown_until && new Date(keyStatus.cooldown_until) > new Date()
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-sm animate-in fade-in duration-200"
    >
      <div className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden transform transition-all">
        {/* Modal Header */}
        <div className="relative px-6 py-5 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-emerald-50 rounded-xl text-emerald-700 border border-emerald-100">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <h2 id="settings-modal-title" className="text-lg font-bold text-slate-900">
                AI Provider Settings
              </h2>
              <p className="text-xs text-slate-500">
                Manage personal LLM credentials for your AI shopping and order assistant
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
            aria-label="Close Settings Modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Notification Banners */}
          {successBanner && (
            <div className="relative">
              <Alert variant="success">
                {successBanner}
              </Alert>
              <button
                type="button"
                onClick={() => setSuccessBanner(null)}
                className="absolute right-3 top-3 text-emerald-700 hover:text-emerald-900"
                aria-label="Dismiss notification"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {statusError && (
            <Alert variant="error" title="Error">
              {statusError}
            </Alert>
          )}

          {/* Google Gemini Card */}
          <div className="border border-slate-200 rounded-xl p-5 bg-white shadow-sm space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-sm">
                  <KeyRound className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-slate-900">Google Gemini</h3>
                    <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">
                      gemini-2.5-flash
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Powers conversational shopping recommendations and automated order support.
                  </p>
                </div>
              </div>

              {/* Status Badge */}
              {isLoadingStatus ? (
                <div className="flex items-center gap-1.5 text-xs text-slate-400">
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Checking...</span>
                </div>
              ) : keyStatus?.has_key ? (
                <div className="flex flex-col items-end gap-1">
                  {keyStatus.status === 'INVALID' ? (
                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200">
                      <XCircle className="w-3 h-3" /> Invalid / Revoked
                    </span>
                  ) : keyStatus.status === 'CORRUPTED' ? (
                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200">
                      <AlertTriangle className="w-3 h-3" /> Decryption Error
                    </span>
                  ) : isCooldownActive ? (
                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200">
                      <AlertTriangle className="w-3 h-3" /> Cooldown Active
                    </span>
                  ) : keyStatus.is_enabled ? (
                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                      <CheckCircle2 className="w-3 h-3" /> Active & Ready
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-600 border border-slate-200">
                      Disabled
                    </span>
                  )}
                </div>
              ) : (
                <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200">
                  Platform Pool Only
                </span>
              )}
            </div>

            {/* Configured Key Details or Empty State */}
            {isLoadingStatus ? (
              <div className="py-6 text-center text-xs text-slate-400">Loading key settings...</div>
            ) : keyStatus?.has_key ? (
              <div className="pt-3 border-t border-slate-100 space-y-4">
                {/* Masked Key & Quick Controls */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 bg-slate-50 rounded-lg border border-slate-200/60">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-slate-500">Stored Key:</span>
                    <code className="text-xs font-mono font-bold tracking-wider text-slate-800 bg-white px-2 py-1 rounded border border-slate-200">
                      •••• •••• •••• {keyStatus.key_hint || '****'}
                    </code>
                  </div>

                  {/* Toggle Button */}
                  <button
                    type="button"
                    onClick={handleToggle}
                    disabled={isToggling}
                    className="inline-flex items-center gap-2 text-xs font-semibold text-slate-700 hover:text-slate-900 transition-colors"
                    aria-label={keyStatus.is_enabled ? 'Disable Personal Key' : 'Enable Personal Key'}
                  >
                    <span>{keyStatus.is_enabled ? 'Enabled' : 'Disabled'}</span>
                    {keyStatus.is_enabled ? (
                      <ToggleRight className="w-6 h-6 text-emerald-600" />
                    ) : (
                      <ToggleLeft className="w-6 h-6 text-slate-400" />
                    )}
                  </button>
                </div>

                {/* Metadata Grid */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-xs">
                  <div className="p-2.5 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="block text-slate-400 font-medium text-[11px]">Last Validated</span>
                    <span className="font-semibold text-slate-700">
                      {formatDateTime(keyStatus.last_validated_at)}
                    </span>
                  </div>
                  <div className="p-2.5 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="block text-slate-400 font-medium text-[11px]">Last Used</span>
                    <span className="font-semibold text-slate-700">
                      {formatDateTime(keyStatus.last_used_at)}
                    </span>
                  </div>
                  <div className="p-2.5 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="block text-slate-400 font-medium text-[11px]">Cooldown Status</span>
                    <span
                      className={`font-semibold ${
                        isCooldownActive ? 'text-amber-700' : 'text-slate-700'
                      }`}
                    >
                      {isCooldownActive
                        ? `Until ${formatDateTime(keyStatus.cooldown_until)}`
                        : 'No Cooldown'}
                    </span>
                  </div>
                </div>

                {/* Key Actions: Replace / Remove */}
                {!isFormOpen && !showDeleteConfirm && (
                  <div className="flex items-center justify-between pt-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setIsFormOpen(true);
                        setApiKeyInput('');
                        setValidationResult(null);
                        setFormError(null);
                      }}
                    >
                      Replace Key
                    </Button>

                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowDeleteConfirm(true)}
                      className="text-rose-600 hover:text-rose-700 hover:bg-rose-50"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      <span>Remove Key</span>
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <div className="pt-3 border-t border-slate-100 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                <p className="text-xs text-slate-600">
                  You are currently using StoreFlow&apos;s platform-managed multi-key Gemini pool. Add your own key to isolate your AI request quotas.
                </p>
                {!isFormOpen && (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => {
                      setIsFormOpen(true);
                      setApiKeyInput('');
                      setValidationResult(null);
                      setFormError(null);
                    }}
                    className="whitespace-nowrap"
                  >
                    <KeyRound className="w-3.5 h-3.5" />
                    <span>Add Personal Key</span>
                  </Button>
                )}
              </div>
            )}

            {/* Deletion Confirmation Dialog */}
            {showDeleteConfirm && (
              <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl space-y-3 animate-in fade-in duration-150">
                <div className="flex items-start gap-2.5 text-rose-900">
                  <AlertTriangle className="w-4 h-4 text-rose-600 mt-0.5 flex-shrink-0" />
                  <div className="text-xs leading-relaxed">
                    <p className="font-semibold">Remove your personal Gemini key?</p>
                    <p className="text-rose-700 mt-0.5">
                      Your stored encrypted key will be permanently deleted from StoreFlow. Your AI chats will smoothly fall back to the platform key pool.
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowDeleteConfirm(false)}
                    disabled={isDeleting}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={handleDelete}
                    isLoading={isDeleting}
                  >
                    Confirm Remove
                  </Button>
                </div>
              </div>
            )}

            {/* Add / Replace Key Form */}
            {isFormOpen && (
              <form onSubmit={handleSave} className="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-3.5 mt-3 animate-in fade-in duration-150">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                    {keyStatus?.has_key ? 'Replace Gemini API Key' : 'Add Gemini API Key'}
                  </h4>
                  <button
                    type="button"
                    onClick={() => {
                      setIsFormOpen(false);
                      setApiKeyInput('');
                      setShowPlaintext(false);
                      setValidationResult(null);
                      setFormError(null);
                    }}
                    className="text-xs text-slate-400 hover:text-slate-600"
                  >
                    Cancel
                  </button>
                </div>

                {formError && (
                  <Alert variant="error" title="Validation Failed">
                    {formError}
                  </Alert>
                )}

                {validationResult?.is_valid && (
                  <Alert variant="success" title="Validation Passed">
                    {validationResult.message}
                  </Alert>
                )}

                <div>
                  <label
                    htmlFor="gemini-api-key-input"
                    className="block text-xs font-semibold text-slate-700 mb-1"
                  >
                    API Key
                  </label>
                  <div className="relative">
                    <input
                      id="gemini-api-key-input"
                      type={showPlaintext ? 'text' : 'password'}
                      autoComplete="off"
                      spellCheck="false"
                      value={apiKeyInput}
                      onChange={(e) => {
                        setApiKeyInput(e.target.value);
                        setFormError(null);
                        setValidationResult(null);
                      }}
                      placeholder="Paste your Gemini API key (starts with AIza...)"
                      className="w-full px-3.5 py-2 pr-10 text-xs font-mono bg-white border border-slate-300 rounded-lg text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPlaintext(!showPlaintext)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 p-1"
                      aria-label={showPlaintext ? 'Hide password' : 'Show password'}
                    >
                      {showPlaintext ? (
                        <EyeOff className="w-4 h-4" />
                      ) : (
                        <Eye className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                  <p className="text-[11px] text-slate-500 mt-1.5 leading-relaxed">
                    Keys are validated with Google Gemini and securely encrypted before storage. Plaintext keys are never persisted or exposed.
                  </p>
                </div>

                <div className="flex items-center justify-between pt-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleValidate}
                    isLoading={isValidating}
                    disabled={isSaving || !apiKeyInput.trim()}
                  >
                    <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
                    <span>Validate Key</span>
                  </Button>

                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setIsFormOpen(false);
                        setApiKeyInput('');
                        setShowPlaintext(false);
                        setValidationResult(null);
                        setFormError(null);
                      }}
                    >
                      Cancel
                    </Button>
                    <Button
                      type="submit"
                      variant="primary"
                      size="sm"
                      isLoading={isSaving}
                      disabled={isValidating || !apiKeyInput.trim()}
                    >
                      Save Key
                    </Button>
                  </div>
                </div>
              </form>
            )}
          </div>

          {/* Fallback & Usage Information Card */}
          <div className="border border-slate-200 rounded-xl p-4 bg-slate-50 space-y-2.5 text-xs text-slate-600">
            <div className="flex items-center gap-2 text-slate-900 font-semibold">
              <Info className="w-4 h-4 text-emerald-600" />
              <span>How Personal Gemini Keys Work</span>
            </div>
            <ul className="list-disc list-inside space-y-1 text-slate-600 leading-relaxed pl-1">
              <li>
                <strong className="text-slate-800">Personal-Key-First:</strong> When enabled, your key is used for AI shopping recommendations and order inquiries.
              </li>
              <li>
                <strong className="text-slate-800">Automatic Failover:</strong> If your key exhausts its quota or experiences a short cooldown, StoreFlow seamlessly falls back to company platform keys.
              </li>
              <li>
                <strong className="text-slate-800">Quotas &amp; Billing:</strong> StoreFlow does not promise unlimited free usage. Requests follow Google AI Studio tier limits.
              </li>
            </ul>

            <div className="pt-2 border-t border-slate-200/60 flex items-center justify-between text-xs">
              <span className="text-slate-500">Need a Google Gemini API key?</span>
              <a
                href="https://aistudio.google.com/app/apikey"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 font-semibold text-emerald-700 hover:text-emerald-800 underline decoration-emerald-300 underline-offset-2"
              >
                <span>Google AI Studio API Keys</span>
                <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-3.5 bg-slate-50 border-t border-slate-100 flex items-center justify-end">
          <Button variant="secondary" size="sm" onClick={handleClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
};
