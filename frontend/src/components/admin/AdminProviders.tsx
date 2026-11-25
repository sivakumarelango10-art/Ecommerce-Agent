import React, { useState, useEffect, useCallback } from 'react';
import {
  Key,
  AlertTriangle,
  XCircle,
  RefreshCw,
  Clock,
  Info,
  CheckCircle2,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type { AdminProvidersResponse } from '../../types/admin';

export const AdminProviders: React.FC = () => {
  const { accessToken } = useAuth();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [providersData, setProvidersData] = useState<AdminProvidersResponse | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchProviders = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const data = await api.admin.getProviders(accessToken);
      setProvidersData(data);
    } catch (err: any) {
      console.error('Failed to fetch provider runtime status:', err);
      setError(err?.message || 'Failed to load provider runtime pool data.');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  // Periodic polling if auto-refresh is active (every 10s)
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchProviders();
    }, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchProviders]);

  const renderHealthBadge = (health: string, inCooldown: boolean, remainingSeconds: number) => {
    if (health === 'HEALTHY' && !inCooldown) {
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
          <CheckCircle2 className="w-3 h-3 mr-1 text-emerald-600" />
          Healthy
        </span>
      );
    }
    if (inCooldown || health === 'COOLDOWN') {
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
          <Clock className="w-3 h-3 mr-1 text-amber-600" />
          Cooldown ({remainingSeconds}s)
        </span>
      );
    }
    return (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-100 text-rose-800 border border-rose-200">
        <XCircle className="w-3 h-3 mr-1 text-rose-600" />
        Disabled
      </span>
    );
  };

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">AI Providers & Platform Keys</h2>
          <p className="text-sm text-slate-500">
            In-memory status of company-managed Gemini API key slots and cooldown states for the handling worker process.
          </p>
        </div>

        <div className="flex items-center space-x-3">
          <label className="flex items-center text-xs font-medium text-slate-600 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 mr-2"
            />
            Live Polling (10s)
          </label>

          <button
            onClick={() => fetchProviders()}
            disabled={loading}
            className="inline-flex items-center px-3.5 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 shadow-sm transition disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Architecture Accuracy Alert: Worker-specific in-memory state */}
      <div className="p-4 bg-amber-50/70 border border-amber-200 rounded-xl flex items-start space-x-3 text-xs text-amber-950">
        <Info className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <div>
            <strong className="font-semibold">Worker-Specific Runtime Snapshot:</strong> This view reflects
            the in-memory key pool status returned by the specific backend worker process handling this request.
          </div>
          <p className="text-amber-900 text-[11px] leading-relaxed">
            Because Gemini key health, failure counts, and cooldown timers are maintained in worker memory rather than a shared database, separate backend worker processes can have different cooldown and health states. This view is not a globally synchronized cluster state.
          </p>
          <p className="text-amber-800 text-[11px] font-medium pt-0.5">
            Key secrets remain cryptographically masked in memory and are never transmitted over the wire or exposed in API responses.
          </p>
        </div>
      </div>

      {loading && !providersData ? (
        <div className="py-24 text-center bg-white rounded-xl border border-slate-200 shadow-sm">
          <RefreshCw className="w-8 h-8 text-emerald-600 animate-spin mx-auto mb-3" />
          <p className="text-sm font-medium text-slate-600">Inspecting company key slots...</p>
        </div>
      ) : error ? (
        <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-center space-y-3">
          <AlertTriangle className="w-8 h-8 text-rose-500 mx-auto" />
          <p className="text-sm font-semibold text-rose-800">{error}</p>
          <button
            onClick={() => fetchProviders()}
            className="px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      ) : providersData ? (
        <>
          {/* Top Metric Cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Provider & Model</div>
              <div className="text-base font-bold text-slate-900 mt-1 uppercase">
                {providersData.provider}
              </div>
              <div className="text-xs font-mono text-emerald-600 mt-0.5">
                {providersData.configured_model}
              </div>
            </div>

            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Total Key Slots</div>
              <div className="text-2xl font-bold text-slate-900 mt-1">
                {providersData.total_slots}
              </div>
              <div className="text-xs text-slate-400 mt-0.5">Configured in pool</div>
            </div>

            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Healthy Slots</div>
              <div className="text-2xl font-bold text-emerald-600 mt-1">
                {providersData.healthy_slots}
              </div>
              <div className="text-xs text-slate-400 mt-0.5">Ready for requests</div>
            </div>

            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">In Cooldown</div>
              <div className="text-2xl font-bold text-amber-600 mt-1">
                {providersData.cooldown_slots}
              </div>
              <div className="text-xs text-slate-400 mt-0.5">Rate/Quota recovery</div>
            </div>

            <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Disabled Slots</div>
              <div className="text-2xl font-bold text-rose-600 mt-1">
                {providersData.disabled_slots}
              </div>
              <div className="text-xs text-slate-400 mt-0.5">Suspended / invalid</div>
            </div>
          </div>

          {/* Key Slots Table */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Key className="w-5 h-5 text-emerald-600" />
                <h3 className="text-base font-bold text-slate-900">Key Slot Rotation Pool</h3>
              </div>
              <div className="flex items-center space-x-3 text-xs">
                <span className="text-slate-500 font-mono">
                  Model: {providersData.configured_model}
                </span>
                <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-600 text-[11px]">
                  Worker Snapshot
                </span>
              </div>
            </div>

            <div className="overflow-x-auto">
              {providersData.keys.length > 0 ? (
                <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                  <thead className="bg-slate-50 text-xs font-semibold text-slate-600 uppercase">
                    <tr>
                      <th className="px-5 py-3">Slot #</th>
                      <th className="px-4 py-3">Masked Key</th>
                      <th className="px-4 py-3">Quota Group</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3 text-right">Failures</th>
                      <th className="px-4 py-3 text-right">Cooldown Remaining</th>
                      <th className="px-5 py-3">Last Used At</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 bg-white">
                    {providersData.keys.map((slot) => (
                      <tr key={slot.slot_id} className="hover:bg-slate-50/80 transition-colors">
                        <td className="px-5 py-3.5 font-mono text-xs font-bold text-slate-700">
                          Slot {slot.slot_index + 1}
                          <div className="text-[10px] text-slate-400 font-normal">{slot.slot_id}</div>
                        </td>

                        <td className="px-4 py-3.5 font-mono text-xs text-slate-900 font-semibold">
                          {slot.masked_key}
                        </td>

                        <td className="px-4 py-3.5 text-xs text-slate-600">
                          <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 font-mono">
                            {slot.quota_group}
                          </span>
                        </td>

                        <td className="px-4 py-3.5 whitespace-nowrap">
                          {renderHealthBadge(
                            slot.health_status,
                            slot.in_cooldown,
                            slot.cooldown_remaining_seconds
                          )}
                        </td>

                        <td className="px-4 py-3.5 text-right font-medium text-slate-800">
                          {slot.failure_count}
                        </td>

                        <td className="px-4 py-3.5 text-right font-mono text-xs">
                          {slot.cooldown_remaining_seconds > 0 ? (
                            <span className="text-amber-700 font-semibold">
                              {slot.cooldown_remaining_seconds}s
                            </span>
                          ) : (
                            <span className="text-slate-400">0s</span>
                          )}
                        </td>

                        <td className="px-5 py-3.5 text-xs text-slate-500 whitespace-nowrap">
                          {slot.last_used_at ? new Date(slot.last_used_at).toLocaleTimeString() : 'Never'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="p-8 text-center text-sm text-slate-500">
                  No company key slots configured. Check server GEMINI_API_KEYS environment configuration.
                </div>
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
};
export default AdminProviders;
