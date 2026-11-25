import React from 'react';
import {
  User,
  ShieldCheck,
  Shield,
  LogOut,
  LogIn,
  CheckCircle2,
  Sparkles,
  Settings as SettingsIcon,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { Button } from '../ui/Button';

interface NavbarProps {
  onOpenAuth: () => void;
  onOpenGuestPortal: () => void;
  onOpenSettings: () => void;
  onOpenAdmin?: () => void;
  activeView: 'chat' | 'orders' | 'admin';
  setActiveView: (view: 'chat' | 'orders' | 'admin') => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  onOpenAuth,
  onOpenGuestPortal,
  onOpenSettings,
  onOpenAdmin,
  activeView,
  setActiveView,
}) => {
  const { isAuthenticated, currentUser, logout } = useAuth();
  const isAdminUser = currentUser?.role === 'admin' || currentUser?.role === 'super_admin';

  const handleAdminClick = () => {
    if (onOpenAdmin) {
      onOpenAdmin();
    } else {
      setActiveView('admin');
    }
  };

  return (
    <header className="sticky top-0 z-30 bg-white/95 backdrop-blur border-b border-slate-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Brand / Logo */}
        <div className="flex items-center gap-4 sm:gap-6">
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => setActiveView('chat')}
              className="flex items-center focus:outline-none focus:ring-2 focus:ring-emerald-500 rounded-lg p-0.5 transition-transform hover:opacity-95"
              aria-label="StoreFlow Home"
            >
              <img
                src="/storeflow-logo.svg"
                alt="StoreFlow - Order Support & Shopping Agent"
                className="h-8 sm:h-9 md:h-10 w-auto object-contain"
              />
            </button>
            <span className="hidden lg:inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
              <CheckCircle2 className="w-2.5 h-2.5" /> AI Supervisor
            </span>
          </div>

          {/* Navigation Tabs */}
          <nav className="hidden md:flex items-center gap-1 pl-4 border-l border-slate-200">
            <button
              onClick={() => setActiveView('chat')}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                activeView === 'chat'
                  ? 'bg-slate-100 text-slate-900'
                  : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
              }`}
            >
              Support Chat
            </button>
            {isAuthenticated && (
              <>
                <button
                  onClick={() => setActiveView('orders')}
                  className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                    activeView === 'orders'
                      ? 'bg-slate-100 text-slate-900'
                      : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
                  }`}
                >
                  My Orders
                </button>
                <button
                  onClick={onOpenSettings}
                  className="px-3 py-1.5 text-xs font-semibold rounded-md transition-colors text-slate-600 hover:text-slate-900 hover:bg-slate-50 flex items-center gap-1.5"
                >
                  <Sparkles className="w-3.5 h-3.5 text-emerald-600" />
                  <span>AI Settings</span>
                </button>
                {isAdminUser && (
                  <button
                    onClick={handleAdminClick}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors flex items-center gap-1.5 ${
                      activeView === 'admin'
                        ? 'bg-purple-100 text-purple-900'
                        : 'text-purple-700 hover:text-purple-900 hover:bg-purple-50'
                    }`}
                  >
                    <Shield className="w-3.5 h-3.5 text-purple-600" />
                    <span>Admin Dashboard</span>
                  </button>
                )}
              </>
            )}
          </nav>
        </div>

        {/* Right Actions */}
        <div className="flex items-center gap-2.5">
          {/* Admin Dashboard Quick Button for Admins */}
          {isAuthenticated && isAdminUser && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleAdminClick}
              className="text-purple-700 hover:text-purple-900 border-purple-200 hover:bg-purple-50 gap-1.5 inline-flex"
              aria-label="Open Admin Dashboard"
            >
              <Shield className="w-3.5 h-3.5 text-purple-600" />
              <span className="hidden sm:inline">Admin</span>
            </Button>
          )}

          {/* Guest Order Lookup Button */}
          <Button
            variant="outline"
            size="sm"
            onClick={onOpenGuestPortal}
            className="text-slate-700 hover:text-slate-900 border-slate-200 gap-1.5 hidden sm:inline-flex"
          >
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
            <span>Guest Order Lookup</span>
          </Button>

          {/* User / Auth State */}
          {isAuthenticated ? (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={onOpenSettings}
                title="AI Settings"
                className="text-slate-700 hover:text-slate-900 border-slate-200 gap-1.5"
                aria-label="Open AI Settings"
              >
                <SettingsIcon className="w-3.5 h-3.5 text-slate-500" />
                <span className="hidden md:inline">AI Settings</span>
              </Button>
              <div className="hidden sm:flex items-center gap-2 px-3 py-1 bg-slate-100 rounded-lg text-xs font-medium text-slate-700">
                <User className="w-3.5 h-3.5 text-slate-500" />
                <span className="truncate max-w-[140px]">{currentUser?.email}</span>
                {currentUser?.role && currentUser.role !== 'customer' && (
                  <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-purple-100 text-purple-800">
                    {currentUser.role}
                  </span>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={logout}
                title="Sign Out"
                className="text-slate-600 hover:text-rose-600 hover:bg-rose-50"
              >
                <LogOut className="w-4 h-4" />
                <span className="hidden sm:inline">Sign Out</span>
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={onOpenAuth}
                className="gap-1.5 bg-slate-900 hover:bg-slate-800 text-white font-medium shadow-sm"
              >
                <LogIn className="w-3.5 h-3.5" />
                <span>Customer Sign In</span>
              </Button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
