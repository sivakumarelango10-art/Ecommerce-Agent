import React, { useEffect, useState } from 'react';
import { Package, Calendar, ChevronRight, RotateCw } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api, ApiError } from '../../api/client';
import { OrderSummaryResponse, OrderResponse } from '../../types/api';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Alert } from '../ui/Alert';

interface OrderListViewProps {
  onSelectOrder: (order: OrderResponse) => void;
}

export const OrderListView: React.FC<OrderListViewProps> = ({ onSelectOrder }) => {
  const { accessToken } = useAuth();
  const [orders, setOrders] = useState<OrderSummaryResponse[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const fetchOrders = async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.orders.list(accessToken);
      setOrders(data);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError('Failed to fetch your orders.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, [accessToken]);

  const handleOrderClick = async (orderSummary: OrderSummaryResponse) => {
    if (!accessToken) return;
    setSelectedId(orderSummary.id);
    try {
      const fullOrder = await api.orders.get(orderSummary.id, accessToken);
      onSelectOrder(fullOrder);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError('Failed to load order details.');
      }
    }
  };

  const formatDate = (isoString: string) => {
    try {
      return new Date(isoString).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
    } catch {
      return isoString;
    }
  };

  const formatCurrency = (amt: number, curr = 'USD') => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: curr,
    }).format(amt);
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200/90 shadow-sm p-5 space-y-4">
      <div className="flex items-center justify-between pb-3 border-b border-slate-100">
        <div>
          <h3 className="text-base font-bold text-slate-900">Your StoreFlow Order History</h3>
          <p className="text-xs text-slate-500">Track and review past customer purchases</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={fetchOrders}
          isLoading={isLoading}
          className="h-8 gap-1.5 text-xs text-slate-600 border-slate-200"
        >
          <RotateCw className="w-3.5 h-3.5" />
          <span>Refresh</span>
        </Button>
      </div>

      {error && (
        <Alert variant="error" title="Orders Error">
          {error}
        </Alert>
      )}

      {isLoading && orders.length === 0 ? (
        <div className="py-12 text-center">
          <div className="animate-spin rounded-full h-7 w-7 border-b-2 border-emerald-600 mx-auto mb-2"></div>
          <p className="text-xs text-slate-500">Fetching order history...</p>
        </div>
      ) : orders.length === 0 ? (
        <div className="py-12 text-center bg-slate-50/50 rounded-xl border border-dashed border-slate-200">
          <Package className="w-8 h-8 text-slate-400 mx-auto mb-2" />
          <h4 className="text-sm font-semibold text-slate-800">No Orders Placed Yet</h4>
          <p className="text-xs text-slate-500 mt-1 max-w-xs mx-auto">
            Once you place an order with StoreFlow, it will appear here with full shipment and item tracking.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {orders.map((ord) => {
            const isSelected = selectedId === ord.id;
            return (
              <div
                key={ord.id}
                onClick={() => handleOrderClick(ord)}
                className={`flex items-center justify-between p-3.5 rounded-xl border cursor-pointer transition-all ${
                  isSelected
                    ? 'border-emerald-500 bg-emerald-50/40 shadow-sm ring-1 ring-emerald-400'
                    : 'border-slate-200/70 hover:border-slate-300 hover:bg-slate-50/70'
                }`}
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-slate-900">{ord.order_number}</span>
                    <Badge variant={ord.status}>{ord.status}</Badge>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-slate-500">
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3 text-slate-400" />
                      {formatDate(ord.created_at)}
                    </span>
                    <span>•</span>
                    <span className="font-semibold text-slate-700">
                      {formatCurrency(ord.total_amount, ord.currency)}
                    </span>
                  </div>
                </div>

                <div className="flex items-center text-slate-400 hover:text-slate-700">
                  <span className="text-xs font-medium mr-1 hidden sm:inline">Inspect</span>
                  <ChevronRight className="w-4 h-4" />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
