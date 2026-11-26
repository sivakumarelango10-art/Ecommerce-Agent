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
  const { accessToken } = useAuth();
  const [activeView, setActiveView] = useState<'chat' | 'orders' | 'admin'>(() => {
    if (typeof window !== 'undefined') {
      if (window.location.pathname.startsWith('/admin') || window.location.hash.startsWith('#admin')) {
        return 'admin';
      }
    }
    return 'chat';
  });

  const [selectedOrder, setSelectedOrder] = useState<OrderResponse | GuestOrderResponse | null>(null);
  const [isInspectorLoading, setIsInspectorLoading] = useState(false);
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [isGuestOpen, setIsGuestOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Inspector order fetching when clicked in chat or order list
  const handleInspectOrderNumber = async (orderNumber: string) => {
    if (!accessToken) {
      setIsAuthOpen(true);
      return;
    }
    setIsInspectorLoading(true);
    try {
      // First list orders to find matching order ID
      const orders = await api.orders.list(accessToken);
      const matched = orders.find((o) => o.order_number === orderNumber);
      if (matched) {
        const fullOrder = await api.orders.get(matched.id, accessToken);
        setSelectedOrder(fullOrder);
      } else {
        // Fallback: check if the order exists via standard list
        alert(`Order ${orderNumber} not found under your customer account.`);
      }
    } catch (err) {
      console.error('Error fetching order for inspection:', err);
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
        onOpenAuth={() => setIsAuthOpen(true)}
        onOpenGuestPortal={() => setIsGuestOpen(true)}
        onOpenSettings={() => setIsSettingsOpen(true)}
        onOpenAdmin={() => setActiveView('admin')}
        activeView={activeView}
        setActiveView={setActiveView}
      />

      {/* Main View Switching */}
      {activeView === 'admin' ? (
        <div className="flex-1">
          <AdminDashboard
            onBackToStore={() => setActiveView('chat')}
            onOpenLogin={() => setIsAuthOpen(true)}
          />
        </div>
      ) : (
        <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 lg:p-8">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-[calc(100vh-8rem)]">
            {/* Left Column: Chat or Order List (~60% on desktop) */}
            <div className="lg:col-span-7 h-full flex flex-col">
              {activeView === 'chat' ? (
                <ChatContainer
                  onInspectOrderNumber={handleInspectOrderNumber}
                  onOpenAuth={() => setIsAuthOpen(true)}
                  onOpenGuestPortal={() => setIsGuestOpen(true)}
                />
              ) : (
                <div className="h-full overflow-y-auto">
                  <OrderListView onSelectOrder={(order) => setSelectedOrder(order)} />
                </div>
              )}
            </div>

            {/* Right Column: Order Inspector (~40% on desktop) */}
            <div className="lg:col-span-5 h-full">
              <OrderInspector
                order={selectedOrder}
                isLoading={isInspectorLoading}
                onClose={() => setSelectedOrder(null)}
                onRefresh={handleRefreshOrder}
              />
            </div>
          </div>
        </main>
      )}

      {/* Global Modals */}
      <AuthModal isOpen={isAuthOpen} onClose={() => setIsAuthOpen(false)} />
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
