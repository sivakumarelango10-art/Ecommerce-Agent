import React, { useState, useEffect, useCallback } from 'react';
import {
  Search,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  UserCheck,
  UserX,
  Shield,
  Key,
  Zap,
  Activity,
  AlertTriangle,
  X,
  Eye,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Layers,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../api/client';
import type {
  AdminUserListItem,
  AdminUserDetailResponse,
  AdminUserListResponse,
} from '../../types/admin';

export const UserManagement: React.FC = () => {
  const { accessToken, currentUser } = useAuth();
  const [users, setUsers] = useState<AdminUserListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters & Pagination
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(10);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  // User Detail Modal / Drawer
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [userDetail, setUserDetail] = useState<AdminUserDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Action Modals
  const [statusModalUser, setStatusModalUser] = useState<AdminUserListItem | null>(null);
  const [statusActionReason, setStatusActionReason] = useState('');
  const [statusSubmitting, setStatusSubmitting] = useState(false);

  const [roleModalUser, setRoleModalUser] = useState<AdminUserListItem | null>(null);
  const [selectedRole, setSelectedRole] = useState<'customer' | 'admin' | 'super_admin'>('customer');
  const [roleActionReason, setRoleActionReason] = useState('');
  const [roleSubmitting, setRoleSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const isSuperAdmin = currentUser?.role === 'super_admin';

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 400);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchUsers = useCallback(async () => {
    if (!accessToken) return;
    try {
      setLoading(true);
      setError(null);
      const res: AdminUserListResponse = await api.admin.listUsers(accessToken, {
        page,
        page_size: pageSize,
        search: debouncedSearch.trim() || undefined,
        role: roleFilter || undefined,
        is_active: statusFilter === '' ? undefined : statusFilter === 'active',
      });
      setUsers(res.items);
      setTotalPages(res.total_pages);
      setTotalCount(res.total);
    } catch (err: any) {
      console.error('Failed to fetch admin users:', err);
      setError(err?.message || 'Failed to load user list.');
    } finally {
      setLoading(false);
    }
  }, [accessToken, page, pageSize, debouncedSearch, roleFilter, statusFilter]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const openUserDetail = async (userId: string) => {
    setSelectedUserId(userId);
    setDetailLoading(true);
    setDetailError(null);
    setUserDetail(null);
    try {
      if (!accessToken) return;
      const data = await api.admin.getUser(userId, accessToken);
      setUserDetail(data);
    } catch (err: any) {
      setDetailError(err?.message || 'Failed to fetch user details');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleStatusUpdate = async () => {
    if (!statusModalUser || !accessToken) return;
    try {
      setStatusSubmitting(true);
      setActionError(null);
      const newStatus = !statusModalUser.is_active;
      await api.admin.updateUserStatus(
        statusModalUser.id,
        {
          is_active: newStatus,
          reason: statusActionReason.trim() || undefined,
        },
        accessToken
      );
      setStatusModalUser(null);
      setStatusActionReason('');
      await fetchUsers();
      if (selectedUserId === statusModalUser.id) {
        await openUserDetail(statusModalUser.id);
      }
    } catch (err: any) {
      setActionError(err?.message || 'Failed to update user status.');
    } finally {
      setStatusSubmitting(false);
    }
  };

  const handleRoleUpdate = async () => {
    if (!roleModalUser || !accessToken) return;
    try {
      setRoleSubmitting(true);
      setActionError(null);
      await api.admin.updateUserRole(
        roleModalUser.id,
        {
          role: selectedRole,
          reason: roleActionReason.trim() || undefined,
        },
        accessToken
      );
      setRoleModalUser(null);
      setRoleActionReason('');
      await fetchUsers();
      if (selectedUserId === roleModalUser.id) {
        await openUserDetail(roleModalUser.id);
      }
    } catch (err: any) {
      setActionError(err?.message || 'Failed to update user role.');
    } finally {
      setRoleSubmitting(false);
    }
  };

  const renderRoleBadge = (role: string) => {
    switch (role) {
      case 'super_admin':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800 border border-purple-200">
            <Shield className="w-3 h-3 mr-1 text-purple-600" />
            Super Admin
          </span>
        );
      case 'admin':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
            <Shield className="w-3 h-3 mr-1 text-emerald-600" />
            Admin
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200">
            Customer
          </span>
        );
    }
  };

  const renderKeyStatusBadge = (status: string | null, enabled: boolean | null) => {
    if (!status || status === 'NOT_CONFIGURED') {
      return (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-600">
          None
        </span>
      );
    }
    if (enabled === false) {
      return (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-200 text-slate-700">
          Disabled
        </span>
      );
    }
    switch (status) {
      case 'ACTIVE':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-100 text-emerald-800">
            <CheckCircle2 className="w-3 h-3 mr-1 text-emerald-600" />
            Active
          </span>
        );
      case 'QUOTA_EXHAUSTED':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-800">
            <AlertTriangle className="w-3 h-3 mr-1 text-amber-600" />
            Quota
          </span>
        );
      case 'INVALID':
      case 'CORRUPTED':
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-rose-100 text-rose-800">
            <XCircle className="w-3 h-3 mr-1 text-rose-600" />
            {status}
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
            {status}
          </span>
        );
    }
  };

  return (
    <div className="space-y-6">
      {/* Header and Controls */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">User Management</h2>
          <p className="text-sm text-slate-500">
            Manage user accounts, roles, access statuses, and inspect individual AI usage.
          </p>
        </div>

        <button
          onClick={() => fetchUsers()}
          disabled={loading}
          className="inline-flex items-center px-3.5 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition shadow-sm disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Filter Bar */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          {/* Search */}
          <div className="relative md:col-span-2">
            <Search className="w-4 h-4 absolute left-3 top-3 text-slate-400" />
            <input
              type="text"
              placeholder="Search by email..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500"
            />
          </div>

          {/* Role Filter */}
          <div>
            <select
              value={roleFilter}
              onChange={(e) => {
                setRoleFilter(e.target.value);
                setPage(1);
              }}
              className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500"
            >
              <option value="">All Roles</option>
              <option value="customer">Customer</option>
              <option value="admin">Admin</option>
              <option value="super_admin">Super Admin</option>
            </select>
          </div>

          {/* Status Filter */}
          <div>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
              className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500"
            >
              <option value="">All Statuses</option>
              <option value="active">Active Only</option>
              <option value="inactive">Inactive Only</option>
            </select>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs text-slate-500 pt-1 border-t border-slate-100">
          <span>
            Showing <strong className="font-semibold text-slate-700">{users.length}</strong> of{' '}
            <strong className="font-semibold text-slate-700">{totalCount}</strong> registered users
          </span>
          {(search || roleFilter || statusFilter) && (
            <button
              onClick={() => {
                setSearch('');
                setRoleFilter('');
                setStatusFilter('');
                setPage(1);
              }}
              className="text-emerald-600 hover:text-emerald-700 font-medium"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Main Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-8 text-center space-y-4">
            <RefreshCw className="w-8 h-8 text-emerald-600 animate-spin mx-auto" />
            <p className="text-sm text-slate-500">Loading user records...</p>
          </div>
        ) : error ? (
          <div className="p-8 text-center space-y-3">
            <AlertTriangle className="w-8 h-8 text-rose-500 mx-auto" />
            <p className="text-sm font-medium text-rose-700">{error}</p>
            <button
              onClick={() => fetchUsers()}
              className="px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800"
            >
              Try Again
            </button>
          </div>
        ) : users.length === 0 ? (
          <div className="p-12 text-center space-y-2">
            <HelpCircle className="w-10 h-10 text-slate-300 mx-auto" />
            <p className="text-base font-semibold text-slate-800">No users found</p>
            <p className="text-sm text-slate-500">Try adjusting your search terms or filter selections.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold text-slate-600 uppercase tracking-wider">
                <tr>
                  <th className="px-5 py-3">User</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Personal Key</th>
                  <th className="px-4 py-3 text-right">Logical Req</th>
                  <th className="px-4 py-3 text-right">Attempts</th>
                  <th className="px-4 py-3 text-right">Tokens</th>
                  <th className="px-4 py-3">Last AI Activity</th>
                  <th className="px-5 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {users.map((u) => (
                  <tr key={u.id} className="hover:bg-slate-50/80 transition-colors">
                    {/* User */}
                    <td className="px-5 py-3.5">
                      <div className="font-medium text-slate-900">{u.email}</div>
                      <div className="text-xs text-slate-400">ID: {u.id.slice(0, 8)}...</div>
                    </td>

                    {/* Role */}
                    <td className="px-4 py-3.5 whitespace-nowrap">{renderRoleBadge(u.role)}</td>

                    {/* Status */}
                    <td className="px-4 py-3.5 whitespace-nowrap">
                      {u.is_active ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5"></span>
                          Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-rose-50 text-rose-700 border border-rose-200">
                          <span className="w-1.5 h-1.5 rounded-full bg-rose-500 mr-1.5"></span>
                          Inactive
                        </span>
                      )}
                    </td>

                    {/* Personal Key Status */}
                    <td className="px-4 py-3.5 whitespace-nowrap">
                      <div className="flex flex-col">
                        {renderKeyStatusBadge(u.gemini_key_status, u.gemini_key_enabled)}
                        {u.gemini_key_hint && (
                          <span className="text-[11px] text-slate-400 mt-0.5">...{u.gemini_key_hint}</span>
                        )}
                      </div>
                    </td>

                    {/* Requests */}
                    <td className="px-4 py-3.5 text-right font-medium text-slate-800">
                      {u.total_requests.toLocaleString()}
                    </td>

                    {/* Attempts */}
                    <td className="px-4 py-3.5 text-right text-xs text-slate-600">
                      <div>{u.total_attempts.toLocaleString()}</div>
                      <div className="text-[10px] text-slate-400">
                        C: {u.company_attempts} / P: {u.personal_attempts}
                      </div>
                    </td>

                    {/* Tokens */}
                    <td className="px-4 py-3.5 text-right font-mono text-xs text-slate-700">
                      {u.total_tokens ? u.total_tokens.toLocaleString() : '—'}
                    </td>

                    {/* Last AI Activity */}
                    <td className="px-4 py-3.5 text-xs text-slate-500 whitespace-nowrap">
                      {u.last_ai_activity ? new Date(u.last_ai_activity).toLocaleDateString() : 'Never'}
                    </td>

                    {/* Actions */}
                    <td className="px-5 py-3.5 text-right whitespace-nowrap space-x-2">
                      <button
                        onClick={() => openUserDetail(u.id)}
                        className="p-1.5 text-slate-600 hover:text-emerald-700 hover:bg-slate-100 rounded-lg transition"
                        title="View Full Profile & AI Usage"
                      >
                        <Eye className="w-4 h-4" />
                      </button>

                      <button
                        onClick={() => {
                          setStatusModalUser(u);
                          setStatusActionReason('');
                          setActionError(null);
                        }}
                        className={`p-1.5 rounded-lg transition ${
                          u.is_active
                            ? 'text-amber-600 hover:text-amber-700 hover:bg-amber-50'
                            : 'text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50'
                        }`}
                        title={u.is_active ? 'Deactivate User' : 'Activate User'}
                      >
                        {u.is_active ? <UserX className="w-4 h-4" /> : <UserCheck className="w-4 h-4" />}
                      </button>

                      {isSuperAdmin && (
                        <button
                          onClick={() => {
                            setRoleModalUser(u);
                            setSelectedRole(u.role as any);
                            setRoleActionReason('');
                            setActionError(null);
                          }}
                          className="p-1.5 text-purple-600 hover:text-purple-700 hover:bg-purple-50 rounded-lg transition"
                          title="Change Role (Super Admin)"
                        >
                          <Shield className="w-4 h-4" />
                        </button>
                      )}
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

      {/* User Detail Side Drawer / Modal */}
      {selectedUserId && (
        <div className="fixed inset-0 z-50 overflow-hidden bg-slate-900/40 backdrop-blur-sm flex justify-end">
          <div className="w-full max-w-2xl bg-white h-full shadow-2xl flex flex-col animate-in slide-in-from-right duration-200 overflow-y-auto">
            {/* Header */}
            <div className="p-6 border-b border-slate-200 flex items-center justify-between sticky top-0 bg-white z-10">
              <div>
                <h3 className="text-lg font-bold text-slate-900">User Details & Telemetry</h3>
                <p className="text-xs text-slate-500">ID: {selectedUserId}</p>
              </div>
              <button
                onClick={() => setSelectedUserId(null)}
                className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Content */}
            <div className="p-6 space-y-6 flex-1">
              {detailLoading ? (
                <div className="py-20 text-center">
                  <RefreshCw className="w-8 h-8 text-emerald-600 animate-spin mx-auto mb-2" />
                  <p className="text-sm text-slate-500">Loading user details...</p>
                </div>
              ) : detailError ? (
                <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 text-sm">
                  {detailError}
                </div>
              ) : userDetail ? (
                <>
                  {/* Account Overview Card */}
                  <div className="bg-slate-50 rounded-xl p-4 border border-slate-200 space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-base font-bold text-slate-900">{userDetail.email}</div>
                        <div className="text-xs text-slate-500">
                          Joined on {new Date(userDetail.created_at).toLocaleString()}
                        </div>
                      </div>
                      <div className="flex items-center space-x-2">
                        {renderRoleBadge(userDetail.role)}
                        {userDetail.is_active ? (
                          <span className="px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-800">
                            Active
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-xs font-semibold bg-rose-100 text-rose-800">
                            Inactive
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Personal Gemini API Key Card (Strictly Sanitized) */}
                  <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <Key className="w-4 h-4 text-emerald-600" />
                        <h4 className="text-sm font-semibold text-slate-900">Personal Gemini Configuration</h4>
                      </div>
                      {userDetail.gemini_settings ? (
                        renderKeyStatusBadge(
                          userDetail.gemini_settings.status,
                          userDetail.gemini_settings.is_enabled
                        )
                      ) : (
                        <span className="text-xs text-slate-400">Not configured</span>
                      )}
                    </div>

                    {userDetail.gemini_settings ? (
                      <div className="grid grid-cols-2 gap-3 text-xs">
                        <div>
                          <span className="text-slate-500">Key Hint:</span>{' '}
                          <span className="font-mono font-medium text-slate-800">
                            ...{userDetail.gemini_settings.key_hint}
                          </span>
                        </div>
                        <div>
                          <span className="text-slate-500">Enabled:</span>{' '}
                          <span className="font-medium text-slate-800">
                            {userDetail.gemini_settings.is_enabled ? 'Yes' : 'No'}
                          </span>
                        </div>
                        <div>
                          <span className="text-slate-500">Cooldown Until:</span>{' '}
                          <span className="font-medium text-slate-800">
                            {userDetail.gemini_settings.cooldown_until
                              ? new Date(userDetail.gemini_settings.cooldown_until).toLocaleString()
                              : 'None'}
                          </span>
                        </div>
                        <div>
                          <span className="text-slate-500">Last Used:</span>{' '}
                          <span className="font-medium text-slate-800">
                            {userDetail.gemini_settings.last_used_at
                              ? new Date(userDetail.gemini_settings.last_used_at).toLocaleString()
                              : 'Never'}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500">
                        This user relies entirely on StoreFlow company-managed AI pools.
                      </p>
                    )}
                  </div>

                  {/* AI Telemetry Summary */}
                  <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm space-y-3">
                    <div className="flex items-center space-x-2">
                      <Zap className="w-4 h-4 text-emerald-600" />
                      <h4 className="text-sm font-semibold text-slate-900">AI Consumption Metrics</h4>
                    </div>
                    <div className="grid grid-cols-3 gap-3 text-center">
                      <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                        <div className="text-xs text-slate-500">Logical Requests</div>
                        <div className="text-base font-bold text-slate-900 mt-1">
                          {userDetail.ai_summary.total_requests.toLocaleString()}
                        </div>
                      </div>
                      <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                        <div className="text-xs text-slate-500">Provider Attempts</div>
                        <div className="text-base font-bold text-slate-900 mt-1">
                          {userDetail.ai_summary.total_attempts.toLocaleString()}
                        </div>
                      </div>
                      <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                        <div className="text-xs text-slate-500">Total Tokens</div>
                        <div className="text-base font-bold text-slate-900 mt-1">
                          {userDetail.ai_summary.total_tokens
                            ? userDetail.ai_summary.total_tokens.toLocaleString()
                            : '0'}
                        </div>
                      </div>
                    </div>

                    <div className="pt-2 text-xs text-slate-600 flex justify-between border-t border-slate-100">
                      <span>Company Pool Attempts: <strong>{userDetail.source_breakdown.company_attempts}</strong></span>
                      <span>Personal Key Attempts: <strong>{userDetail.source_breakdown.personal_attempts}</strong></span>
                    </div>
                  </div>

                  {/* Model Breakdown */}
                  {userDetail.model_breakdown.length > 0 && (
                    <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm space-y-3">
                      <div className="flex items-center space-x-2">
                        <Layers className="w-4 h-4 text-emerald-600" />
                        <h4 className="text-sm font-semibold text-slate-900">Model Breakdown</h4>
                      </div>
                      <div className="divide-y divide-slate-100 text-xs">
                        {userDetail.model_breakdown.map((m) => (
                          <div key={m.model_name} className="py-2 flex items-center justify-between">
                            <span className="font-mono text-slate-800">{m.model_name}</span>
                            <div className="space-x-4 text-slate-500">
                              <span>Attempts: <strong className="text-slate-700">{m.attempts}</strong></span>
                              <span>Tokens: <strong className="text-slate-700">{m.tokens?.toLocaleString() || '0'}</strong></span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Recent Activity */}
                  <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm space-y-3">
                    <div className="flex items-center space-x-2">
                      <Activity className="w-4 h-4 text-emerald-600" />
                      <h4 className="text-sm font-semibold text-slate-900">Recent AI Executions</h4>
                    </div>
                    {userDetail.recent_activity.length === 0 ? (
                      <p className="text-xs text-slate-500">No recent AI activity recorded.</p>
                    ) : (
                      <div className="divide-y divide-slate-100 text-xs max-h-60 overflow-y-auto">
                        {userDetail.recent_activity.map((act, idx) => (
                          <div key={idx} className="py-2 flex items-center justify-between">
                            <div>
                              <div className="font-medium text-slate-800">{act.model_name} ({act.key_source})</div>
                              <div className="text-[10px] text-slate-400">
                                {new Date(act.created_at).toLocaleTimeString()} &bull; Latency: {act.latency_ms ?? '—'}ms
                              </div>
                            </div>
                            <div className="text-right">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                                  act.status === 'success'
                                    ? 'bg-emerald-100 text-emerald-800'
                                    : 'bg-rose-100 text-rose-800'
                                }`}
                              >
                                {act.status}
                              </span>
                              <div className="text-[10px] text-slate-500 mt-0.5">
                                {act.total_tokens ? `${act.total_tokens.toLocaleString()} tokens` : ''}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {/* Status Modal (Activate/Deactivate) */}
      {statusModalUser && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center space-x-3 text-slate-900">
              {statusModalUser.is_active ? (
                <div className="p-2 bg-amber-100 rounded-xl text-amber-600">
                  <UserX className="w-6 h-6" />
                </div>
              ) : (
                <div className="p-2 bg-emerald-100 rounded-xl text-emerald-600">
                  <UserCheck className="w-6 h-6" />
                </div>
              )}
              <div>
                <h3 className="text-base font-bold">
                  {statusModalUser.is_active ? 'Deactivate User Account' : 'Activate User Account'}
                </h3>
                <p className="text-xs text-slate-500">{statusModalUser.email}</p>
              </div>
            </div>

            <p className="text-sm text-slate-600">
              {statusModalUser.is_active
                ? 'Deactivating this user will revoke their access to authenticate, view orders, and use AI features immediately.'
                : 'Activating this account will restore full customer access to StoreFlow.'}
            </p>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Audit Reason (Optional)
              </label>
              <textarea
                value={statusActionReason}
                onChange={(e) => setStatusActionReason(e.target.value)}
                placeholder="Reason recorded in admin audit log..."
                rows={2}
                className="w-full text-xs p-2.5 border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none"
              />
            </div>

            {actionError && (
              <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-700">
                {actionError}
              </div>
            )}

            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={() => setStatusModalUser(null)}
                disabled={statusSubmitting}
                className="px-4 py-2 text-xs font-semibold text-slate-700 bg-slate-100 rounded-lg hover:bg-slate-200 transition"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleStatusUpdate}
                disabled={statusSubmitting}
                className={`px-4 py-2 text-xs font-semibold text-white rounded-lg transition ${
                  statusModalUser.is_active
                    ? 'bg-amber-600 hover:bg-amber-700'
                    : 'bg-emerald-600 hover:bg-emerald-700'
                }`}
              >
                {statusSubmitting ? 'Updating...' : statusModalUser.is_active ? 'Deactivate' : 'Activate'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Role Modal (Change Role) */}
      {roleModalUser && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-200 space-y-4">
            <div className="flex items-center space-x-3 text-slate-900">
              <div className="p-2 bg-purple-100 rounded-xl text-purple-600">
                <Shield className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-bold">Change User Role</h3>
                <p className="text-xs text-slate-500">{roleModalUser.email}</p>
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">Select Role</label>
              <select
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value as any)}
                className="w-full text-xs p-2.5 border border-slate-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:outline-none"
              >
                <option value="customer">Customer (Standard)</option>
                <option value="admin">Admin (Operational privileges)</option>
                <option value="super_admin">Super Admin (All privileges including role changes)</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Audit Reason (Optional)
              </label>
              <textarea
                value={roleActionReason}
                onChange={(e) => setRoleActionReason(e.target.value)}
                placeholder="Reason recorded in admin audit log..."
                rows={2}
                className="w-full text-xs p-2.5 border border-slate-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:outline-none"
              />
            </div>

            {actionError && (
              <div className="p-2.5 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-700">
                {actionError}
              </div>
            )}

            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={() => setRoleModalUser(null)}
                disabled={roleSubmitting}
                className="px-4 py-2 text-xs font-semibold text-slate-700 bg-slate-100 rounded-lg hover:bg-slate-200 transition"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleRoleUpdate}
                disabled={roleSubmitting}
                className="px-4 py-2 text-xs font-semibold text-white bg-purple-600 hover:bg-purple-700 rounded-lg transition"
              >
                {roleSubmitting ? 'Saving...' : 'Update Role'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
export default UserManagement;
