import React, { useState } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { GuestOrderProvider } from './context/GuestOrderContext';
import { Navbar } from './components/layout/Navbar';
import { ChatContainer } from './components/chat/ChatContainer';
import { OrderInspector } from './components/orders/OrderInspector';
import { OrderListView } from './components/orders/OrderListView';
import { AuthModal } from './components/auth/AuthModal';
import { GuestOrderPortal } from './components/guest/GuestOrderPortal';
import { SettingsModal } from './components/settings/SettingsModal';
import { AdminDashboard } from './components/admin/AdminDashboard';
import { OrderResponse, GuestOrderResponse } from './types/api';
import { api } from './api/client';

const MainApp: React.FC = () => {
  const {
    accessToken,
    isHydrating = false,
    sessionError = null,
    clearSessionError,
    retrySessionVerification,
  } = useAuth();
  const [activeView, setActiveView] = useState<'chat' | 'orders' | 'admin'>(() => {
    if (typeof window !== 'undefined') {
      if (window.location.pathname.startsWith('/admin') || window.location.hash.startsWith('#admin')) {
        return 'admin';
      }
      const params = new URLSearchParams(window.location.search);
      if (params.get('view') === 'orders') {
        return 'orders';
      }
    }
    return 'chat';
  });

  const handleSetView = (view: 'chat' | 'orders' | 'admin') => {
    setActiveView(view);
    if (typeof window !== 'undefined' && window.history?.replaceState) {
      try {
        const nextUrl =
          view === 'admin'
            ? '/admin'
            : view === 'orders'
            ? '/?view=orders'
            : '/';
        window.history.replaceState(null, '', nextUrl);
      } catch {
        // Ignore history restrictions in sandboxed iframes
      }
    }
  };

  const [selectedOrder, setSelectedOrder] = useState<OrderResponse | GuestOrderResponse | null>(null);
  const [isInspectorLoading, setIsInspectorLoading] = useState(false);
  const [inspectorError, setInspectorError] = useState<string | null>(null);
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [authPendingQuestion, setAuthPendingQuestion] = useState<string | null>(null);
  const [authPromptNotice, setAuthPromptNotice] = useState<string | null>(null);
  const [authCancelSignal, setAuthCancelSignal] = useState(0);
  const [isGuestOpen, setIsGuestOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const openAuthModal = (pendingQuestion?: string, reason?: string) => {
    setAuthPendingQuestion(pendingQuestion ?? null);
    setAuthPromptNotice(reason ?? null);
    setIsAuthOpen(true);
  };

  const handleCloseAuthModal = () => {
    setIsAuthOpen(false);
    setAuthPendingQuestion(null);
    setAuthPromptNotice(null);
    setAuthCancelSignal((n) => n + 1);
  };

  const handleAuthSuccess = () => {
    setIsAuthOpen(false);
    setAuthPendingQuestion(null);
    setAuthPromptNotice(null);
  };

  // Inspector order fetching when clicked in chat or order list
  const handleInspectOrderNumber = async (orderNumber: string) => {
    if (!accessToken) {
      openAuthModal(undefined, `Sign in to inspect order ${orderNumber}`);
      return;
    }
    setIsInspectorLoading(true);
    setInspectorError(null);
    try {
      // First list orders to find matching order ID
      const orders = await api.orders.list(accessToken);
      const matched = orders.find((o) => o.order_number === orderNumber);
      if (matched) {
        const fullOrder = await api.orders.get(matched.id, accessToken);
        setSelectedOrder(fullOrder);
      } else {
        setInspectorError(`Order ${orderNumber} was not found under your signed-in account.`);
      }
    } catch (err) {
      console.error('Error fetching order for inspection:', err);
      setInspectorError('Unable to load order details right now. Please try again.');
    } finally {
      setIsInspectorLoading(false);
    }
  };

  const handleRefreshOrder = async () => {
    if (!selectedOrder || !accessToken || !('id' in selectedOrder) || !selectedOrder.id) return;
    setIsInspectorLoading(true);
    try {
      const refreshed = await api.orders.get(selectedOrder.id, accessToken);
      setSelectedOrder(refreshed);
    } catch (err) {
      console.error('Failed to refresh order:', err);
    } finally {
      setIsInspectorLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col text-slate-900">
      <Navbar
        onOpenAuth={() => openAuthModal()}
        onOpenGuestPortal={() => setIsGuestOpen(true)}
        onOpenSettings={() => setIsSettingsOpen(true)}
        onOpenAdmin={() => handleSetView('admin')}
        activeView={activeView}
        setActiveView={handleSetView}
      />

      {!isHydrating && sessionError && activeView !== 'admin' && (
        <div
          role="status"
          className="bg-amber-50 border-b border-amber-200 px-4 py-2.5 text-xs text-amber-900"
        >
          <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-2">
            <span>{sessionError}</span>
            <div className="flex items-center gap-2">
              {retrySessionVerification && (
                <button
                  type="button"
                  onClick={() => void retrySessionVerification()}
                  className="px-2.5 py-1 rounded bg-amber-600 text-white font-semibold hover:bg-amber-700"
                >
                  Retry
                </button>
              )}
              <button
                type="button"
                onClick={() => setIsAuthOpen(true)}
                className="px-2.5 py-1 rounded border border-amber-300 bg-white text-amber-900 font-semibold hover:bg-amber-100"
              >
                Sign In
              </button>
              {clearSessionError && (
                <button
                  type="button"
                  onClick={clearSessionError}
                  className="px-2 py-1 text-amber-700 hover:text-amber-900 underline"
                >
                  Dismiss
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Main View Switching */}
      {activeView === 'admin' ? (
        <div className="flex-1">
          <AdminDashboard
            onBackToStore={() => handleSetView('chat')}
            onOpenLogin={() => openAuthModal(undefined, 'Administrator sign-in required')}
          />
        </div>
      ) : (
        <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 lg:p-8">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 lg:gap-6 lg:h-[calc(100vh-8.5rem)] lg:min-h-[540px]">
            {/* Left Column: Chat or Order List (~58% on desktop) */}
            <div className="lg:col-span-7 flex flex-col min-h-0 lg:h-full">
              {activeView === 'chat' ? (
                <ChatContainer
                  onInspectOrderNumber={handleInspectOrderNumber}
                  onOpenAuth={openAuthModal}
                  onOpenGuestPortal={() => setIsGuestOpen(true)}
                  authCancelSignal={authCancelSignal}
                />
              ) : (
                <div className="lg:h-full lg:overflow-y-auto">
                  <OrderListView onSelectOrder={(order) => setSelectedOrder(order)} />
                </div>
              )}
            </div>

            {/* Right Column: Order Inspector (~42% on desktop) */}
            <div className="lg:col-span-5 flex flex-col gap-3 min-h-0 lg:h-full">
              {inspectorError && (
                <div role="alert" className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900 flex items-center justify-between gap-2">
                  <span>{inspectorError}</span>
                  <button
                    type="button"
                    onClick={() => setInspectorError(null)}
                    className="font-semibold text-amber-800 hover:text-amber-950"
                    aria-label="Dismiss order notice"
                  >
                    Dismiss
                  </button>
                </div>
              )}
              <div className="flex-1 min-h-0">
                <OrderInspector
                  order={selectedOrder}
                  isLoading={isInspectorLoading}
                  onClose={() => setSelectedOrder(null)}
                  onRefresh={handleRefreshOrder}
                />
              </div>
            </div>
          </div>
        </main>
      )}

      {/* Global Modals */}
      <AuthModal
        isOpen={isAuthOpen}
        onClose={handleCloseAuthModal}
        onAuthSuccess={handleAuthSuccess}
        pendingQuestion={authPendingQuestion}
        promptNotice={authPromptNotice}
      />
      <GuestOrderPortal isOpen={isGuestOpen} onClose={() => setIsGuestOpen(false)} />
      <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <GuestOrderProvider>
        <MainApp />
      </GuestOrderProvider>
    </AuthProvider>
  );
};

export default App;
