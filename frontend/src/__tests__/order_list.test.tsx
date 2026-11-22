import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OrderListView } from '../components/orders/OrderListView';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { api } from '../api/client';
import { OrderSummaryResponse, OrderResponse } from '../types/api';

describe('OrderListView Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mockSummaries: OrderSummaryResponse[] = [
    {
      id: 'order-uuid-001',
      order_number: 'ORD-20261122-001',
      status: 'confirmed',
      currency: 'USD',
      total_amount: 150.0,
      created_at: '2026-11-22T08:00:00Z',
    },
    {
      id: 'order-uuid-002',
      order_number: 'ORD-20261122-002',
      status: 'delivered',
      currency: 'USD',
      total_amount: 220.0,
      created_at: '2026-11-22T09:00:00Z',
    },
  ];

  const mockFullOrder: OrderResponse = {
    id: 'order-uuid-001',
    order_number: 'ORD-20261122-001',
    status: 'confirmed',
    currency: 'USD',
    subtotal: 150.0,
    shipping_fee: 0,
    tax: 0,
    total_amount: 150.0,
    created_at: '2026-11-22T08:00:00Z',
    shipping_snapshot: {
      recipient_name: 'Customer 1',
      line1: '123 Main St',
      city: 'Seattle',
      region: 'WA',
      postal_code: '98101',
      country_code: 'USA',
    },
    items: [],
  };

  // Helper component to establish authenticated state in AuthContext
  const AuthSetup: React.FC<{ onSelectOrder: (order: OrderResponse) => void }> = ({ onSelectOrder }) => {
    const { login, isAuthenticated } = useAuth();
    return (
      <div>
        {!isAuthenticated && (
          <button
            onClick={() => login('customer@example.com', 'Password123456!')}
          >
            Authenticate
          </button>
        )}
        <OrderListView onSelectOrder={onSelectOrder} />
      </div>
    );
  };

  it('fetches and renders customer orders, selecting one loads full order', async () => {
    vi.spyOn(api.auth, 'login').mockResolvedValueOnce({
      access_token: 'cust_valid_token',
      token_type: 'bearer',
      expires_in: 3600,
    });
    const listSpy = vi.spyOn(api.orders, 'list').mockResolvedValueOnce(mockSummaries);
    const getSpy = vi.spyOn(api.orders, 'get').mockResolvedValueOnce(mockFullOrder);

    const onSelectOrder = vi.fn();

    render(
      <AuthProvider>
        <AuthSetup onSelectOrder={onSelectOrder} />
      </AuthProvider>
    );

    // Authenticate
    fireEvent.click(screen.getByRole('button', { name: 'Authenticate' }));

    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith('cust_valid_token');
    });

    // Orders rendered
    expect(screen.getByText('ORD-20261122-001')).toBeInTheDocument();
    expect(screen.getByText('ORD-20261122-002')).toBeInTheDocument();

    // Click first order
    fireEvent.click(screen.getByText('ORD-20261122-001'));

    await waitFor(() => {
      expect(getSpy).toHaveBeenCalledWith('order-uuid-001', 'cust_valid_token');
      expect(onSelectOrder).toHaveBeenCalledWith(mockFullOrder);
    });
  });
});
