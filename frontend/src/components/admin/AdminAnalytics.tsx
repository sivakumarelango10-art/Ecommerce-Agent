import React, { useState, useEffect, useCallback } from 'react';
import {
  BarChart3,
  ArrowRightLeft,
  RefreshCw,
  Cpu,
  Database,
  AlertTriangle,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type {
  AdminAnalyticsUsageResponse,
  AdminModelsResponse,
  AdminSourcesResponse,
  AdminFallbacksResponse,
} from '../../types/admin';

export const AdminAnalytics: React.FC = () => {
  const { accessToken } = useAuth();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Time & Grouping controls
  const [timeRange, setTimeRange] = useState<'7d' | '14d' | '30d' | 'all'>('7d');
  const [groupBy, setGroupBy] = useState<'daily' | 'hourly'>('daily');

  // Responses
  const [usage, setUsage] = useState<AdminAnalyticsUsageResponse | null>(null);
  const [models, setModels] = useState<AdminModelsResponse | null>(null);
  const [sources, setSources] = useState<AdminSourcesResponse | null>(null);
  const [fallbacks, setFallbacks] = useState<AdminFallbacksResponse | null>(null);

  // Compute start date based on preset
  const getDateRange = useCallback(() => {
    if (timeRange === 'all') return { start_date: undefined, end_date: undefined };
    const days = timeRange === '7d' ? 7 : timeRange === '14d' ? 14 : 30;
    const start = new Date();
    start.setDate(start.getDate() - days);
    return {
      start_date: start.toISOString(),
      end_date: new Date().toISOString(),
    };
  }, [timeRange]);

  const fetchAnalytics = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const { start_date, end_date } = getDateRange();

      const [usageData, modelsData, sourcesData, fallbacksData] = await Promise.all([
        api.admin.getAnalyticsUsage(accessToken, { group_by: groupBy, start_date, end_date }),
        api.admin.getAnalyticsModels(accessToken, { start_date, end_date }),
        api.admin.getAnalyticsSources(accessToken, { start_date, end_date }),
        api.admin.getAnalyticsFallbacks(accessToken, { start_date, end_date }),
      ]);

      setUsage(usageData);
      setModels(modelsData);
      setSources(sourcesData);
      setFallbacks(fallbacksData);
    } catch (err: any) {
      console.error('Failed to load admin analytics:', err);
      setError(err?.message || 'Failed to load telemetry analytics.');
    } finally {
      setLoading(false);
    }
  }, [accessToken, groupBy, getDateRange]);

  useEffect(() => {
    fetchAnalytics();
  }, [fetchAnalytics]);

  // Aggregate totals from usage data
  const totalLogicalReqs = usage?.data.reduce((acc, p) => acc + p.logical_requests, 0) || 0;
  const totalProviderAttempts = usage?.data.reduce((acc, p) => acc + p.provider_attempts, 0) || 0;
  const totalTokens = usage?.data.reduce((acc, p) => acc + (p.total_tokens || 0), 0) || 0;

  return (
    <div className="space-y-6">
      {/* Top Header & Range Selectors */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">AI Telemetry & Analytics</h2>
          <p className="text-sm text-slate-500">
            Monitor model latencies, token consumption, quota fallbacks, and provider execution volume.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Time range selector */}
          <div className="inline-flex rounded-lg border border-slate-300 bg-white p-1 shadow-sm">
            {(['7d', '14d', '30d', 'all'] as const).map((range) => (
              <button
                key={range}
                onClick={() => setTimeRange(range)}
                className={`px-3 py-1 text-xs font-semibold rounded-md transition ${
                  timeRange === range
                    ? 'bg-slate-900 text-white shadow-xs'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
                }`}
              >
                {range === 'all' ? 'All Time' : `${range.toUpperCase()}`}
              </button>
            ))}
          </div>

          {/* Group By selector */}
          <div className="inline-flex rounded-lg border border-slate-300 bg-white p-1 shadow-sm">
            {(['daily', 'hourly'] as const).map((g) => (
              <button
                key={g}
                onClick={() => setGroupBy(g)}
                className={`px-3 py-1 text-xs font-semibold rounded-md capitalize transition ${
                  groupBy === g
                    ? 'bg-emerald-600 text-white shadow-xs'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
                }`}
              >
                {g}
              </button>
            ))}
          </div>

          <button
            onClick={() => fetchAnalytics()}
            disabled={loading}
            className="p-2 text-slate-600 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 shadow-sm transition disabled:opacity-50"
            title="Refresh analytics"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {loading && !usage ? (
        <div className="py-24 text-center bg-white rounded-xl border border-slate-200 shadow-sm">
          <RefreshCw className="w-8 h-8 text-emerald-600 animate-spin mx-auto mb-3" />
          <p className="text-sm font-medium text-slate-600">Aggregating telemetry events...</p>
        </div>
      ) : error ? (
        <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-center space-y-3">
          <AlertTriangle className="w-8 h-8 text-rose-500 mx-auto" />
          <p className="text-sm font-semibold text-rose-800">{error}</p>
          <button
            onClick={() => fetchAnalytics()}
            className="px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          {/* High-level Summary Cards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Logical AI Requests</div>
              <div className="text-2xl font-bold text-slate-900 mt-1">
                {totalLogicalReqs.toLocaleString()}
              </div>
              <div className="text-xs text-slate-400 mt-1">Distinct client-initiated turns</div>
            </div>

            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Total Provider Attempts</div>
              <div className="text-2xl font-bold text-slate-900 mt-1">
                {totalProviderAttempts.toLocaleString()}
              </div>
              <div className="text-xs text-emerald-600 mt-1 font-medium">
                {totalLogicalReqs > 0
                  ? `${(totalProviderAttempts / totalLogicalReqs).toFixed(2)} attempts / request`
                  : '—'}
              </div>
            </div>

            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Token Consumption</div>
              <div className="text-2xl font-bold text-slate-900 mt-1">
                {totalTokens.toLocaleString()}
              </div>
              <div className="text-xs text-slate-400 mt-1">Total combined prompt & completion</div>
            </div>

            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
              <div className="text-xs font-medium text-slate-500">Personal-to-Company Fallbacks</div>
              <div className="text-2xl font-bold text-amber-600 mt-1">
                {fallbacks?.total_fallbacks?.toLocaleString() || 0}
              </div>
              <div className="text-xs text-slate-400 mt-1">
                {fallbacks && fallbacks.total_fallbacks > 0
                  ? `${((fallbacks.successful_company_fallbacks / fallbacks.total_fallbacks) * 100).toFixed(0)}% fallback recovery rate`
                  : 'Zero fallback triggers'}
              </div>
            </div>
          </div>

          {/* Timeline Table / Visual Representation */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <BarChart3 className="w-5 h-5 text-emerald-600" />
                <h3 className="text-base font-bold text-slate-900">Execution Volume by Period</h3>
              </div>
              <span className="text-xs font-medium text-slate-500 capitalize">Grouping: {groupBy}</span>
            </div>

            <div className="overflow-x-auto">
              {usage && usage.data.length > 0 ? (
                <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                  <thead className="bg-slate-50 text-xs font-semibold text-slate-600 uppercase">
                    <tr>
                      <th className="px-5 py-3">Period</th>
                      <th className="px-4 py-3 text-right">Logical Reqs</th>
                      <th className="px-4 py-3 text-right">Provider Attempts</th>
                      <th className="px-4 py-3 text-right">Company Pool</th>
                      <th className="px-4 py-3 text-right">Personal Key</th>
                      <th className="px-4 py-3 text-right">Guest Reqs</th>
                      <th className="px-4 py-3 text-right">Tokens</th>
                      <th className="px-4 py-3 text-right">Fallbacks</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 bg-white">
                    {usage.data.map((point) => (
                      <tr key={point.period} className="hover:bg-slate-50/80 transition-colors">
                        <td className="px-5 py-3 font-mono text-xs font-medium text-slate-900">
                          {point.period}
                        </td>
                        <td className="px-4 py-3 text-right font-medium text-slate-800">
                          {point.logical_requests.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-right text-slate-700">
                          {point.provider_attempts.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-right text-xs text-slate-600">
                          {point.company_attempts.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-right text-xs text-slate-600">
                          {point.personal_attempts.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-right text-xs text-slate-500">
                          {point.guest_requests.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-xs text-slate-700">
                          {point.total_tokens ? point.total_tokens.toLocaleString() : '—'}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {point.fallbacks > 0 ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800">
                              {point.fallbacks}
                            </span>
                          ) : (
                            <span className="text-xs text-slate-400">0</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="p-8 text-center text-sm text-slate-500">
                  No execution volume recorded for the selected window.
                </div>
              )}
            </div>
          </div>

          {/* Model Breakdown & Key Source Distribution */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Model Breakdown */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden flex flex-col">
              <div className="p-4 border-b border-slate-200 flex items-center space-x-2">
                <Cpu className="w-5 h-5 text-purple-600" />
                <h3 className="text-base font-bold text-slate-900">Model Performance & Tokens</h3>
              </div>
              <div className="p-4 flex-1">
                {models && models.models.length > 0 ? (
                  <div className="space-y-4">
                    {models.models.map((m) => {
                      const successRate =
                        m.provider_attempts > 0
                          ? ((m.successful_attempts / m.provider_attempts) * 100).toFixed(1)
                          : '100';
                      return (
                        <div
                          key={m.model_name}
                          className="p-3.5 bg-slate-50 rounded-xl border border-slate-200 space-y-2"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-mono text-xs font-bold text-slate-900">
                              {m.model_name}
                            </span>
                            <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                              {successRate}% Success
                            </span>
                          </div>

                          <div className="grid grid-cols-3 gap-2 text-xs text-slate-600 pt-1">
                            <div>
                              <span className="text-slate-400">Attempts:</span>{' '}
                              <strong className="text-slate-800">{m.provider_attempts}</strong>
                            </div>
                            <div>
                              <span className="text-slate-400">Latency:</span>{' '}
                              <strong className="text-slate-800">
                                {m.avg_latency_ms ? `${m.avg_latency_ms.toFixed(0)}ms` : '—'}
                              </strong>
                            </div>
                            <div>
                              <span className="text-slate-400">Tokens:</span>{' '}
                              <strong className="text-slate-800">
                                {m.total_tokens ? m.total_tokens.toLocaleString() : '0'}
                              </strong>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="py-12 text-center text-sm text-slate-400">
                    No model telemetry recorded in this period.
                  </div>
                )}
              </div>
            </div>

            {/* Source Distribution */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden flex flex-col">
              <div className="p-4 border-b border-slate-200 flex items-center space-x-2">
                <Database className="w-5 h-5 text-emerald-600" />
                <h3 className="text-base font-bold text-slate-900">Key Source Distribution</h3>
              </div>
              <div className="p-4 flex-1 space-y-4">
                {sources && sources.sources.length > 0 ? (
                  sources.sources.map((src) => {
                    const pct =
                      totalProviderAttempts > 0
                        ? ((src.provider_attempts / totalProviderAttempts) * 100).toFixed(1)
                        : '0';
                    return (
                      <div key={src.source} className="space-y-1.5">
                        <div className="flex items-center justify-between text-xs font-medium">
                          <span className="capitalize text-slate-800 font-semibold">{src.source} Keys</span>
                          <span className="text-slate-600">
                            {src.provider_attempts.toLocaleString()} ({pct}%)
                          </span>
                        </div>
                        <div className="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden">
                          <div
                            className={`h-2.5 rounded-full transition-all duration-500 ${
                              src.source === 'company'
                                ? 'bg-emerald-600'
                                : src.source === 'personal'
                                ? 'bg-purple-600'
                                : 'bg-slate-500'
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <div className="flex justify-between text-[11px] text-slate-400">
                          <span>Successful: {src.successful_attempts}</span>
                          <span>Failed: {src.failed_attempts}</span>
                          <span>Tokens: {src.total_tokens?.toLocaleString() || '0'}</span>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="py-12 text-center text-sm text-slate-400">
                    No source telemetry available.
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Fallback Analysis Card */}
          {fallbacks && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 space-y-4">
              <div className="flex items-center space-x-2">
                <ArrowRightLeft className="w-5 h-5 text-amber-600" />
                <h3 className="text-base font-bold text-slate-900">
                  Automated Fallback Diagnostics
                </h3>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-center">
                <div className="bg-amber-50/60 p-3.5 rounded-xl border border-amber-200">
                  <div className="text-xs font-medium text-amber-800">Personal Quota Failures</div>
                  <div className="text-xl font-bold text-amber-900 mt-1">
                    {fallbacks.personal_quota_failures}
                  </div>
                </div>

                <div className="bg-rose-50/60 p-3.5 rounded-xl border border-rose-200">
                  <div className="text-xs font-medium text-rose-800">Invalid Personal Keys</div>
                  <div className="text-xl font-bold text-rose-900 mt-1">
                    {fallbacks.invalid_personal_credentials}
                  </div>
                </div>

                <div className="bg-blue-50/60 p-3.5 rounded-xl border border-blue-200">
                  <div className="text-xs font-medium text-blue-800">Temporary Failures</div>
                  <div className="text-xl font-bold text-blue-900 mt-1">
                    {fallbacks.temporary_provider_failures}
                  </div>
                </div>

                <div className="bg-emerald-50/60 p-3.5 rounded-xl border border-emerald-200">
                  <div className="text-xs font-medium text-emerald-800">Recovered by Company Pool</div>
                  <div className="text-xl font-bold text-emerald-900 mt-1">
                    {fallbacks.successful_company_fallbacks}
                  </div>
                </div>
              </div>

              {fallbacks.breakdown_by_reason.length > 0 && (
                <div className="mt-4 pt-4 border-t border-slate-100">
                  <h4 className="text-xs font-semibold text-slate-700 uppercase mb-2">
                    Fallback Reasons Recorded
                  </h4>
                  <div className="divide-y divide-slate-100 text-xs">
                    {fallbacks.breakdown_by_reason.map((item, idx) => (
                      <div key={idx} className="py-2 flex items-center justify-between">
                        <span className="font-mono text-slate-700">{item.reason}</span>
                        <div className="space-x-4 text-slate-500">
                          <span>Occurrences: <strong className="text-slate-800">{item.count}</strong></span>
                          <span>Last: {item.last_occurred ? new Date(item.last_occurred).toLocaleDateString() : '—'}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
};
export default AdminAnalytics;
