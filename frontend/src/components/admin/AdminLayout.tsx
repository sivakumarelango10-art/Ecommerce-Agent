import React, { useState } from 'react';
import {
  LayoutDashboard,
  Users,
  BarChart3,
  Server,
  Key,
  Sliders,
  ShieldCheck,
  FileText,
  LogOut,
  Store,
  Menu,
  X,
  ChevronLeft,
  ChevronRight,
  Shield,
  Sparkles,
  Compass,
  Activity,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

export type AdminTab =
  | 'overview'
  | 'users'
  | 'analytics'
  | 'providers'
  | 'api-keys'
  | 'models'
  | 'routing'
  | 'provider-activity'
  | 'admins'
  | 'audit-logs';

export interface AdminLayoutProps {
  currentTab?: AdminTab;
  activeTab?: string;
  onSelectTab?: (tab: AdminTab) => void;
  onTabChange?: (tab: string) => void;
  onReturnToStore?: () => void;
  onBackToStore?: () => void;
  children: React.ReactNode;
}

export const AdminLayout: React.FC<AdminLayoutProps> = ({
  currentTab,
  activeTab,
  onSelectTab,
  onTabChange,
  onReturnToStore,
  onBackToStore,
  children,
}) => {
  const { currentUser, logout } = useAuth();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const effectiveTab: AdminTab = (currentTab || (activeTab as AdminTab) || 'overview') as AdminTab;
  const role = currentUser?.role || 'admin';
  const isSuperAdmin = role === 'super_admin';

  const handleTabClick = (tabId: AdminTab) => {
    if (onSelectTab) onSelectTab(tabId);
    if (onTabChange) onTabChange(tabId);
    setIsMobileMenuOpen(false);
  };

  const handleReturn = onReturnToStore || onBackToStore || (() => {
    if (typeof window !== 'undefined') window.location.href = '/';
  });

  const navItems: Array<{
    id: AdminTab;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    badge?: string;
    superAdminOnly?: boolean;
  }> = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'analytics', label: 'AI Overview', icon: BarChart3 },
    { id: 'providers', label: 'Provider Catalogue', icon: Server },
    { id: 'api-keys', label: 'Company API Key Vault', icon: Key },
    { id: 'models', label: 'Model Registry', icon: Sliders },
    { id: 'routing', label: 'Routing Preferences', icon: Compass, badge: 'Preview' },
    { id: 'provider-activity', label: 'Provider Activity', icon: Activity },
    { id: 'users', label: 'User Accounts', icon: Users },
    { id: 'admins', label: 'Admin Management', icon: ShieldCheck, superAdminOnly: true },
    { id: 'audit-logs', label: 'Audit Logs', icon: FileText },
  ];

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col antialiased text-slate-800">
      {/* Top Header */}
      <header className="sticky top-0 z-40 bg-white/95 backdrop-blur border-b border-slate-200 px-4 sm:px-6 h-16 flex items-center justify-between shadow-xs">
        <div className="flex items-center gap-3">
          {/* Mobile hamburger */}
          <button
            type="button"
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            className="md:hidden p-2 rounded-lg text-slate-600 hover:text-slate-900 hover:bg-slate-100 focus:outline-none"
            aria-label="Toggle Navigation Drawer"
          >
            {isMobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>

          {/* Logo & Brand */}
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-slate-900 flex items-center justify-center text-emerald-400 font-bold shadow-xs">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <span className="text-base font-bold text-slate-900 tracking-tight leading-none block">StoreFlow</span>
              <span className="text-[10px] font-semibold text-emerald-700 uppercase tracking-wider block">Admin Console</span>
            </div>
          </div>
        </div>

        {/* Right Header: Administrator Details, Return to Store, Logout */}
        <div className="flex items-center gap-3">
          {/* Admin Identity Badge */}
          <div className="hidden sm:flex items-center gap-2 bg-slate-100/80 border border-slate-200/80 px-3 py-1.5 rounded-lg text-xs">
            <Shield className="w-3.5 h-3.5 text-emerald-600" />
            <span className="font-medium text-slate-700 truncate max-w-[180px]">{currentUser?.email || 'Administrator'}</span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide uppercase ${
                isSuperAdmin
                  ? 'bg-emerald-100 text-emerald-800 border border-emerald-300'
                  : 'bg-blue-100 text-blue-800 border border-blue-300'
              }`}
            >
              {role.replace('_', ' ')}
            </span>
          </div>

          {/* Return to Customer App */}
          <button
            type="button"
            onClick={handleReturn}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-700 hover:text-slate-900 bg-white border border-slate-200 hover:bg-slate-50 transition-colors shadow-xs"
            title="Return to StoreFlow customer interface"
          >
            <Store className="w-3.5 h-3.5 text-slate-500" />
            <span className="hidden sm:inline">Back to Store</span>
          </button>

          {/* Sign Out */}
          <button
            type="button"
            onClick={logout}
            className="p-2 text-slate-500 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors"
            title="Sign out of Administrator Session"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </header>

      {/* Main Body with Sidebar + Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Desktop Collapsible Sidebar */}
        <aside
          className={`hidden md:flex flex-col border-r border-slate-200 bg-white transition-all duration-200 ease-in-out ${
            isSidebarCollapsed ? 'w-16' : 'w-64'
          }`}
        >
          {/* Navigation Items */}
          <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
            {navItems.map((item) => {
              if (item.superAdminOnly && !isSuperAdmin) return null;
              const Icon = item.icon;
              const isActive = effectiveTab === item.id;

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => handleTabClick(item.id)}
                  title={isSidebarCollapsed ? item.label : undefined}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold transition-all ${
                    isActive
                      ? 'bg-emerald-50 text-emerald-800 shadow-2xs font-bold border border-emerald-100'
                      : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
                  } ${isSidebarCollapsed ? 'justify-center px-0' : ''}`}
                >
                  <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-emerald-700' : 'text-slate-500'}`} />
                  {!isSidebarCollapsed && (
                    <span className="flex-1 text-left truncate flex items-center justify-between">
                      <span>{item.label}</span>
                      {item.badge && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-500">
                          {item.badge}
                        </span>
                      )}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>

          {/* Sidebar Footer with Collapse Toggle */}
          <div className="p-3 border-t border-slate-100 flex items-center justify-between">
            {!isSidebarCollapsed && (
              <span className="text-[11px] font-medium text-slate-400 px-2">StoreFlow v1.0 Admin</span>
            )}
            <button
              type="button"
              onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors mx-auto"
              title={isSidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {isSidebarCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
            </button>
          </div>
        </aside>

        {/* Mobile Navigation Drawer */}
        {isMobileMenuOpen && (
          <div className="fixed inset-0 z-50 md:hidden bg-slate-900/50 backdrop-blur-xs flex">
            <div className="w-64 bg-white h-full shadow-xl flex flex-col p-4 animate-in slide-in-from-left duration-200">
              <div className="flex items-center justify-between pb-4 border-b border-slate-100">
                <span className="font-bold text-slate-900 text-sm">Navigation</span>
                <button
                  type="button"
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <nav className="flex-1 py-4 space-y-1 overflow-y-auto">
                {navItems.map((item) => {
                  if (item.superAdminOnly && !isSuperAdmin) return null;
                  const Icon = item.icon;
                  const isActive = effectiveTab === item.id;

                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => handleTabClick(item.id)}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold transition-colors ${
                        isActive
                          ? 'bg-emerald-50 text-emerald-800 border border-emerald-100'
                          : 'text-slate-600 hover:bg-slate-50'
                      }`}
                    >
                      <Icon className={`w-4 h-4 ${isActive ? 'text-emerald-700' : 'text-slate-500'}`} />
                      <span>{item.label}</span>
                    </button>
                  );
                })}
              </nav>

              <div className="pt-4 border-t border-slate-100">
                <button
                  type="button"
                  onClick={handleReturn}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-lg transition"
                >
                  <Store className="w-4 h-4" />
                  <span>Return to Store</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Main Content Area */}
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          <div className="max-w-7xl mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
};

export default AdminLayout;
