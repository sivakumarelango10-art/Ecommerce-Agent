import React, { useState, useEffect, useCallback } from 'react';
import {
  Sliders,
  Plus,
  ShieldAlert,
  AlertCircle,
  CheckCircle2,
  Sparkles,
  Search,
  RefreshCw,
  Star,
  X,
  Lock,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type {
  AIModel,
  AIProvider,
  AIModelCreateRequest,
  ModelAccessClass,
} from '../../types/admin';

export const AIModelRegistry: React.FC = () => {
  const { accessToken, currentUser } = useAuth();
  const isSuperAdmin = currentUser?.role === 'super_admin';

  const [models, setModels] = useState<AIModel[]>([]);
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [providerFilter, setProviderFilter] = useState('ALL');
  const [accessClassFilter, setAccessClassFilter] = useState('ALL');
  const [enabledFilter, setEnabledFilter] = useState<string>('ALL'); // 'ALL' | 'ENABLED' | 'DISABLED'
  const [freeOnly, setFreeOnly] = useState(false);

  // Register Model Modal State
  const [isRegisterModalOpen, setIsRegisterModalOpen] = useState(false);
  const [newModelProviderId, setNewModelProviderId] = useState('');
  const [newModelIdentifier, setNewModelIdentifier] = useState('');
  const [newModelDisplayName, setNewModelDisplayName] = useState('');
  const [newModelDescription, setNewModelDescription] = useState('');
  const [newModelAccessClass, setNewModelAccessClass] = useState<ModelAccessClass>('PAY_AS_YOU_GO');
  const [newModelContextWindow, setNewModelContextWindow] = useState<number | ''>(128000);
  const [newModelMaxTokens, setNewModelMaxTokens] = useState<number | ''>(8192);
  const [newModelSupportsText, setNewModelSupportsText] = useState(true);
  const [newModelSupportsVision, setNewModelSupportsVision] = useState(false);
  const [newModelSupportsTools, setNewModelSupportsTools] = useState(false);
  const [newModelSupportsStreaming, setNewModelSupportsStreaming] = useState(true);
  const [isSubmittingModel, setIsSubmittingModel] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const [modelsRes, providersRes] = await Promise.all([
        api.admin.ai.listModels(accessToken),
        api.admin.ai.listProviders(accessToken),
      ]);
      setModels(modelsRes);
      setProviders(providersRes);
      if (providersRes.length > 0 && !newModelProviderId) {
        setNewModelProviderId(providersRes[0].id);
      }
    } catch (err: any) {
      console.error('Failed to load AI models or providers:', err);
      setError(err?.message || 'Failed to load AI model registry.');
    } finally {
      setLoading(false);
    }
  }, [accessToken, newModelProviderId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const providerMap = React.useMemo(() => {
    const map = new Map<string, AIProvider>();
    providers.forEach((p) => map.set(p.id, p));
    return map;
  }, [providers]);

  // Handle Toggle Enabled
  const handleToggleEnabled = async (model: AIModel) => {
    if (!accessToken || !isSuperAdmin) return;
    try {
      setError(null);
      const updated = await api.admin.ai.updateModel(
        model.id,
        { is_enabled: !model.is_enabled },
        accessToken
      );
      setModels((prev) => prev.map((m) => (m.id === model.id ? updated : m)));
      setSuccessMessage(`Model '${model.display_name}' ${updated.is_enabled ? 'enabled' : 'disabled'}.`);
    } catch (err: any) {
      console.error('Failed to toggle model state:', err);
      setError(err?.message || 'Failed to update model.');
    }
  };

  // Handle Set as Default
  const handleSetDefault = async (model: AIModel) => {
    if (!accessToken || !isSuperAdmin || model.is_default) return;
    try {
      setError(null);
      const updated = await api.admin.ai.updateModel(
        model.id,
        { is_default: true },
        accessToken
      );
      // Refresh list to update former default
      await fetchData();
      setSuccessMessage(`Model '${updated.display_name}' set as default for its provider.`);
    } catch (err: any) {
      console.error('Failed to set default model:', err);
      setError(err?.message || 'Failed to update default model.');
    }
  };

  // Handle Register Model Submit
  const handleRegisterModelSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken || !isSuperAdmin) return;

    if (!newModelProviderId) {
      setRegisterError('Please select an AI provider.');
      return;
    }
    if (!newModelIdentifier.trim()) {
      setRegisterError('Model identifier is mandatory.');
      return;
    }
    if (!newModelDisplayName.trim()) {
      setRegisterError('Display name is mandatory.');
      return;
    }

    try {
      setIsSubmittingModel(true);
      setRegisterError(null);

      const payload: AIModelCreateRequest = {
        provider_id: newModelProviderId,
        model_identifier: newModelIdentifier.trim(),
        display_name: newModelDisplayName.trim(),
        description: newModelDescription.trim() || null,
        access_class: newModelAccessClass,
        context_window: newModelContextWindow ? Number(newModelContextWindow) : null,
        max_output_tokens: newModelMaxTokens ? Number(newModelMaxTokens) : null,
        supports_text: newModelSupportsText,
        supports_vision: newModelSupportsVision,
        supports_tools: newModelSupportsTools,
        supports_streaming: newModelSupportsStreaming,
        is_enabled: true,
        is_default: false,
      };

      await api.admin.ai.createModel(payload, accessToken);
      setIsRegisterModalOpen(false);
      setNewModelIdentifier('');
      setNewModelDisplayName('');
      setNewModelDescription('');
      setSuccessMessage('AI Model registered successfully.');
      await fetchData();
    } catch (err: any) {
      console.error('Failed to register model:', err);
      setRegisterError(err?.message || 'Failed to register model.');
    } finally {
      setIsSubmittingModel(false);
    }
  };

  // Filtered models
  const filteredModels = models.filter((m) => {
    const matchesSearch =
      m.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.model_identifier.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (m.description || '').toLowerCase().includes(searchQuery.toLowerCase());

    const matchesProvider = providerFilter === 'ALL' || m.provider_id === providerFilter;

    const matchesAccessClass =
      freeOnly ? m.access_class === 'FREE_TIER' : accessClassFilter === 'ALL' || m.access_class === accessClassFilter;

    const matchesEnabled =
      enabledFilter === 'ALL' ||
      (enabledFilter === 'ENABLED' && m.is_enabled) ||
      (enabledFilter === 'DISABLED' && !m.is_enabled);

    return matchesSearch && matchesProvider && matchesAccessClass && matchesEnabled;
  });

  const renderAccessBadge = (accessClass: ModelAccessClass) => {
    switch (accessClass) {
      case 'FREE_TIER':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
            <Sparkles className="w-3 h-3 mr-1 text-emerald-600" />
            FREE TIER
          </span>
        );
      case 'PAY_AS_YOU_GO':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-200">
            PAY AS YOU GO
          </span>
        );
      case 'PROMOTIONAL_CREDITS':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
            PROMOTIONAL CREDITS
          </span>
        );
      case 'SELF_HOSTED':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800 border border-purple-200">
            SELF HOSTED
          </span>
        );
      case 'UNKNOWN':
      default:
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700 border border-slate-300">
            UNKNOWN
          </span>
        );
    }
  };

  return (
    <div className="space-y-6" data-testid="ai-model-registry">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-bold text-slate-900 tracking-tight">AI Model Registry</h2>
            <span className="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-slate-200 text-slate-700">
              {models.length} Model{models.length === 1 ? '' : 's'}
            </span>
          </div>
          <p className="text-sm text-slate-500 mt-1">
            Registered LLMs, multi-modal engines, capability matrices, context limits, and pricing metadata.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={fetchData}
            disabled={loading}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 shadow-xs transition disabled:opacity-50"
            title="Refresh Models"
          >
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>

          {isSuperAdmin ? (
            <button
              type="button"
              onClick={() => {
                setRegisterError(null);
                setIsRegisterModalOpen(true);
              }}
              className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-slate-900 hover:bg-slate-800 rounded-lg shadow-xs transition"
            >
              <Plus className="w-4 h-4 mr-1.5" />
              Register Model
            </button>
          ) : (
            <span className="inline-flex items-center text-xs font-medium text-slate-500 bg-slate-100 px-3 py-2 rounded-lg border border-slate-200">
              <Lock className="w-3.5 h-3.5 mr-1.5 text-slate-400" />
              Super Admin required to register models
            </span>
          )}
        </div>
      </div>

      {/* Free Tier & Data Privacy Disclosures */}
      <div className="p-4 bg-emerald-50/70 border border-emerald-200 rounded-xl space-y-2 text-xs text-emerald-950">
        <div className="flex items-center gap-2 font-semibold text-emerald-900 text-sm">
          <ShieldAlert className="w-4 h-4 text-emerald-700" />
          Free Tier Availability & Data Privacy Guidelines
        </div>
        <p className="leading-relaxed">
          <strong className="font-semibold">Free-Tier Policy:</strong> Models offering a free tier (such as Gemini Flash) allow zero-cost prototyping.
          However, free-tier prompts and outputs may be processed by provider human reviewers and utilized for product/model improvement.
          <strong className="text-rose-700 font-semibold block mt-1">
            Sensitive customer orders, payments, credentials, or PII must not be transmitted to Free Tier endpoints.
          </strong>
        </p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-emerald-800 pt-1 border-t border-emerald-200/60">
          <span>• Free availability does not imply unlimited access (project RPM/TPM/RPD limits apply).</span>
          <span>• Promotional credits are temporary billing offsets and not permanent free access.</span>
          <span>• Unverified pricing values are recorded as NULL.</span>
        </div>
      </div>

      {/* Alerts */}
      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl flex items-center gap-3 text-rose-800 text-sm">
          <AlertCircle className="w-5 h-5 text-rose-500 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {successMessage && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl flex items-center gap-3 text-emerald-800 text-sm">
          <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
          <span>{successMessage}</span>
        </div>
      )}

      {/* Filter and Search Bar with Free-First Toggle */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs space-y-3">
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
          {/* Search Box */}
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search models by identifier, display name or description..."
              className="w-full pl-9 pr-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:bg-white transition"
            />
          </div>

          {/* Prominent Free Models Filter */}
          <button
            type="button"
            onClick={() => setFreeOnly(!freeOnly)}
            data-testid="free-models-filter-button"
            className={`inline-flex items-center px-3.5 py-1.5 rounded-lg text-xs font-semibold transition shadow-xs ${
              freeOnly
                ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                : 'bg-emerald-50 text-emerald-800 border border-emerald-300 hover:bg-emerald-100'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 mr-1.5" />
            {freeOnly ? 'Showing Free Tier Only' : 'Filter Free Models'}
          </button>
        </div>

        {/* Dropdown Filters */}
        <div className="flex flex-wrap items-center gap-2.5 pt-1 text-xs">
          {/* Provider Filter */}
          <div className="flex items-center gap-1.5">
            <span className="text-slate-400 font-medium">Provider:</span>
            <select
              value={providerFilter}
              onChange={(e) => setProviderFilter(e.target.value)}
              className="px-2.5 py-1 bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
              aria-label="Filter by Provider"
            >
              <option value="ALL">All Providers</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
          </div>

          {/* Access Class Filter */}
          {!freeOnly && (
            <div className="flex items-center gap-1.5">
              <span className="text-slate-400 font-medium">Access:</span>
              <select
                value={accessClassFilter}
                onChange={(e) => setAccessClassFilter(e.target.value)}
                className="px-2.5 py-1 bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                aria-label="Filter by Access Class"
              >
                <option value="ALL">All Access Classes</option>
                <option value="FREE_TIER">FREE_TIER</option>
                <option value="PAY_AS_YOU_GO">PAY_AS_YOU_GO</option>
                <option value="PROMOTIONAL_CREDITS">PROMOTIONAL_CREDITS</option>
                <option value="SELF_HOSTED">SELF_HOSTED</option>
                <option value="UNKNOWN">UNKNOWN</option>
              </select>
            </div>
          )}

          {/* Enabled Status */}
          <div className="flex items-center gap-1.5">
            <span className="text-slate-400 font-medium">State:</span>
            <select
              value={enabledFilter}
              onChange={(e) => setEnabledFilter(e.target.value)}
              className="px-2.5 py-1 bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
              aria-label="Filter by Enabled State"
            >
              <option value="ALL">All</option>
              <option value="ENABLED">Enabled Only</option>
              <option value="DISABLED">Disabled Only</option>
            </select>
          </div>
        </div>
      </div>

      {/* Model Cards / Grid */}
      {loading && models.length === 0 ? (
        <div className="p-8 text-center text-sm text-slate-500">Loading AI models...</div>
      ) : filteredModels.length === 0 ? (
        <div className="p-12 text-center bg-white rounded-xl border border-slate-200">
          <Sliders className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <h3 className="text-sm font-semibold text-slate-900">No Models Found</h3>
          <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
            No AI models matched your search or access class criteria.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filteredModels.map((model) => {
            const provider = providerMap.get(model.provider_id);
            const isFree = model.access_class === 'FREE_TIER';

            return (
              <div
                key={model.id}
                className="bg-white rounded-xl border border-slate-200 hover:border-slate-300 p-5 shadow-xs transition flex flex-col justify-between"
                data-testid={`model-card-${model.model_identifier}`}
              >
                <div>
                  {/* Card Header: Title, Identifier, Access Class */}
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-base font-semibold text-slate-900 tracking-tight">
                          {model.display_name}
                        </h3>
                        {model.is_default && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-amber-50 text-amber-800 border border-amber-200">
                            <Star className="w-3 h-3 mr-0.5 fill-amber-500 text-amber-500" />
                            DEFAULT
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="font-mono text-xs text-slate-500">{model.model_identifier}</span>
                        <span className="text-slate-300">•</span>
                        <span className="text-xs text-slate-600 font-medium">{provider?.display_name || 'Provider'}</span>
                      </div>
                    </div>

                    {renderAccessBadge(model.access_class)}
                  </div>

                  {/* Description */}
                  {model.description && (
                    <p className="text-xs text-slate-600 mt-2 mb-3 leading-relaxed line-clamp-2">
                      {model.description}
                    </p>
                  )}

                  {/* Free Tier Warning Badge */}
                  {isFree && (
                    <div className="p-2.5 bg-emerald-50/60 rounded-lg border border-emerald-200/80 text-[11px] text-emerald-950 mb-3 space-y-1">
                      <div className="flex items-center gap-1 font-semibold text-emerald-900">
                        <ShieldAlert className="w-3.5 h-3.5 text-emerald-700" />
                        Free Tier Quotas & Data Use Terms Apply
                      </div>
                      <p className="text-emerald-800 text-[10px] leading-tight">
                        Non-confidential workloads only. Provider model improvement policy active. Do not submit customer PII.
                      </p>
                    </div>
                  )}

                  {/* Context and Pricing Specs */}
                  <div className="grid grid-cols-2 gap-3 py-2.5 border-t border-slate-100 text-xs text-slate-600">
                    <div>
                      <span className="text-slate-400 block text-[11px]">Context Window</span>
                      <span className="font-semibold text-slate-800">
                        {model.context_window ? `${(model.context_window / 1000).toLocaleString()}k tokens` : '—'}
                      </span>
                    </div>

                    <div>
                      <span className="text-slate-400 block text-[11px]">Pricing (In / Out per 1M)</span>
                      <span className="font-semibold text-slate-800">
                        {model.pricing_input_per_million ? (
                          `$${model.pricing_input_per_million} / $${model.pricing_output_per_million}`
                        ) : (
                          <span className="text-slate-400 italic">Unverified (NULL)</span>
                        )}
                      </span>
                    </div>
                  </div>

                  {/* Capability Badges */}
                  <div className="flex flex-wrap gap-1.5 pt-2 pb-3">
                    {model.supports_text && (
                      <span className="px-2 py-0.5 bg-slate-100 text-slate-700 rounded text-[11px]">Text</span>
                    )}
                    {model.supports_vision && (
                      <span className="px-2 py-0.5 bg-purple-50 text-purple-700 rounded text-[11px] border border-purple-100">
                        Vision
                      </span>
                    )}
                    {model.supports_tools && (
                      <span className="px-2 py-0.5 bg-amber-50 text-amber-700 rounded text-[11px] border border-amber-100">
                        Function Calling
                      </span>
                    )}
                    {model.supports_streaming && (
                      <span className="px-2 py-0.5 bg-sky-50 text-sky-700 rounded text-[11px] border border-sky-100">
                        Streaming
                      </span>
                    )}
                    {model.supports_json_mode && (
                      <span className="px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[11px] border border-indigo-100">
                        JSON Mode
                      </span>
                    )}
                  </div>
                </div>

                {/* Footer Controls (Super Admin only for mutation) */}
                <div className="pt-3 border-t border-slate-100 flex items-center justify-between gap-2 mt-auto">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${model.is_enabled ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                    <span className="text-xs font-medium text-slate-700">
                      {model.is_enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>

                  {isSuperAdmin ? (
                    <div className="flex items-center gap-2">
                      {!model.is_default && model.is_enabled && (
                        <button
                          type="button"
                          onClick={() => handleSetDefault(model)}
                          className="px-2.5 py-1 text-xs font-medium text-amber-800 bg-amber-50 hover:bg-amber-100 rounded-md border border-amber-200 transition"
                        >
                          Make Default
                        </button>
                      )}

                      <button
                        type="button"
                        onClick={() => handleToggleEnabled(model)}
                        className={`px-2.5 py-1 text-xs font-medium rounded-md transition ${
                          model.is_enabled
                            ? 'text-rose-700 bg-rose-50 hover:bg-rose-100 border border-rose-200'
                            : 'text-emerald-700 bg-emerald-50 hover:bg-emerald-100 border border-emerald-200'
                        }`}
                      >
                        {model.is_enabled ? 'Disable' : 'Enable'}
                      </button>
                    </div>
                  ) : (
                    <span className="text-slate-400 text-xs">Read-only</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Register Model Modal (Super Admin only) */}
      {isRegisterModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl border border-slate-200 shadow-xl max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <h3 className="text-base font-bold text-slate-900">Register AI Model</h3>
              <button
                type="button"
                onClick={() => setIsRegisterModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {registerError && (
              <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-rose-800 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0 text-rose-500" />
                <span>{registerError}</span>
              </div>
            )}

            <form onSubmit={handleRegisterModelSubmit} className="space-y-3.5 text-xs">
              {/* Provider */}
              <div>
                <label className="block font-semibold text-slate-700 mb-1">
                  Provider <span className="text-rose-500">*</span>
                </label>
                <select
                  value={newModelProviderId}
                  onChange={(e) => setNewModelProviderId(e.target.value)}
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name} ({p.code})
                    </option>
                  ))}
                </select>
              </div>

              {/* Identifier */}
              <div>
                <label className="block font-semibold text-slate-700 mb-1">
                  Model Identifier <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  value={newModelIdentifier}
                  onChange={(e) => setNewModelIdentifier(e.target.value)}
                  placeholder="e.g. llama-3.3-70b-versatile, claude-3-5-sonnet"
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 font-mono"
                />
              </div>

              {/* Display Name */}
              <div>
                <label className="block font-semibold text-slate-700 mb-1">
                  Display Name <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  value={newModelDisplayName}
                  onChange={(e) => setNewModelDisplayName(e.target.value)}
                  placeholder="e.g. Llama 3.3 70B Versatile"
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>

              {/* Access Class */}
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Access Class</label>
                <select
                  value={newModelAccessClass}
                  onChange={(e) => setNewModelAccessClass(e.target.value as ModelAccessClass)}
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                  <option value="PAY_AS_YOU_GO">PAY_AS_YOU_GO</option>
                  <option value="FREE_TIER">FREE_TIER</option>
                  <option value="PROMOTIONAL_CREDITS">PROMOTIONAL_CREDITS</option>
                  <option value="SELF_HOSTED">SELF_HOSTED</option>
                  <option value="UNKNOWN">UNKNOWN</option>
                </select>
              </div>

              {/* Limits */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Context Window</label>
                  <input
                    type="number"
                    value={newModelContextWindow}
                    onChange={(e) => setNewModelContextWindow(e.target.value ? Number(e.target.value) : '')}
                    placeholder="128000"
                    className="w-full px-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Max Output Tokens</label>
                  <input
                    type="number"
                    value={newModelMaxTokens}
                    onChange={(e) => setNewModelMaxTokens(e.target.value ? Number(e.target.value) : '')}
                    placeholder="8192"
                    className="w-full px-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none"
                  />
                </div>
              </div>

              {/* Capabilities Checkboxes */}
              <div className="pt-1">
                <span className="block font-semibold text-slate-700 mb-1.5">Capabilities:</span>
                <div className="grid grid-cols-2 gap-2 text-slate-700">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newModelSupportsText}
                      onChange={(e) => setNewModelSupportsText(e.target.checked)}
                      className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                    />
                    Supports Text
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newModelSupportsVision}
                      onChange={(e) => setNewModelSupportsVision(e.target.checked)}
                      className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                    />
                    Supports Vision
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newModelSupportsTools}
                      onChange={(e) => setNewModelSupportsTools(e.target.checked)}
                      className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                    />
                    Supports Function Calling
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newModelSupportsStreaming}
                      onChange={(e) => setNewModelSupportsStreaming(e.target.checked)}
                      className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                    />
                    Supports Streaming
                  </label>
                </div>
              </div>

              {/* Description */}
              <div>
                <label className="block font-semibold text-slate-700 mb-1">Description (Optional)</label>
                <textarea
                  value={newModelDescription}
                  onChange={(e) => setNewModelDescription(e.target.value)}
                  rows={2}
                  placeholder="Brief model description..."
                  className="w-full px-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none"
                />
              </div>

              {/* Actions */}
              <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setIsRegisterModalOpen(false)}
                  className="px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmittingModel}
                  className="px-4 py-2 text-sm font-medium text-white bg-slate-900 hover:bg-slate-800 rounded-lg disabled:opacity-50"
                >
                  {isSubmittingModel ? 'Registering...' : 'Register Model'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
