import React, { useState, useEffect, useCallback } from 'react';
import {
  FileText,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  ShieldAlert,
  X,
  Code,
  Info,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type { AdminAuditLogItem, AdminAuditLogResponse } from '../../types/admin';

export const AdminAuditLogs: React.FC = () => {
  const { accessToken } = useAuth();
  const [logs, setLogs] = useState<AdminAuditLogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters & Pagination
  const [actionFilter, setActionFilter] = useState('');
  const [targetTypeFilter, setTargetTypeFilter] = useState('');
  const [actorFilter, setActorFilter] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(15);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  // Selected Log for Metadata Modal
  const [selectedLog, setSelectedLog] = useState<AdminAuditLogItem | null>(null);

  const fetchLogs = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const res: AdminAuditLogResponse = await api.admin.listAuditLogs(accessToken, {
        page,
        page_size: pageSize,
        action: actionFilter.trim() || undefined,
        target_type: targetTypeFilter.trim() || undefined,
        actor_id: actorFilter.trim() || undefined,
      });
      setLogs(res.items);
      setTotalPages(res.total_pages);
      setTotalCount(res.total);
    } catch (err: any) {
      console.error('Failed to load audit logs:', err);
      setError(err?.message || 'Failed to fetch audit log trail.');
    } finally {
      setLoading(false);
    }
  }, [accessToken, page, pageSize, actionFilter, targetTypeFilter, actorFilter]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  const renderActionBadge = (action: string) => {
    if (action.includes('role')) {
      return (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-purple-100 text-purple-800 border border-purple-200">
          {action}
        </span>
      );
    }
    if (action.includes('status')) {
      return (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
          {action}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-slate-100 text-slate-800 border border-slate-200">
        {action}
      </span>
    );
  };

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">Security & Audit Logs</h2>
          <p className="text-sm text-slate-500">
            Immutable log of all administrative operations, role promotions, and user status modifications.
          </p>
        </div>

        <button
          onClick={() => fetchLogs()}
          disabled={loading}
          className="inline-flex items-center px-3.5 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 shadow-sm transition disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Filter Bar */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Action Filter */}
          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1">Action</label>
            <input
              type="text"
              placeholder="Filter by action (e.g. user.role_change)..."
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value);
                setPage(1);
              }}
              className="w-full px-3 py-2 text-xs border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            />
          </div>

          {/* Target Type Filter */}
          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1">Target Type</label>
            <select
              value={targetTypeFilter}
              onChange={(e) => {
                setTargetTypeFilter(e.target.value);
                setPage(1);
              }}
              className="w-full px-3 py-2 text-xs border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            >
              <option value="">All Targets</option>
              <option value="user">User</option>
              <option value="system">System</option>
              <option value="provider">Provider</option>
            </select>
          </div>

          {/* Actor Filter */}
          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1">Actor ID / Email</label>
            <input
              type="text"
              placeholder="Search actor..."
              value={actorFilter}
              onChange={(e) => {
                setActorFilter(e.target.value);
                setPage(1);
              }}
              className="w-full px-3 py-2 text-xs border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            />
          </div>
        </div>

        <div className="flex items-center justify-between text-xs text-slate-500 pt-1 border-t border-slate-100">
          <span>
            Total audit events: <strong className="font-semibold text-slate-700">{totalCount}</strong>
          </span>
          {(actionFilter || targetTypeFilter || actorFilter) && (
            <button
              onClick={() => {
                setActionFilter('');
                setTargetTypeFilter('');
                setActorFilter('');
                setPage(1);
              }}
              className="text-emerald-600 hover:text-emerald-700 font-medium"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Audit Logs Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-12 text-center space-y-3">
            <RefreshCw className="w-8 h-8 text-emerald-600 animate-spin mx-auto" />
            <p className="text-sm text-slate-500">Retrieving audit log trail...</p>
          </div>
        ) : error ? (
          <div className="p-8 text-center space-y-3">
            <ShieldAlert className="w-8 h-8 text-rose-500 mx-auto" />
            <p className="text-sm font-semibold text-rose-700">{error}</p>
            <button
              onClick={() => fetchLogs()}
              className="px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800"
            >
              Try Again
            </button>
          </div>
        ) : logs.length === 0 ? (
          <div className="p-12 text-center space-y-2">
            <Info className="w-10 h-10 text-slate-300 mx-auto" />
            <p className="text-base font-semibold text-slate-800">No audit log records found</p>
            <p className="text-sm text-slate-500">No matching security events were logged in this view.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold text-slate-600 uppercase">
                <tr>
                  <th className="px-5 py-3">Timestamp</th>
                  <th className="px-4 py-3">Actor</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Target</th>
                  <th className="px-4 py-3">IP Address</th>
                  <th className="px-5 py-3 text-right">Metadata</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {logs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-5 py-3.5 whitespace-nowrap text-xs text-slate-600 font-mono">
                      {new Date(log.created_at).toLocaleString()}
                    </td>

                    <td className="px-4 py-3.5 text-xs text-slate-900">
                      <div className="font-medium">{log.actor_email || 'System / CLI'}</div>
                      {log.actor_user_id && (
                        <div className="text-[10px] text-slate-400 font-mono">
                          ID: {log.actor_user_id.slice(0, 8)}...
                        </div>
                      )}
                    </td>

                    <td className="px-4 py-3.5 whitespace-nowrap">{renderActionBadge(log.action)}</td>

                    <td className="px-4 py-3.5 text-xs text-slate-700">
                      <span className="font-semibold uppercase text-[11px] text-slate-500 mr-1.5">
                        {log.target_type}:
                      </span>
                      <span className="font-mono text-slate-900">
                        {log.target_id ? log.target_id.slice(0, 16) : '—'}
                      </span>
                    </td>

                    <td className="px-4 py-3.5 text-xs font-mono text-slate-500 whitespace-nowrap">
                      {log.ip_address || '—'}
                    </td>

                    <td className="px-5 py-3.5 text-right whitespace-nowrap">
                      <button
                        onClick={() => setSelectedLog(log)}
                        className="inline-flex items-center px-2.5 py-1 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-md transition"
                      >
                        <Code className="w-3.5 h-3.5 mr-1 text-slate-500" />
                        Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination Footer */}
        <div className="px-5 py-3.5 bg-slate-50 border-t border-slate-200 flex items-center justify-between text-xs text-slate-600">
          <div>
            Page <strong className="font-semibold text-slate-900">{page}</strong> of{' '}
            <strong className="font-semibold text-slate-900">{totalPages || 1}</strong>
          </div>
          <div className="flex items-center space-x-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              className="p-1.5 rounded-lg border border-slate-300 bg-white hover:bg-slate-100 disabled:opacity-40 disabled:hover:bg-white transition"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
              className="p-1.5 rounded-lg border border-slate-300 bg-white hover:bg-slate-100 disabled:opacity-40 disabled:hover:bg-white transition"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Metadata Detail Modal */}
      {selectedLog && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-xl w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center space-x-2">
                <FileText className="w-5 h-5 text-emerald-600" />
                <h3 className="text-base font-bold text-slate-900">Audit Record Inspector</h3>
              </div>
              <button
                onClick={() => setSelectedLog(null)}
                className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-slate-500">Event ID:</span>{' '}
                <span className="font-mono text-slate-800">{selectedLog.id}</span>
              </div>
              <div>
                <span className="text-slate-500">Timestamp:</span>{' '}
                <span className="text-slate-800">{new Date(selectedLog.created_at).toLocaleString()}</span>
              </div>
              <div>
                <span className="text-slate-500">Action:</span>{' '}
                <span className="font-semibold text-slate-900">{selectedLog.action}</span>
              </div>
              <div>
                <span className="text-slate-500">Target:</span>{' '}
                <span className="font-mono text-slate-800">
                  {selectedLog.target_type} ({selectedLog.target_id || 'N/A'})
                </span>
              </div>
              <div>
                <span className="text-slate-500">Actor Email:</span>{' '}
                <span className="text-slate-800">{selectedLog.actor_email || 'CLI / Internal'}</span>
              </div>
              <div>
                <span className="text-slate-500">IP Address:</span>{' '}
                <span className="font-mono text-slate-800">{selectedLog.ip_address || 'Internal'}</span>
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1.5">
                Sanitized Event Metadata (JSON)
              </label>
              <pre className="bg-slate-950 text-slate-100 p-4 rounded-xl text-xs font-mono overflow-x-auto max-h-60 border border-slate-800">
                {JSON.stringify(selectedLog.metadata_json || {}, null, 2)}
              </pre>
            </div>

            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={() => setSelectedLog(null)}
                className="px-4 py-2 text-xs font-semibold text-slate-700 bg-slate-100 rounded-lg hover:bg-slate-200 transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
export default AdminAuditLogs;
