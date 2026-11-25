import React, { useState, useEffect, useCallback } from 'react';
import {
  Key,
  Plus,
  ShieldCheck,
  AlertCircle,
  CheckCircle2,
  Clock,
  Trash2,
  Edit2,
  Lock,
  Eye,
  EyeOff,
  RefreshCw,
  Search,
  Info,
  X,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type {
  AIProvider,
  PlatformAIKey,
  PlatformAIKeyCreateRequest,
  PlatformKeyStatus,
} from '../../types/admin';

export const PlatformKeyVault: React.FC = () => {
  const { accessToken, currentUser } = useAuth();
  const isSuperAdmin = currentUser?.role === 'super_admin';

  const [keys, setKeys] = useState<PlatformAIKey[]>([]);
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Filters
  const [selectedProviderId, setSelectedProviderId] = useState<string>('ALL');
  const [selectedStatus, setSelectedStatus] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  // Add Key Modal State
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [newKeyProviderId, setNewKeyProviderId] = useState('');
  const [newKeyLabel, setNewKeyLabel] = useState('');
  const [newKeyQuotaGroup, setNewKeyQuotaGroup] = useState('production-default');
  const [newRawApiKey, setNewRawApiKey] = useState('');
  const [showRawKeyInput, setShowRawKeyInput] = useState(false);
  const [isSavingKey, setIsSavingKey] = useState(false);
  const [addKeyError, setAddKeyError] = useState<string | null>(null);

  // Edit Key Modal State
  const [editingKey, setEditingKey] = useState<PlatformAIKey | null>(null);
  const [editLabel, setEditLabel] = useState('');
  const [editQuotaGroup, setEditQuotaGroup] = useState('');
  const [isUpdatingKey, setIsUpdatingKey] = useState(false);

  // Delete Confirmation State
  const [keyToDelete, setKeyToDelete] = useState<PlatformAIKey | null>(null);
  const [isDeletingKey, setIsDeletingKey] = useState(false);

  const fetchData = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const [keysRes, providersRes] = await Promise.all([
        api.admin.ai.listKeys(accessToken),
        api.admin.ai.listProviders(accessToken),
      ]);
      setKeys(keysRes);
      setProviders(providersRes);
      if (providersRes.length > 0 && !newKeyProviderId) {
        setNewKeyProviderId(providersRes[0].id);
      }
    } catch (err: any) {
      console.error('Failed to load platform keys or providers:', err);
      setError(err?.message || 'Failed to load platform key vault.');
    } finally {
      setLoading(false);
    }
  }, [accessToken, newKeyProviderId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Provider map for fast name lookup
  const providerMap = React.useMemo(() => {
    const map = new Map<string, AIProvider>();
    providers.forEach((p) => map.set(p.id, p));
    return map;
  }, [providers]);

  // Handle Add Key Submit
  const handleAddKeySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken || !isSuperAdmin) return;

    if (!newKeyProviderId) {
      setAddKeyError('Please select an AI provider.');
      return;
    }
    if (!newRawApiKey.trim()) {
      setAddKeyError('Please provide an API key.');
      return;
    }
    if (!newKeyQuotaGroup.trim()) {
      setAddKeyError('Quota group is mandatory.');
      return;
    }

    try {
      setIsSavingKey(true);
      setAddKeyError(null);

      const payload: PlatformAIKeyCreateRequest = {
        provider_id: newKeyProviderId,
        api_key: newRawApiKey.trim(),
        quota_group: newKeyQuotaGroup.trim(),
        key_label: newKeyLabel.trim() || 'Default Key',
        is_enabled: true,
      };

      await api.admin.ai.createKey(payload, accessToken);

      // SECURITY: Clear plaintext key immediately from memory and form state
      setNewRawApiKey('');
      setNewKeyLabel('');
      setNewKeyQuotaGroup('production-default');
      setIsAddModalOpen(false);
      setSuccessMessage('Company API key securely encrypted and stored with UNVERIFIED status.');

      await fetchData();
    } catch (err: any) {
      console.error('Failed to create platform key:', err);
      setAddKeyError(err?.message || 'Failed to save company API key.');
    } finally {
      setIsSavingKey(false);
    }
  };

  // Handle Toggle Key Status (Enable / Disable)
  const handleToggleKeyEnabled = async (key: PlatformAIKey) => {
    if (!accessToken || !isSuperAdmin) return;
    try {
      setError(null);
      const updated = await api.admin.ai.updateKey(
        key.id,
        { is_enabled: !key.is_enabled },
        accessToken
      );
      setKeys((prev) => prev.map((k) => (k.id === key.id ? updated : k)));
      setSuccessMessage(
        `Key '${key.key_label}' ${updated.is_enabled ? 'enabled' : 'disabled'} successfully.`
      );
    } catch (err: any) {
      console.error('Failed to toggle key status:', err);
      setError(err?.message || 'Failed to update key status.');
    }
  };

  // Open Edit Modal
  const openEditModal = (key: PlatformAIKey) => {
    setEditingKey(key);
    setEditLabel(key.key_label);
    setEditQuotaGroup(key.quota_group);
  };

  // Save Edit Key Submit
  const handleEditKeySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken || !isSuperAdmin || !editingKey) return;

    if (!editQuotaGroup.trim()) {
      setError('Quota group is mandatory.');
      return;
    }

    try {
      setIsUpdatingKey(true);
      setError(null);
      const updated = await api.admin.ai.updateKey(
        editingKey.id,
        {
          key_label: editLabel.trim() || editingKey.key_label,
          quota_group: editQuotaGroup.trim(),
        },
        accessToken
      );
      setKeys((prev) => prev.map((k) => (k.id === editingKey.id ? updated : k)));
      setEditingKey(null);
      setSuccessMessage(`Key '${updated.key_label}' updated successfully.`);
    } catch (err: any) {
      console.error('Failed to update platform key:', err);
      setError(err?.message || 'Failed to update platform key.');
    } finally {
      setIsUpdatingKey(false);
    }
  };

  // Confirm and Execute Delete Key
  const handleDeleteKey = async () => {
    if (!accessToken || !isSuperAdmin || !keyToDelete) return;

    try {
      setIsDeletingKey(true);
      setError(null);
      await api.admin.ai.deleteKey(keyToDelete.id, accessToken);
      setKeys((prev) => prev.filter((k) => k.id !== keyToDelete.id));
      setSuccessMessage(`Company API key '${keyToDelete.key_label}' permanently deleted.`);
      setKeyToDelete(null);
    } catch (err: any) {
      console.error('Failed to delete platform key:', err);
      setError(err?.message || 'Failed to delete platform key.');
    } finally {
      setIsDeletingKey(false);
    }
  };

  // Status Badge Helper
  const renderKeyStatusBadge = (status: PlatformKeyStatus) => {
    switch (status) {
      case 'ACTIVE':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
            <CheckCircle2 className="w-3 h-3 mr-1 text-emerald-600" />
            ACTIVE
          </span>
        );
      case 'COOLDOWN':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
            <Clock className="w-3 h-3 mr-1 text-amber-600" />
            COOLDOWN
          </span>
        );
      case 'QUOTA_EXHAUSTED':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-orange-100 text-orange-800 border border-orange-200">
            <AlertCircle className="w-3 h-3 mr-1 text-orange-600" />
            QUOTA EXHAUSTED
          </span>
        );
      case 'INVALID':
      case 'REVOKED':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-rose-100 text-rose-800 border border-rose-200">
            <AlertCircle className="w-3 h-3 mr-1 text-rose-600" />
            {status}
          </span>
        );
      case 'UNVERIFIED':
      default:
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-300">
            <Clock className="w-3 h-3 mr-1 text-slate-500" />
            UNVERIFIED
          </span>
        );
    }
  };

  // Filtered keys
  const filteredKeys = keys.filter((k) => {
    const matchesProvider = selectedProviderId === 'ALL' || k.provider_id === selectedProviderId;
    const matchesStatus = selectedStatus === 'ALL' || k.status === selectedStatus;
    const providerName = providerMap.get(k.provider_id)?.display_name || '';
    const matchesSearch =
      k.key_label.toLowerCase().includes(searchQuery.toLowerCase()) ||
      k.quota_group.toLowerCase().includes(searchQuery.toLowerCase()) ||
      k.key_hint.toLowerCase().includes(searchQuery.toLowerCase()) ||
      providerName.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesProvider && matchesStatus && matchesSearch;
  });

  return (
    <div className="space-y-6" data-testid="platform-key-vault">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-bold text-slate-900 tracking-tight">Company API Key Vault</h2>
            <span className="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-slate-200 text-slate-700">
              {keys.length} Key{keys.length === 1 ? '' : 's'}
            </span>
          </div>
          <p className="text-sm text-slate-500 mt-1">
            Centrally manage company-owned API credentials with AES-128 Fernet encryption at rest and domain separation.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={fetchData}
            disabled={loading}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 shadow-xs transition disabled:opacity-50"
            title="Refresh Keys"
          >
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>

          {isSuperAdmin ? (
            <button
              type="button"
              onClick={() => {
                setAddKeyError(null);
                setIsAddModalOpen(true);
              }}
              className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg shadow-xs transition"
            >
              <Plus className="w-4 h-4 mr-1.5" />
              Add Company Key
            </button>
          ) : (
            <span className="inline-flex items-center text-xs font-medium text-slate-500 bg-slate-100 px-3 py-2 rounded-lg border border-slate-200">
              <Lock className="w-3.5 h-3.5 mr-1.5 text-slate-400" />
              Super Admin required to manage keys
            </span>
          )}
        </div>
      </div>

      {/* Security Architecture & Unverified Status Disclosure */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="p-4 bg-slate-900 text-white rounded-xl shadow-xs border border-slate-800 text-xs space-y-1.5">
          <div className="flex items-center gap-2 text-emerald-400 font-semibold text-sm">
            <ShieldCheck className="w-4 h-4" />
            Cryptographic Vault Protections
          </div>
          <p className="text-slate-300 leading-relaxed">
            API keys are encrypted under <code className="text-emerald-300 font-mono">PLATFORM_KEY_ENCRYPTION_SECRET</code> using Fernet domain separation.
            Plaintext keys and ciphertexts are never logged, returned, or exposed in API payloads or browser storage.
          </p>
          <p className="text-slate-400 text-[11px]">
            User-owned personal Gemini keys are completely segregated and inaccessible to administrators.
          </p>
        </div>

        <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl text-xs space-y-1.5 text-amber-900">
          <div className="flex items-center gap-2 text-amber-800 font-semibold text-sm">
            <Info className="w-4 h-4" />
            Verification & Traffic Status
          </div>
          <p className="leading-relaxed">
            Newly registered company keys are initialized as <strong className="font-semibold">UNVERIFIED</strong>. Keys remain inactive for live AI traffic until a provider validation adapter verifies authentication and quota validity.
          </p>
          <p className="text-amber-700 text-[11px]">
            Validation actions are disabled for providers without operational verification adapters.
          </p>
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

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-white p-3.5 rounded-xl border border-slate-200 shadow-xs">
        <div className="relative flex-1">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search keys by label, quota group, hint or provider..."
            className="w-full pl-9 pr-3 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:bg-white transition"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Provider Filter */}
          <select
            value={selectedProviderId}
            onChange={(e) => setSelectedProviderId(e.target.value)}
            className="px-3 py-1.5 text-xs bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
            aria-label="Filter by Provider"
          >
            <option value="ALL">All Providers ({providers.length})</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name}
              </option>
            ))}
          </select>

          {/* Status Filter */}
          <select
            value={selectedStatus}
            onChange={(e) => setSelectedStatus(e.target.value)}
            className="px-3 py-1.5 text-xs bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
            aria-label="Filter by Status"
          >
            <option value="ALL">All Statuses</option>
            <option value="UNVERIFIED">UNVERIFIED</option>
            <option value="ACTIVE">ACTIVE</option>
            <option value="COOLDOWN">COOLDOWN</option>
            <option value="QUOTA_EXHAUSTED">QUOTA EXHAUSTED</option>
            <option value="INVALID">INVALID</option>
            <option value="REVOKED">REVOKED</option>
          </select>
        </div>
      </div>

      {/* Keys Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-xs overflow-hidden">
        {loading && keys.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-500">Loading company key vault...</div>
        ) : filteredKeys.length === 0 ? (
          <div className="p-12 text-center">
            <Key className="w-10 h-10 text-slate-300 mx-auto mb-3" />
            <h3 className="text-sm font-semibold text-slate-900">No Company Keys Found</h3>
            <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
              {keys.length === 0
                ? 'No company platform API keys have been registered in the vault yet.'
                : 'No keys matched your search and filter criteria.'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase tracking-wider">
                <tr>
                  <th className="py-3 px-4 font-semibold">Key Label</th>
                  <th className="py-3 px-4 font-semibold">Provider</th>
                  <th className="py-3 px-4 font-semibold">Masked Hint</th>
                  <th className="py-3 px-4 font-semibold">Quota Group</th>
                  <th className="py-3 px-4 font-semibold">Status</th>
                  <th className="py-3 px-4 font-semibold">Enabled</th>
                  <th className="py-3 px-4 font-semibold">Created / Cooldown</th>
                  <th className="py-3 px-4 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredKeys.map((key) => {
                  const provider = providerMap.get(key.provider_id);
                  return (
                    <tr key={key.id} className="hover:bg-slate-50/50" data-testid={`key-row-${key.id}`}>
                      {/* Label */}
                      <td className="py-3.5 px-4">
                        <div className="font-semibold text-slate-900">{key.key_label}</div>
                      </td>

                      {/* Provider */}
                      <td className="py-3.5 px-4">
                        <div className="font-medium text-slate-800">{provider?.display_name || 'Unknown'}</div>
                        <div className="text-[11px] font-mono text-slate-400">{provider?.code}</div>
                      </td>

                      {/* Masked Hint */}
                      <td className="py-3.5 px-4 font-mono text-slate-700">
                        <span className="px-2 py-0.5 bg-slate-100 rounded text-slate-800 font-semibold">
                          {key.key_hint}
                        </span>
                      </td>

                      {/* Quota Group */}
                      <td className="py-3.5 px-4 text-slate-600 font-mono text-[11px]">
                        {key.quota_group}
                      </td>

                      {/* Status */}
                      <td className="py-3.5 px-4">
                        {renderKeyStatusBadge(key.status)}
                      </td>

                      {/* Enabled Toggle */}
                      <td className="py-3.5 px-4">
                        {isSuperAdmin ? (
                          <button
                            type="button"
                            onClick={() => handleToggleKeyEnabled(key)}
                            className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                              key.is_enabled ? 'bg-emerald-600' : 'bg-slate-200'
                            }`}
                            role="switch"
                            aria-checked={key.is_enabled}
                            aria-label={`Toggle ${key.key_label}`}
                          >
                            <span
                              className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out ${
                                key.is_enabled ? 'translate-x-4' : 'translate-x-0'
                              }`}
                            />
                          </button>
                        ) : (
                          <span className={`text-xs font-semibold ${key.is_enabled ? 'text-emerald-700' : 'text-slate-400'}`}>
                            {key.is_enabled ? 'Enabled' : 'Disabled'}
                          </span>
                        )}
                      </td>

                      {/* Timestamps */}
                      <td className="py-3.5 px-4 text-slate-500 text-[11px]">
                        <div>Added: {new Date(key.created_at).toLocaleDateString()}</div>
                        {key.cooldown_until && (
                          <div className="text-amber-600">
                            Cooldown: {new Date(key.cooldown_until).toLocaleTimeString()}
                          </div>
                        )}
                      </td>

                      {/* Actions */}
                      <td className="py-3.5 px-4 text-right">
                        {isSuperAdmin ? (
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => openEditModal(key)}
                              className="p-1.5 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded transition"
                              title="Edit Label / Quota Group"
                              aria-label={`Edit ${key.key_label}`}
                            >
                              <Edit2 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setKeyToDelete(key)}
                              className="p-1.5 text-rose-500 hover:text-rose-700 hover:bg-rose-50 rounded transition"
                              title="Delete Key"
                              aria-label={`Delete ${key.key_label}`}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ) : (
                          <span className="text-slate-400 text-[11px]">Read-only</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modal: Add Company API Key */}
      {isAddModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl border border-slate-200 shadow-xl max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <Key className="w-5 h-5 text-emerald-600" />
                <h3 className="text-base font-bold text-slate-900">Add Company API Key</h3>
              </div>
              <button
                type="button"
                onClick={() => {
                  setNewRawApiKey('');
                  setIsAddModalOpen(false);
                }}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {addKeyError && (
              <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-rose-800 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0 text-rose-500" />
                <span>{addKeyError}</span>
              </div>
            )}

            <form onSubmit={handleAddKeySubmit} className="space-y-4">
              {/* Provider Selection */}
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  AI Provider <span className="text-rose-500">*</span>
                </label>
                <select
                  value={newKeyProviderId}
                  onChange={(e) => setNewKeyProviderId(e.target.value)}
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name} ({p.code}) — {p.connection_status}
                    </option>
                  ))}
                </select>
              </div>

              {/* Key Friendly Label */}
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Key Label <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  value={newKeyLabel}
                  onChange={(e) => setNewKeyLabel(e.target.value)}
                  placeholder="e.g. Primary Production Pool A"
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>

              {/* Quota Group */}
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Quota Group <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  value={newKeyQuotaGroup}
                  onChange={(e) => setNewKeyQuotaGroup(e.target.value)}
                  placeholder="e.g. production-default, tier-1-pool"
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 font-mono"
                />
                <span className="text-[11px] text-slate-400 mt-1 block">
                  Mandatory quota bucket / project identifier used for cooldown isolation.
                </span>
              </div>

              {/* API Key Input (Password field) */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-xs font-semibold text-slate-700">
                    Secret API Key <span className="text-rose-500">*</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => setShowRawKeyInput(!showRawKeyInput)}
                    className="text-[11px] text-slate-500 hover:text-slate-800 inline-flex items-center gap-1"
                  >
                    {showRawKeyInput ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                    {showRawKeyInput ? 'Hide' : 'Show'}
                  </button>
                </div>
                <input
                  type={showRawKeyInput ? 'text' : 'password'}
                  value={newRawApiKey}
                  onChange={(e) => setNewRawApiKey(e.target.value)}
                  placeholder="sk-... or AIzaSy..."
                  required
                  autoComplete="off"
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 font-mono"
                />
                <span className="text-[11px] text-slate-400 mt-1 block">
                  Encrypted immediately with Fernet. Plaintext is never stored in browser memory or database.
                </span>
              </div>

              {/* Modal Actions */}
              <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => {
                    setNewRawApiKey('');
                    setIsAddModalOpen(false);
                  }}
                  className="px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSavingKey}
                  className="px-4 py-2 text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg shadow-xs transition disabled:opacity-50"
                >
                  {isSavingKey ? 'Encrypting & Storing...' : 'Save & Encrypt Key'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Edit Key Metadata */}
      {editingKey && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl border border-slate-200 shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <h3 className="text-base font-bold text-slate-900">Edit Key Configuration</h3>
              <button
                type="button"
                onClick={() => setEditingKey(null)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleEditKeySubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Key Label</label>
                <input
                  type="text"
                  value={editLabel}
                  onChange={(e) => setEditLabel(e.target.value)}
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Quota Group</label>
                <input
                  type="text"
                  value={editQuotaGroup}
                  onChange={(e) => setEditQuotaGroup(e.target.value)}
                  required
                  className="w-full px-3 py-2 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 font-mono"
                />
              </div>

              <div className="text-xs text-slate-400 italic">
                Secret API key cannot be modified. To change credential, delete and register a new key.
              </div>

              <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setEditingKey(null)}
                  className="px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isUpdatingKey}
                  className="px-4 py-2 text-sm font-medium text-white bg-slate-900 hover:bg-slate-800 rounded-lg disabled:opacity-50"
                >
                  {isUpdatingKey ? 'Saving...' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Confirmation Modal: Delete Key */}
      {keyToDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-xs">
          <div className="bg-white rounded-2xl border border-slate-200 shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center gap-3 text-rose-600">
              <div className="p-2.5 bg-rose-100 rounded-xl">
                <Trash2 className="w-6 h-6 text-rose-600" />
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-900">Delete Company API Key</h3>
                <span className="text-xs text-slate-500">Irreversible action</span>
              </div>
            </div>

            <p className="text-sm text-slate-600 leading-relaxed">
              Are you sure you want to permanently delete the company key{' '}
              <strong className="text-slate-900 font-semibold">{keyToDelete.key_label}</strong> ({keyToDelete.key_hint})?
              Any workloads relying on this credential slot will fail.
            </p>

            <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-100">
              <button
                type="button"
                onClick={() => setKeyToDelete(null)}
                className="px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 rounded-lg"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteKey}
                disabled={isDeletingKey}
                className="px-4 py-2 text-sm font-medium text-white bg-rose-600 hover:bg-rose-700 rounded-lg shadow-xs disabled:opacity-50"
              >
                {isDeletingKey ? 'Deleting...' : 'Confirm Delete Key'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
