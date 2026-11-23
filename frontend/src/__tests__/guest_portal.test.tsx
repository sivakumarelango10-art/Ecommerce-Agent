import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { GuestOrderPortal } from '../components/guest/GuestOrderPortal';
import { GuestOrderProvider } from '../context/GuestOrderContext';
import { api } from '../api/client';
import { GuestOrderResponse } from '../types/api';

describe('GuestOrderPortal Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mockGuestOrder: GuestOrderResponse = {
    order_number: 'ORD-GUEST-123',
    status: 'confirmed',
    currency: 'USD',
    subtotal: 75.0,
    shipping_fee: 0,
    tax: 6.0,
    total_amount: 81.0,
    created_at: '2026-11-22T09:00:00Z',
    shipping_snapshot: {
      recipient_name: 'Guest Customer',
      line1: '456 Guest Ave',
      city: 'Portland',
      region: 'OR',
      postal_code: '97201',
      country_code: 'USA',
    },
    items: [
      {
        product_sku: 'SKU-LAMP-01',
        product_name: 'Smart Desk Lamp',
        unit_price: 75.0,
        quantity: 1,
        line_total: 75.0,
      },
    ],
  };

  it('progresses through entire 3-step guest verification wizard with aligned schemas', async () => {
    const requestSpy = vi.spyOn(api.guest, 'requestOtp').mockResolvedValueOnce({
      message: 'Verification code sent.',
    });
    const verifySpy = vi.spyOn(api.guest, 'verifyOtp').mockResolvedValueOnce({
      access_token: 'valid_guest_jwt',
      token_type: 'Bearer',
      expires_in: 900,
      order_number: 'ORD-GUEST-123',
    });
    const getOrderSpy = vi.spyOn(api.guest, 'getOrder').mockResolvedValueOnce(mockGuestOrder);

    render(
      <GuestOrderProvider>
        <GuestOrderPortal isOpen={true} onClose={vi.fn()} />
      </GuestOrderProvider>
    );

    // STEP 1: Request OTP
    fireEvent.change(screen.getByLabelText(/Order Number/i), {
      target: { value: 'ORD-GUEST-123' },
    });
    fireEvent.change(screen.getByLabelText(/Checkout Email Address/i), {
      target: { value: 'guest@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send 6-Digit Verification Code/i }));

    await waitFor(() => {
      expect(requestSpy).toHaveBeenCalledWith({
        order_number: 'ORD-GUEST-123',
        email: 'guest@example.com',
      });
    });

    // STEP 2: Verify OTP
    expect(screen.getByRole('heading', { name: /Verification Code Sent/i })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/6-Digit Verification Code/i), {
      target: { value: '654321' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Verify & View Order/i }));

    await waitFor(() => {
      expect(verifySpy).toHaveBeenCalledWith({
        order_number: 'ORD-GUEST-123',
        email: 'guest@example.com',
        otp: '654321',
      });
      expect(getOrderSpy).toHaveBeenCalledWith('ORD-GUEST-123', 'valid_guest_jwt');
    });

    // STEP 3: View Verified Order Details (renders without internal ID)
    expect(screen.getByText('Verified Guest Session')).toBeInTheDocument();
    expect(screen.getByText('Smart Desk Lamp')).toBeInTheDocument();
    expect(screen.getByText('Guest Customer')).toBeInTheDocument();
    expect(screen.getByText('456 Guest Ave')).toBeInTheDocument();
    expect(screen.getByText('$81.00')).toBeInTheDocument();

    // Reset button
    const resetBtn = screen.getByRole('button', { name: /Look up another order/i });
    fireEvent.click(resetBtn);

    // Back to step 1
    expect(screen.getByRole('button', { name: /Send 6-Digit Verification Code/i })).toBeInTheDocument();
  });
});
