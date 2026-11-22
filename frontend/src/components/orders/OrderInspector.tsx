import React from 'react';
import {
  Package,
  Calendar,
  MapPin,
  X,
  RotateCw,
} from 'lucide-react';
import { OrderResponse, GuestOrderResponse } from '../../types/api';
import { Badge } from '../ui/Badge';
import { Card, CardHeader, CardBody } from '../ui/Card';
import { Button } from '../ui/Button';

interface OrderInspectorProps {
  order: OrderResponse | GuestOrderResponse | null;
  isLoading?: boolean;
  onClose?: () => void;
  onRefresh?: () => void;
  isCompact?: boolean;
}

export const OrderInspector: React.FC<OrderInspectorProps> = ({
  order,
  isLoading,
  onClose,
  onRefresh,
}) => {
  if (isLoading) {
    return (
      <Card className="h-full border-slate-200 flex flex-col justify-center items-center p-8 text-center bg-white">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-600 mb-3"></div>
        <p className="text-sm font-medium text-slate-700">Loading order details...</p>
      </Card>
    );
  }

  if (!order) {
    return (
      <Card className="h-full border-slate-200 flex flex-col justify-center items-center p-8 text-center bg-slate-50/50">
        <div className="w-12 h-12 rounded-2xl bg-slate-100 flex items-center justify-center text-slate-400 mb-3">
          <Package className="w-6 h-6" />
        </div>
        <h4 className="text-sm font-semibold text-slate-800 mb-1">No Order Selected</h4>
        <p className="text-xs text-slate-500 max-w-xs leading-relaxed">
          Ask the AI assistant about your orders or select an order to inspect items, shipment status, and billing details.
        </p>
      </Card>
    );
  }

  const formatCurrency = (amt: number, curr = 'USD') => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: curr,
    }).format(amt);
  };

  const formatDate = (isoString: string) => {
    try {
      return new Date(isoString).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  return (
    <Card className="h-full flex flex-col border-slate-200/90 shadow-sm bg-white">
      {/* Header */}
      <CardHeader className="flex flex-row items-center justify-between pb-3 bg-slate-50/50">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Order</span>
            <span className="text-sm font-bold font-mono text-slate-900">{order.order_number}</span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <Badge variant={order.status}>{order.status}</Badge>
          </div>
        </div>

        <div className="flex items-center gap-1">
          {onRefresh && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onRefresh}
              title="Refresh order data"
              className="p-1.5 h-8 w-8 text-slate-500 hover:text-slate-900"
            >
              <RotateCw className="w-4 h-4" />
            </Button>
          )}
          {onClose && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              title="Close panel"
              className="p-1.5 h-8 w-8 text-slate-500 hover:text-slate-900"
            >
              <X className="w-4 h-4" />
            </Button>
          )}
        </div>
      </CardHeader>

      <CardBody className="flex-1 overflow-y-auto space-y-5 p-5">
        {/* Date and Summary Meta */}
        <div className="flex items-center gap-2 text-xs text-slate-500 bg-slate-50 p-2.5 rounded-lg border border-slate-100">
          <Calendar className="w-3.5 h-3.5 text-slate-400" />
          <span>Placed on {formatDate(order.created_at)}</span>
        </div>

        {/* Order Items */}
        <div>
          <h5 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
            <Package className="w-3.5 h-3.5 text-slate-500" />
            <span>Items Ordered ({order.items.length})</span>
          </h5>

          <div className="space-y-2">
            {order.items.map((item, idx) => (
              <div
                key={('id' in item && item.id) ? item.id : `${item.product_sku}-${idx}`}
                className="flex items-center justify-between p-2.5 rounded-lg border border-slate-100 bg-white hover:border-slate-200 transition-colors"
              >
                <div className="flex-1 pr-3">
                  <p className="text-xs font-semibold text-slate-900 line-clamp-1">{item.product_name}</p>
                  <p className="text-[11px] text-slate-500 mt-0.5">
                    Qty: {item.quantity} × {formatCurrency(item.unit_price, order.currency)}
                  </p>
                </div>
                <span className="text-xs font-bold font-mono text-slate-900">
                  {formatCurrency(item.line_total, order.currency)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Financial Breakdown */}
        <div className="p-3 bg-slate-50/80 rounded-xl border border-slate-100 text-xs space-y-1.5">
          <div className="flex justify-between text-slate-600">
            <span>Subtotal</span>
            <span className="font-mono">{formatCurrency(order.subtotal, order.currency)}</span>
          </div>
          <div className="flex justify-between text-slate-600">
            <span>Shipping Fee</span>
            <span className="font-mono">
              {order.shipping_fee === 0 ? 'Free' : formatCurrency(order.shipping_fee, order.currency)}
            </span>
          </div>
          <div className="flex justify-between text-slate-600">
            <span>Estimated Tax</span>
            <span className="font-mono">{formatCurrency(order.tax, order.currency)}</span>
          </div>
          <div className="pt-2 border-t border-slate-200 flex justify-between font-bold text-slate-900 text-sm">
            <span>Total</span>
            <span className="font-mono text-emerald-700">{formatCurrency(order.total_amount, order.currency)}</span>
          </div>
        </div>

        {/* Shipping Address Snapshot */}
        {order.shipping_snapshot && (
          <div>
            <h5 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <MapPin className="w-3.5 h-3.5 text-slate-500" />
              <span>Delivery Address</span>
            </h5>
            <div className="text-xs text-slate-600 bg-white border border-slate-100 rounded-lg p-3 space-y-0.5">
              <p className="font-semibold text-slate-900">{order.shipping_snapshot.recipient_name}</p>
              <p>{order.shipping_snapshot.line1}</p>
              {order.shipping_snapshot.line2 && <p>{order.shipping_snapshot.line2}</p>}
              <p>
                {order.shipping_snapshot.city}
                {order.shipping_snapshot.region ? `, ${order.shipping_snapshot.region}` : ''}{' '}
                {order.shipping_snapshot.postal_code}
              </p>
              <p className="text-slate-500">{order.shipping_snapshot.country_code}</p>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
};
