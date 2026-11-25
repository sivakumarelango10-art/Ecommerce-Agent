import React, { useState, useEffect, useCallback } from 'react';
import {
  ShieldCheck,
  ShieldAlert,
  RefreshCw,
  Terminal,
  Info,
  KeyRound,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type { AdminUserListItem } from '../../types/admin';

export const AdminManagement: React.FC = () => {
  const { accessToken, currentUser } = useAuth();
  const [admins, setAdmins] = useState<AdminUserListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isSuperAdmin = currentUser?.role === 'super_admin';

  const fetchAdmins = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      // Fetch admins and super_admins
      const [adminRes, superAdminRes] = await Promise.all([
        api.admin.listUsers(accessToken, { role: 'admin', page_size: 50 }),
        api.admin.listUsers(accessToken, { role: 'super_admin', page_size: 50 }),
      ]);
      const combined = [...superAdminRes.items, ...adminRes.items];
      // Deduplicate by ID
      const map = new Map<string, AdminUserListItem>();
      combined.forEach((item) => map.set(item.id, item));
      setAdmins(Array.from(map.values()));
    } catch (err: any) {
      console.error('Failed to load administrator list:', err);
      setError(err?.message || 'Failed to fetch administrator accounts.');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    fetchAdmins();
  }, [fetchAdmins]);

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">Administrator Roster</h2>
          <p className="text-sm text-slate-500">
            View privileged administrator and super administrator accounts with console access.
          </p>
        </div>

        <button
          onClick={() => fetchAdmins()}
          disabled={loading}
          className="inline-flex items-center px-3.5 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 shadow-sm transition disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Provisioning Info Box */}
      <div className="bg-slate-900 text-slate-100 rounded-2xl p-6 shadow-md border border-slate-800 space-y-4">
        <div className="flex items-center space-x-2 text-emerald-400">
          <Terminal className="w-5 h-5" />
          <h3 className="text-sm font-bold uppercase tracking-wider">
            Secure Administrator Provisioning Architecture
          </h3>
        </div>
        <p className="text-xs text-slate-300 leading-relaxed">
          StoreFlow strictly prohibits open public administrator registration. New administrator
          accounts can be provisioned through two verified paths:
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div className="bg-slate-800/80 p-4 rounded-xl border border-slate-700/60 space-y-2">
            <div className="flex items-center space-x-2 font-semibold text-slate-200">
              <KeyRound className="w-4 h-4 text-emerald-400" />
              <span>1. Direct CLI Provisioning</span>
            </div>
            <p className="text-slate-400 text-[11px] leading-normal">
              Execute the interactive CLI script with Argon2id password hashing and secure masked input:
            </p>
            <pre className="bg-black/60 p-2.5 rounded-lg text-[11px] font-mono text-emerald-300 overflow-x-auto">
              python backend/scripts/create_admin.py
            </pre>
          </div>

          <div className="bg-slate-800/80 p-4 rounded-xl border border-slate-700/60 space-y-2">
            <div className="flex items-center space-x-2 font-semibold text-slate-200">
              <ShieldCheck className="w-4 h-4 text-purple-400" />
              <span>2. Super Admin Role Elevation</span>
            </div>
            <p className="text-slate-400 text-[11px] leading-normal">
              An existing <code className="text-purple-300">super_admin</code> can promote verified customer
              accounts directly within the <strong>User Management</strong> view with full audit logging.
              {isSuperAdmin && ' You have Super Admin access.'}
            </p>
            <div className="text-[11px] text-amber-300/80 flex items-center space-x-1.5 pt-1">
              <Info className="w-3.5 h-3.5 shrink-0" />
              <span>Enterprise invite token system scheduled for next release.</span>
            </div>
          </div>
        </div>
      </div>

      {/* Admin Accounts Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <ShieldCheck className="w-5 h-5 text-emerald-600" />
            <h3 className="text-base font-bold text-slate-900">Privileged Accounts</h3>
          </div>
          <span className="text-xs text-slate-500 font-medium">
            Total Privileged Users: {admins.length}
          </span>
        </div>

        {loading ? (
          <div className="p-12 text-center space-y-3">
            <RefreshCw className="w-8 h-8 text-emerald-600 animate-spin mx-auto" />
            <p className="text-sm text-slate-500">Loading administrator accounts...</p>
          </div>
        ) : error ? (
          <div className="p-8 text-center space-y-3">
            <ShieldAlert className="w-8 h-8 text-rose-500 mx-auto" />
            <p className="text-sm font-semibold text-rose-700">{error}</p>
            <button
              onClick={() => fetchAdmins()}
              className="px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800"
            >
              Try Again
            </button>
          </div>
        ) : admins.length === 0 ? (
          <div className="p-12 text-center text-sm text-slate-500">
            No privileged administrator accounts found.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold text-slate-600 uppercase">
                <tr>
                  <th className="px-5 py-3">Administrator</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Logical Req</th>
                  <th className="px-4 py-3 text-right">Provider Attempts</th>
                  <th className="px-5 py-3">Created At</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {admins.map((adm) => (
                  <tr key={adm.id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-5 py-3.5">
                      <div className="font-medium text-slate-900">{adm.email}</div>
                      <div className="text-xs text-slate-400 font-mono">ID: {adm.id}</div>
                    </td>

                    <td className="px-4 py-3.5 whitespace-nowrap">
                      {adm.role === 'super_admin' ? (
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800 border border-purple-200">
                          Super Admin
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
                          Admin
                        </span>
                      )}
                    </td>

                    <td className="px-4 py-3.5 whitespace-nowrap">
                      {adm.is_active ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
                          Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-rose-50 text-rose-700 border border-rose-200">
                          Suspended
                        </span>
                      )}
                    </td>

                    <td className="px-4 py-3.5 text-right font-medium text-slate-800">
                      {adm.total_requests.toLocaleString()}
                    </td>

                    <td className="px-4 py-3.5 text-right text-xs text-slate-600">
                      {adm.total_attempts.toLocaleString()}
                    </td>

                    <td className="px-5 py-3.5 text-xs text-slate-500 whitespace-nowrap font-mono">
                      {new Date(adm.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
export default AdminManagement;
