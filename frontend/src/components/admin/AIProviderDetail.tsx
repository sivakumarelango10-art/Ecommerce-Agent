import React, { useState, useEffect, useCallback } from 'react';
import {
  ArrowLeft,
  Server,
  Key,
  Sliders,
  ExternalLink,
  CheckCircle2,
  Clock,
  AlertCircle,
  RefreshCw,
  Info,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type { AIProvider, AIModel, PlatformAIKey } from '../../types/admin';
import { getSafeExternalUrl } from '../../utils/url';

export interface AIProviderDetailProps {
  providerId: string;
  onBack: () => void;
  onNavigateToKeys?: () => void;
}

export const AIProviderDetail: React.FC<AIProviderDetailProps> = ({
  providerId,
  onBack,
  onNavigateToKeys,
}) => {
  const { accessToken } = useAuth();

  const [provider, setProvider] = useState<AIProvider | null>(null);
  const [models, setModels] = useState<AIModel[]>([]);
  const [keys, setKeys] = useState<PlatformAIKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchProviderData = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const [providerRes, modelsRes, keysRes] = await Promise.all([
        api.admin.ai.getProvider(providerId, accessToken),
        api.admin.ai.listModels(accessToken, { provider_id: providerId }),
        api.admin.ai.listKeys(accessToken, { provider_id: providerId }),
      ]);
      setProvider(providerRes);
      setModels(modelsRes);
      setKeys(keysRes);
    } catch (err: any) {
      console.error('Failed to load provider details:', err);
      setError(err?.message || 'Failed to load AI provider details.');
    } finally {
      setLoading(false);
    }
  }, [providerId, accessToken]);

  useEffect(() => {
    fetchProviderData();
  }, [fetchProviderData]);

  if (loading && !provider) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-10 bg-slate-200 rounded w-1/4" />
        <div className="h-40 bg-white rounded-xl border border-slate-200 p-6" />
        <div className="h-60 bg-white rounded-xl border border-slate-200 p-6" />
      </div>
    );
  }

  if (error || !provider) {
    return (
      <div className="space-y-4">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center text-sm font-medium text-slate-600 hover:text-slate-900"
        >
          <ArrowLeft className="w-4 h-4 mr-1.5" /> Back to Catalogue
        </button>
        <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800 text-sm">
          <AlertCircle className="w-5 h-5 text-rose-500 mb-2" />
          {error || 'Provider not found.'}
        </div>
      </div>
    );
  }

  const isAvailable = provider.connection_status === 'AVAILABLE';

  return (
    <div className="space-y-6" data-testid="ai-provider-detail">
      {/* Top Navigation */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center text-sm font-medium text-slate-600 hover:text-slate-900 transition"
        >
          <ArrowLeft className="w-4 h-4 mr-1.5" /> Back to Provider Catalogue
        </button>

        <button
          type="button"
          onClick={fetchProviderData}
          disabled={loading}
          className="inline-flex items-center px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition shadow-xs"
        >
          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Provider Overview Card */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-xs">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-slate-100">
          <div className="flex items-start gap-4">
            <div className="p-3 bg-slate-100 rounded-xl text-slate-700 shrink-0">
              <Server className="w-8 h-8" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-xl font-bold text-slate-900 tracking-tight">{provider.display_name}</h2>
                <span className="px-2.5 py-0.5 text-xs font-mono font-medium rounded bg-slate-100 text-slate-700 border border-slate-200">
                  {provider.code}
                </span>
              </div>
              <p className="text-sm text-slate-500 mt-1">
                Adapter: <span className="font-medium text-slate-700">{provider.adapter_type}</span> | Priority: {provider.priority}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold ${
              isAvailable
                ? 'bg-emerald-100 text-emerald-800 border border-emerald-200'
                : 'bg-slate-100 text-slate-700 border border-slate-200'
            }`}>
              {isAvailable ? <CheckCircle2 className="w-3.5 h-3.5 mr-1 text-emerald-600" /> : <Clock className="w-3.5 h-3.5 mr-1 text-slate-500" />}
              {provider.connection_status}
            </span>

            {(() => {
              const safeDocUrl = getSafeExternalUrl(provider.documentation_url);
              return safeDocUrl ? (
                <a
                  href={safeDocUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center px-3 py-1 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-full hover:bg-slate-50 shadow-xs transition"
                >
                  <ExternalLink className="w-3 h-3 mr-1" />
                  Documentation
                </a>
              ) : null;
            })()}
          </div>
        </div>

        {/* Configuration Details Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-4 text-xs">
          <div>
            <span className="block text-slate-400 font-medium">Base URL</span>
            <span className="font-mono text-slate-700 truncate block mt-0.5">
              {provider.base_url || 'Default SDK Endpoint'}
            </span>
          </div>
          <div>
            <span className="block text-slate-400 font-medium">Key Vault Credentials</span>
            <span className="font-semibold text-slate-700 mt-0.5 block">
              {keys.length} Key{keys.length === 1 ? '' : 's'} Configured
            </span>
          </div>
          <div>
            <span className="block text-slate-400 font-medium">Registered Models</span>
            <span className="font-semibold text-slate-700 mt-0.5 block">
              {models.length} Model{models.length === 1 ? '' : 's'} Available
            </span>
          </div>
        </div>

        {/* Architecture Notice */}
        <div className="mt-4 p-3 bg-slate-50 rounded-lg text-xs text-slate-600 border border-slate-200 flex items-start gap-2">
          <Info className="w-4 h-4 text-slate-400 mt-0.5 shrink-0" />
          <div>
            {isAvailable ? (
              <span>This provider is operational and connected to StoreFlow's live runtime agent pipeline.</span>
            ) : (
              <span>This is a planned catalogue entry. Stored credentials and models are maintained securely in readiness for future adapter activation.</span>
            )}
          </div>
        </div>
      </div>

      {/* Models Section */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-xs space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sliders className="w-5 h-5 text-slate-700" />
            <h3 className="text-base font-semibold text-slate-900">Registered Models ({models.length})</h3>
          </div>
        </div>

        {models.length === 0 ? (
          <div className="text-center py-8 text-xs text-slate-500 bg-slate-50 rounded-lg border border-dashed border-slate-200">
            No models registered for this provider yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase tracking-wider">
                <tr>
                  <th className="py-2.5 px-3 font-semibold">Model</th>
                  <th className="py-2.5 px-3 font-semibold">Access Class</th>
                  <th className="py-2.5 px-3 font-semibold">Context / Max Out</th>
                  <th className="py-2.5 px-3 font-semibold">Capabilities</th>
                  <th className="py-2.5 px-3 font-semibold">Pricing (1M)</th>
                  <th className="py-2.5 px-3 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {models.map((m) => (
                  <tr key={m.id} className="hover:bg-slate-50/50">
                    <td className="py-3 px-3">
                      <div className="font-medium text-slate-900">{m.display_name}</div>
                      <div className="font-mono text-slate-400 text-[11px]">{m.model_identifier}</div>
                      {m.description?.includes('[ENTERPRISE_RESTRICTED]') && (
                        <div className="text-[10px] text-amber-800 font-medium mt-0.5">
                          Enterprise committed-spend entitlement required (Unavailable in StoreFlow)
                        </div>
                      )}
                    </td>
                    <td className="py-3 px-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-[11px] font-semibold ${
                        m.access_class === 'FREE_TIER'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : m.access_class === 'PAY_AS_YOU_GO'
                          ? 'bg-blue-50 text-blue-700 border border-blue-200'
                          : m.access_class === 'PROMOTIONAL_CREDITS'
                          ? 'bg-amber-50 text-amber-700 border border-amber-200'
                          : m.access_class === 'SELF_HOSTED'
                          ? 'bg-purple-50 text-purple-700 border border-purple-200'
                          : 'bg-slate-100 text-slate-700 border border-slate-300'
                      }`}>
                        {m.access_class}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-slate-600">
                      <div>In: {m.context_window ? `${(m.context_window / 1000).toFixed(0)}k` : '—'}</div>
                      <div>Out: {m.max_output_tokens ? `${(m.max_output_tokens / 1000).toFixed(0)}k` : '—'}</div>
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex flex-wrap gap-1">
                        {m.supports_text && <span className="bg-slate-100 text-slate-600 px-1.5 py-0.2 rounded text-[10px]">Text</span>}
                        {m.supports_vision && <span className="bg-purple-100 text-purple-700 px-1.5 py-0.2 rounded text-[10px]">Vision</span>}
                        {m.supports_tools && <span className="bg-amber-100 text-amber-700 px-1.5 py-0.2 rounded text-[10px]">Tools</span>}
                        {m.supports_streaming && <span className="bg-sky-100 text-sky-700 px-1.5 py-0.2 rounded text-[10px]">Stream</span>}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-slate-600">
                      {m.pricing_input_per_million ? (
                        <span>${m.pricing_input_per_million} / ${m.pricing_output_per_million}</span>
                      ) : (
                        <span className="text-slate-400 italic">Unverified (NULL)</span>
                      )}
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex items-center gap-1.5">
                        <span className={`w-2 h-2 rounded-full ${m.is_enabled ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                        <span className="text-slate-600">{m.is_enabled ? 'Enabled' : 'Disabled'}</span>
                        {m.is_default && (
                          <span className="ml-1 bg-amber-50 text-amber-700 text-[10px] font-semibold px-1.5 py-0.5 rounded border border-amber-200">
                            DEFAULT
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Keys Section */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-xs space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Key className="w-5 h-5 text-slate-700" />
            <h3 className="text-base font-semibold text-slate-900">Configured Company Keys ({keys.length})</h3>
          </div>
          {onNavigateToKeys && (
            <button
              type="button"
              onClick={onNavigateToKeys}
              className="text-xs font-medium text-emerald-600 hover:text-emerald-700"
            >
              Manage in Key Vault →
            </button>
          )}
        </div>

        {keys.length === 0 ? (
          <div className="text-center py-8 text-xs text-slate-500 bg-slate-50 rounded-lg border border-dashed border-slate-200">
            No company API keys stored for this provider yet.
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            {keys.map((k) => (
              <div key={k.id} className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg text-xs space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-800">{k.key_label}</span>
                  <span
                    className={`px-1.5 py-0.5 rounded font-semibold text-[10px] ${
                      k.status === 'ACTIVE'
                        ? 'bg-emerald-100 text-emerald-800'
                        : k.status === 'UNVERIFIED'
                        ? 'bg-slate-200 text-slate-700'
                        : 'bg-amber-100 text-amber-800'
                    }`}
                  >
                    {k.status}
                  </span>
                </div>
                <div className="text-slate-500 font-mono">Hint: {k.key_hint}</div>
                <div className="text-slate-500">Quota: {k.quota_group}</div>
                <div className="text-[11px] text-slate-400">Created: {new Date(k.created_at).toLocaleDateString()}</div>
                {k.status === 'UNVERIFIED' && (
                  <div className="text-[10px] text-slate-500 italic pt-1 border-t border-slate-200/60">
                    Key is unverified & not yet active for live traffic.
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
