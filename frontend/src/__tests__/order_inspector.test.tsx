import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OrderInspector } from '../components/orders/OrderInspector';
import { OrderResponse, GuestOrderResponse } from '../types/api';

describe('OrderInspector Component', () => {
  const mockCustomerOrder: OrderResponse = {
    id: 'order-uuid-8888',
    order_number: 'ORD-20261122-8888',
    status: 'shipped',
    currency: 'USD',
    subtotal: 120.0,
    shipping_fee: 5.0,
    tax: 9.6,
    total_amount: 134.6,
    created_at: '2026-11-22T08:00:00Z',
    shipping_snapshot: {
      recipient_name: 'Jane Doe',
      line1: '123 Market St',
      line2: 'Suite 400',
      city: 'San Francisco',
      region: 'CA',
      postal_code: '94105',
      country_code: 'USA',
    },
    items: [
      {
        id: 'item-uuid-1',
        product_sku: 'SKU-KB-01',
        product_name: 'Ergonomic Keyboard',
        unit_price: 60.0,
        quantity: 2,
        line_total: 120.0,
      },
    ],
  };

  const mockGuestOrder: GuestOrderResponse = {
    order_number: 'ORD-GUEST-7777',
    status: 'confirmed',
    currency: 'USD',
    subtotal: 50.0,
    shipping_fee: 0,
    tax: 4.0,
    total_amount: 54.0,
    created_at: '2026-11-22T09:30:00Z',
    shipping_snapshot: {
      recipient_name: 'Guest User',
      line1: '789 Guest Rd',
      city: 'Austin',
      region: 'TX',
      postal_code: '78701',
      country_code: 'USA',
    },
    items: [
      {
        product_sku: 'SKU-MOUSE-01',
        product_name: 'Wireless Mouse',
        quantity: 1,
        unit_price: 50.0,
        line_total: 50.0,
      },
    ],
  };

  it('renders placeholder when no order is selected', () => {
    render(<OrderInspector order={null} />);
    expect(screen.getByText(/No Order Selected/i)).toBeInTheDocument();
  });

  it('renders loading spinner when isLoading is true', () => {
    render(<OrderInspector order={null} isLoading={true} />);
    expect(screen.getByText(/Loading order details.../i)).toBeInTheDocument();
  });

  it('renders full customer order details matching backend schema without payment_status or discount', () => {
    render(<OrderInspector order={mockCustomerOrder} />);

    expect(screen.getByText('ORD-20261122-8888')).toBeInTheDocument();
    expect(screen.getByText('shipped')).toBeInTheDocument();

    // Items
    expect(screen.getByText('Ergonomic Keyboard')).toBeInTheDocument();
    expect(screen.getByText(/Qty: 2 × \$60.00/i)).toBeInTheDocument();
    expect(screen.getAllByText('$120.00')).toHaveLength(2); // item line_total and order subtotal

    // Financials
    expect(screen.getByText('$134.60')).toBeInTheDocument();
    expect(screen.getByText('$5.00')).toBeInTheDocument();
    expect(screen.getByText('$9.60')).toBeInTheDocument();

    // Shipping Address
    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('123 Market St')).toBeInTheDocument();
    expect(screen.getByText(/San Francisco, CA 94105/i)).toBeInTheDocument();

    // Verify removed/non-existent fields are not rendered
    expect(screen.queryByText(/Payment:/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Discount/i)).not.toBeInTheDocument();
  });

  it('renders guest order without internal database ID', () => {
    render(<OrderInspector order={mockGuestOrder} />);

    expect(screen.getByText('ORD-GUEST-7777')).toBeInTheDocument();
    expect(screen.getByText('confirmed')).toBeInTheDocument();
    expect(screen.getByText('Wireless Mouse')).toBeInTheDocument();
    expect(screen.getByText('Free')).toBeInTheDocument(); // shipping_fee === 0
    expect(screen.getByText('$54.00')).toBeInTheDocument();
    expect(screen.getByText('Guest User')).toBeInTheDocument();
    expect(screen.getByText('789 Guest Rd')).toBeInTheDocument();
  });
});
