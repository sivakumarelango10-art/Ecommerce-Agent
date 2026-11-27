import React, { useEffect, useState } from 'react';
import {
  Users,
  UserCheck,
  UserPlus,
  ShoppingBag,
  Sparkles,
  Cpu,
  ArrowRightLeft,
  Coins,
  RefreshCw,
  AlertCircle,
  HelpCircle,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api, ApiError } from '../../api/client';
import { AdminOverviewResponse } from '../../types/admin';

export interface AdminOverviewProps {
  onNavigate?: (tab: string) => void;
}

export const AdminOverview: React.FC<AdminOverviewProps> = ({ onNavigate }) => {
  const { accessToken } = useAuth();
  const [data, setData] = useState<AdminOverviewResponse | null>(null);
  const [fallbackRecoveryRate, setFallbackRecoveryRate] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOverview = async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.admin.getOverview(accessToken);
      setData(res);
      if (typeof res.fallback_recovery_rate === 'number') {
        setFallbackRecoveryRate(res.fallback_recovery_rate);
      } else if (res.personal_to_company_fallbacks === 0) {
        setFallbackRecoveryRate(100.0);
      } else {
        try {
          const fallbackRes = await api.admin.getAnalyticsFallbacks(accessToken);
          if (fallbackRes.total_fallbacks > 0) {
            setFallbackRecoveryRate(
              Number(((fallbackRes.successful_company_fallbacks / fallbackRes.total_fallbacks) * 100).toFixed(1))
            );
          } else {
            setFallbackRecoveryRate(100.0);
          }
        } catch {
          setFallbackRecoveryRate(null);
        }
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError('Failed to load admin overview metrics.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchOverview();
  }, [accessToken]);

  if (isLoading && !data) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-8 bg-slate-200 rounded-lg w-48"></div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-32 bg-white rounded-2xl border border-slate-200 p-5"></div>
          ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-32 bg-white rounded-2xl border border-slate-200 p-5"></div>
          ))}
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="bg-rose-50 border border-rose-200 rounded-2xl p-6 text-center max-w-lg mx-auto my-12">
        <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-2" />
        <h3 className="text-sm font-semibold text-rose-900">Failed to Load Overview</h3>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
        <button
          onClick={fetchOverview}
          className="mt-4 px-4 py-2 bg-rose-600 text-white rounded-lg text-xs font-semibold hover:bg-rose-700 transition"
        >
          Try Again
        </button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-8">
      {/* Top Welcome & Refresh Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Platform Overview</h1>
          <p className="text-xs text-slate-500 mt-1">
            Real-time telemetry aggregated across registered users, orders, and Gemini multi-key runtime.
          </p>
        </div>
        <button
          onClick={fetchOverview}
          className="self-start sm:self-auto inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 rounded-lg text-xs font-semibold shadow-2xs transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5 text-slate-500" />
          <span>Refresh Metrics</span>
        </button>
      </div>

      {/* Section 1: User & Order Health */}
      <div>
        <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">User & Commerce Health</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Total Registered Users */}
          <div
            onClick={() => onNavigate && onNavigate('users')}
            className={`bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow ${onNavigate ? 'cursor-pointer' : ''}`}
          >
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">Total Registered</span>
              <div className="w-8 h-8 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center">
                <Users className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{data.total_registered_users.toLocaleString()}</div>
            <div className="flex items-center gap-3 mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500">
              <span className="inline-flex items-center text-emerald-700 font-semibold gap-1">
                <UserCheck className="w-3 h-3" /> {data.active_users} active
              </span>
              <span>•</span>
              <span className="text-slate-400">{data.inactive_users} inactive</span>
            </div>
          </div>

          {/* New Users Today & This Month */}
          <div className="bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow">
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">New Registrations</span>
              <div className="w-8 h-8 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center">
                <UserPlus className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">+{data.new_users_today}</div>
            <div className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500">
              <span className="text-slate-700 font-medium">+{data.new_users_this_week} this week</span>
              <span>•</span>
              <span className="text-slate-500">+{data.new_users_this_month} this month</span>
            </div>
          </div>

          {/* Total Orders */}
          <div className="bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow">
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">Total Customer Orders</span>
              <div className="w-8 h-8 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center">
                <ShoppingBag className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{data.total_orders.toLocaleString()}</div>
            <div className="mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-400">
              Lifetime purchases completed
            </div>
          </div>

          {/* Guest AI Inquiries */}
          <div className="bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow">
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">Guest AI Inquiries</span>
              <div className="w-8 h-8 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center">
                <HelpCircle className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{data.guest_ai_requests.toLocaleString()}</div>
            <div className="mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500">
              Unauthenticated visitor conversations
            </div>
          </div>
        </div>
      </div>

      {/* Section 2: AI Execution & Multi-Key Telemetry */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider">AI Execution & Pool Health</h2>
          <div className="text-[11px] text-slate-500 bg-slate-100 px-2.5 py-1 rounded-md border border-slate-200">
            Distinct correlation IDs represent unique customer requests
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Logical Requests vs Actual Provider Attempts */}
          <div
            onClick={() => onNavigate && onNavigate('analytics')}
            className={`bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow ${onNavigate ? 'cursor-pointer' : ''}`}
          >
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">Logical AI Requests</span>
              <div className="w-8 h-8 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center">
                <Sparkles className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{data.total_logical_ai_requests.toLocaleString()}</div>
            <div className="mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500 flex justify-between">
              <span>Attempts per Req:</span>
              <span className="font-semibold text-slate-700">
                {data.total_logical_ai_requests > 0
                  ? (data.total_provider_attempts / data.total_logical_ai_requests).toFixed(2)
                  : '1.00'}
              </span>
            </div>
          </div>

          {/* Provider Attempts & Pool Breakdown */}
          <div
            onClick={() => onNavigate && onNavigate('providers')}
            className={`bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow ${onNavigate ? 'cursor-pointer' : ''}`}
          >
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">Total Provider Attempts</span>
              <div className="w-8 h-8 rounded-lg bg-purple-50 text-purple-600 flex items-center justify-center">
                <Cpu className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">{data.total_provider_attempts.toLocaleString()}</div>
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500">
              <span className="text-emerald-700 font-semibold">{data.company_provider_attempts.toLocaleString()} Company</span>
              <span>•</span>
              <span className="text-purple-700 font-semibold">{data.personal_provider_attempts.toLocaleString()} Personal</span>
            </div>
          </div>

          {/* Token Consumption */}
          <div className="bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow">
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">Token Consumption</span>
              <div className="w-8 h-8 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center">
                <Coins className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight font-mono">
              {data.total_tokens ? data.total_tokens.toLocaleString() : '0'}
            </div>
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500">
              <span>In: {data.total_input_tokens ? (data.total_input_tokens / 1000).toFixed(0) : '0'}k</span>
              <span>•</span>
              <span>Out: {data.total_output_tokens ? (data.total_output_tokens / 1000).toFixed(0) : '0'}k</span>
            </div>
          </div>

          {/* Personal Key Cooldown Fallbacks */}
          <div
            onClick={() => onNavigate && onNavigate('analytics')}
            className={`bg-white border border-slate-200/90 rounded-2xl p-5 shadow-2xs hover:shadow-xs transition-shadow ${onNavigate ? 'cursor-pointer' : ''}`}
          >
            <div className="flex items-center justify-between text-slate-500 mb-2">
              <span className="text-xs font-medium">Pool Fallbacks</span>
              <div className="w-8 h-8 rounded-lg bg-rose-50 text-rose-600 flex items-center justify-center">
                <ArrowRightLeft className="w-4 h-4" />
              </div>
            </div>
            <div className="text-2xl font-bold text-slate-900 tracking-tight">
              {data.personal_to_company_fallbacks.toLocaleString()}
            </div>
            <div className="mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500">
              Personal quota/error &rarr; company fallback
            </div>
          </div>
        </div>
      </div>

      {/* Section 3: System Reliability & Success Rate */}
      <div className="bg-white border border-slate-200/90 rounded-2xl p-6 shadow-2xs">
        <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-4">Runtime Reliability</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <div className="text-xs text-slate-500 mb-1">Attempt Success Rate</div>
            <div className="text-3xl font-extrabold text-emerald-600">
              {data.total_provider_attempts > 0
                ? ((data.successful_attempts / data.total_provider_attempts) * 100).toFixed(1)
                : '100.0'}
              %
            </div>
            <div className="text-xs text-slate-400 mt-1">
              {data.successful_attempts.toLocaleString()} successful / {data.failed_attempts.toLocaleString()} failed
            </div>
          </div>

          <div>
            <div className="text-xs text-slate-500 mb-1">Personal Key Contribution</div>
            <div className="text-3xl font-extrabold text-purple-600">
              {data.total_provider_attempts > 0
                ? ((data.personal_provider_attempts / data.total_provider_attempts) * 100).toFixed(1)
                : '0.0'}
              %
            </div>
            <div className="text-xs text-slate-400 mt-1">
              Of all execution turns handled by customer BYOK
            </div>
          </div>

          <div>
            <div className="text-xs text-slate-500 mb-1">Fallback Recovery Rate</div>
            <div className="text-3xl font-extrabold text-amber-600">
              {fallbackRecoveryRate !== null
                ? `${fallbackRecoveryRate.toFixed(1)}%`
                : 'N/A'}
            </div>
            <div className="text-xs text-slate-400 mt-1">
              Automated zero-downtime routing to company multi-key pool
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdminOverview;
