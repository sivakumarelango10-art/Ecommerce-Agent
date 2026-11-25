import React, { useState, useEffect, useCallback } from 'react';
import {
  Server,
  ExternalLink,
  ShieldCheck,
  CheckCircle2,
  Clock,
  AlertCircle,
  RefreshCw,
  Search,
  Filter,
  ArrowRight,
  Database,
  Info,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type { AIProvider, ProviderConnectionStatus } from '../../types/admin';
import { AIProviderDetail } from './AIProviderDetail';

export const AIProviderCatalogue: React.FC = () => {
  const { accessToken, currentUser } = useAuth();
  const isSuperAdmin = currentUser?.role === 'super_admin';

  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  // Detail view state
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const data = await api.admin.ai.listProviders(accessToken);
      setProviders(data);
    } catch (err: any) {
      console.error('Failed to load AI providers:', err);
      setError(err?.message || 'Failed to load AI provider catalogue.');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const handleSeedCatalogue = async () => {
    if (!accessToken || !isSuperAdmin) return;
    try {
      setSeeding(true);
      setError(null);
      setSuccessMessage(null);
      const stats = await api.admin.ai.seedCatalogue(accessToken);
      setSuccessMessage(
        `Successfully seeded AI catalogue: ${stats.providers_created} created, ${stats.providers_updated} updated, ${stats.models_created} models added.`
      );
      await fetchProviders();
    } catch (err: any) {
      console.error('Failed to seed AI catalogue:', err);
      setError(err?.message || 'Failed to seed AI provider catalogue.');
    } finally {
      setSeeding(false);
    }
  };

  // Helper for provider connection status badge
  const renderStatusBadge = (status: ProviderConnectionStatus, _adapterType?: string) => {
    switch (status) {
      case 'AVAILABLE':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
            <CheckCircle2 className="w-3.5 h-3.5 mr-1 text-emerald-600" />
            AVAILABLE
          </span>
        );
      case 'CONFIGURED':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-sky-100 text-sky-800 border border-sky-200">
            <CheckCircle2 className="w-3.5 h-3.5 mr-1 text-sky-600" />
            CONFIGURED
          </span>
        );
      case 'DEGRADED':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
            <Clock className="w-3.5 h-3.5 mr-1 text-amber-600" />
            DEGRADED
          </span>
        );
      case 'DISABLED':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-100 text-rose-800 border border-rose-200">
            <AlertCircle className="w-3.5 h-3.5 mr-1 text-rose-600" />
            DISABLED
          </span>
        );
      case 'PLANNED':
      default:
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200">
            <Clock className="w-3.5 h-3.5 mr-1 text-slate-500" />
            PLANNED
          </span>
        );
    }
  };

  // Helper for adapter indicator
  const renderAdapterBadge = (adapterType: string) => {
    if (adapterType === 'google_gemini') {
      return (
        <span className="inline-flex items-center text-xs font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
          <ShieldCheck className="w-3 h-3 mr-1 text-emerald-600" />
          Native Gemini Adapter
        </span>
      );
    }
    return (
      <span className="inline-flex items-center text-xs text-slate-500 bg-slate-100 px-2 py-0.5 rounded border border-slate-200">
        <Clock className="w-3 h-3 mr-1 text-slate-400" />
        Adapter: {adapterType} (Planned)
      </span>
    );
  };

  // Filtered providers
  const filteredProviders = providers.filter((p) => {
    const matchesSearch =
      p.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.code.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.adapter_type.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'ALL' || p.connection_status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  if (selectedProviderId) {
    return (
      <AIProviderDetail
        providerId={selectedProviderId}
        onBack={() => {
          setSelectedProviderId(null);
          fetchProviders();
        }}
      />
    );
  }

  return (
    <div className="space-y-6" data-testid="ai-provider-catalogue">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-bold text-slate-900 tracking-tight">AI Provider Catalogue</h2>
            <span className="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-slate-200 text-slate-700">
              {providers.length} Registered
            </span>
          </div>
          <p className="text-sm text-slate-500 mt-1">
            Official multi-provider AI model registry and connection management.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={fetchProviders}
            disabled={loading}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 shadow-xs transition disabled:opacity-50"
            title="Refresh Provider List"
          >
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>

          {isSuperAdmin && (
            <button
              type="button"
              onClick={handleSeedCatalogue}
              disabled={seeding || loading}
              className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-slate-900 hover:bg-slate-800 rounded-lg shadow-xs transition disabled:opacity-50"
            >
              <Database className="w-4 h-4 mr-1.5" />
              {seeding ? 'Seeding...' : 'Seed 20 Providers'}
            </button>
          )}
        </div>
      </div>

      {/* Safety Notice: Distinguishing catalogue entry from active runtime */}
      <div className="p-4 bg-amber-50/80 border border-amber-200 rounded-xl flex items-start gap-3">
        <Info className="w-5 h-5 text-amber-600 mt-0.5 shrink-0" />
        <div className="text-xs text-amber-900 leading-relaxed">
          <strong className="font-semibold block mb-0.5">Runtime Architecture Notice:</strong>
          Providers marked as <code className="bg-amber-100 px-1 py-0.5 rounded font-mono text-amber-800">PLANNED</code>{' '}
          represent catalogue specifications and are not active for live runtime traffic. The live StoreFlow runtime currently
          executes through Google Gemini (<code className="bg-amber-100 px-1 py-0.5 rounded font-mono text-amber-800">gemini-3.8-flash</code>).
          Storing company credentials or registering providers does not route customer traffic until validation adapters are activated.
        </div>
      </div>

      {/* Notifications */}
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

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-white p-3.5 rounded-xl border border-slate-200 shadow-xs">
        <div className="relative flex-1">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search provider by name, code or adapter..."
            className="w-full pl-9 pr-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:bg-white transition"
          />
        </div>

        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-400 ml-1 hidden sm:inline" />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
            aria-label="Filter by Status"
          >
            <option value="ALL">All Statuses</option>
            <option value="AVAILABLE">AVAILABLE</option>
            <option value="CONFIGURED">CONFIGURED</option>
            <option value="PLANNED">PLANNED</option>
            <option value="DEGRADED">DEGRADED</option>
            <option value="DISABLED">DISABLED</option>
          </select>
        </div>
      </div>

      {/* Provider Grid */}
      {loading && providers.length === 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map((idx) => (
            <div key={idx} className="bg-white p-5 rounded-xl border border-slate-200 shadow-xs animate-pulse space-y-3">
              <div className="h-5 bg-slate-200 rounded w-1/2" />
              <div className="h-4 bg-slate-100 rounded w-3/4" />
              <div className="h-8 bg-slate-100 rounded" />
            </div>
          ))}
        </div>
      ) : filteredProviders.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-xl border border-slate-200 p-8">
          <Server className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <h3 className="text-sm font-semibold text-slate-900">No Providers Found</h3>
          <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
            {providers.length === 0
              ? 'The AI Provider catalogue is currently empty. Super admins can seed the initial 20 providers.'
              : 'No providers matched your search or status filter criteria.'}
          </p>
          {isSuperAdmin && providers.length === 0 && (
            <button
              type="button"
              onClick={handleSeedCatalogue}
              disabled={seeding}
              className="mt-4 inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-slate-900 hover:bg-slate-800 rounded-lg shadow-xs"
            >
              <Database className="w-4 h-4 mr-1.5" />
              Seed Initial 20 Providers
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredProviders.map((provider) => {
            const isAvailable = provider.connection_status === 'AVAILABLE';
            return (
              <div
                key={provider.id}
                className="bg-white rounded-xl border border-slate-200 hover:border-slate-300 p-5 shadow-xs transition flex flex-col justify-between"
                data-testid={`provider-card-${provider.code}`}
              >
                <div>
                  {/* Card Header: Title + Status */}
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div>
                      <div className="flex items-center gap-2">
                        <Server className="w-5 h-5 text-slate-700" />
                        <h3 className="text-base font-semibold text-slate-900 tracking-tight">
                          {provider.display_name}
                        </h3>
                      </div>
                      <span className="text-xs font-mono text-slate-400 mt-0.5 block">
                        {provider.code}
                      </span>
                    </div>
                    {renderStatusBadge(provider.connection_status, provider.adapter_type)}
                  </div>

                  {/* Adapter Badge */}
                  <div className="mt-1 mb-3">{renderAdapterBadge(provider.adapter_type)}</div>

                  {/* Metrics Badges */}
                  <div className="grid grid-cols-3 gap-2 py-2.5 my-2 border-y border-slate-100 text-center">
                    <div>
                      <span className="block text-xs text-slate-400">Priority</span>
                      <span className="text-xs font-semibold text-slate-700">{provider.priority}</span>
                    </div>
                    <div>
                      <span className="block text-xs text-slate-400">Keys</span>
                      <span className="text-xs font-semibold text-slate-700">{provider.keys_count ?? 0}</span>
                    </div>
                    <div>
                      <span className="block text-xs text-slate-400">Models</span>
                      <span className="text-xs font-semibold text-slate-700">{provider.models_count ?? 0}</span>
                    </div>
                  </div>

                  {/* Catalogue vs Runtime Label */}
                  {!isAvailable && (
                    <div className="text-[11px] text-slate-500 bg-slate-50 p-2 rounded border border-slate-100 mb-3">
                      Catalogue entry only. Integration adapter planned.
                    </div>
                  )}
                  {isAvailable && (
                    <div className="text-[11px] text-emerald-800 bg-emerald-50/60 p-2 rounded border border-emerald-100 mb-3 flex items-center gap-1.5">
                      <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                      Runtime adapter active. Primary platform driver.
                    </div>
                  )}
                </div>

                {/* Footer Actions */}
                <div className="pt-2 flex items-center justify-between gap-2 mt-auto">
                  {provider.documentation_url ? (
                    <a
                      href={provider.documentation_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center text-xs text-slate-500 hover:text-slate-800 hover:underline"
                    >
                      <ExternalLink className="w-3 h-3 mr-1" />
                      Docs
                    </a>
                  ) : (
                    <span className="text-xs text-slate-400">No docs link</span>
                  )}

                  <button
                    type="button"
                    onClick={() => setSelectedProviderId(provider.id)}
                    className="inline-flex items-center px-3 py-1.5 text-xs font-medium text-slate-700 hover:text-slate-900 bg-slate-100 hover:bg-slate-200 rounded-lg transition"
                  >
                    View Details
                    <ArrowRight className="w-3 h-3 ml-1" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
