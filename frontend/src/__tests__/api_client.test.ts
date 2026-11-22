import { describe, it, expect, vi, beforeEach } from 'vitest';
import { api } from '../api/client';
import { GuestOrderResponse } from '../types/api';

describe('API Client & Token Isolation', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('correctly passes bearer authorization for authenticated customer chat', async () => {
    const mockResponse = {
      destination: 'order_support',
      order_answer: 'Your order ORD-123 is confirmed.',
      shopping_response: null,
      general_answer: null,
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => mockResponse,
    } as Response);

    const token = 'cust_jwt_token_123';
    const result = await api.chat.sendQuestion('Where is my order?', token);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, options] = fetchSpy.mock.calls[0];
    expect(url).toBe('/api/v1/shopping/chat');
    expect(options?.method).toBe('POST');
    expect((options?.headers as Record<string, string>)['Authorization']).toBe(`Bearer ${token}`);
    expect(JSON.parse(options?.body as string)).toEqual({ question: 'Where is my order?' });
    expect(result.destination).toBe('order_support');
    expect(result.order_answer).toBe('Your order ORD-123 is confirmed.');
  });

  it('sends aligned login payload with email and password', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        access_token: 'valid_jwt_token',
        token_type: 'bearer',
        expires_in: 3600,
      }),
    } as Response);

    const res = await api.auth.login({
      email: 'customer@example.com',
      password: 'SecurePassword123!',
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'customer@example.com',
        password: 'SecurePassword123!',
      }),
    });
    expect(res.access_token).toBe('valid_jwt_token');
  });

  it('sends aligned registration payload with display_name', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        id: 'user-uuid-123',
        email: 'customer@example.com',
        is_active: true,
      }),
    } as Response);

    const res = await api.auth.register({
      email: 'customer@example.com',
      password: 'SecurePassword123!',
      display_name: 'Jane Doe',
    });

    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'customer@example.com',
        password: 'SecurePassword123!',
        display_name: 'Jane Doe',
      }),
    });
    expect(res.email).toBe('customer@example.com');
  });

  it('correctly handles guest OTP request and verify endpoints with aligned otp field', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({ message: 'Verification code sent.' }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({
          access_token: 'guest_token_abc',
          token_type: 'Bearer',
          expires_in: 900,
          order_number: 'ORD-123',
        }),
      } as Response);

    const reqRes = await api.guest.requestOtp({
      order_number: 'ORD-123',
      email: 'guest@example.com',
    });
    expect(reqRes.message).toBe('Verification code sent.');

    const verifyRes = await api.guest.verifyOtp({
      order_number: 'ORD-123',
      email: 'guest@example.com',
      otp: '123456',
    });
    expect(verifyRes.access_token).toBe('guest_token_abc');
    expect(verifyRes.order_number).toBe('ORD-123');
    expect(verifyRes.expires_in).toBe(900);
  });

  it('guest getOrder sends guest token and receives aligned GuestOrderResponse', async () => {
    const mockGuestOrder: GuestOrderResponse = {
      order_number: 'ORD-GUEST-99',
      status: 'confirmed',
      currency: 'USD',
      subtotal: 50.0,
      shipping_fee: 5.0,
      tax: 4.5,
      total_amount: 59.5,
      created_at: '2026-11-22T10:00:00Z',
      shipping_snapshot: {
        recipient_name: 'Guest Recipient',
        line1: '123 Guest St',
        city: 'Portland',
        region: 'OR',
        postal_code: '97201',
        country_code: 'USA',
      },
      items: [
        {
          product_sku: 'SKU-01',
          product_name: 'Wireless Mouse',
          quantity: 1,
          unit_price: 50.0,
          line_total: 50.0,
        },
      ],
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => mockGuestOrder,
    } as Response);

    const guestToken = 'guest_jwt_token_xyz';
    const order = await api.guest.getOrder('ORD-GUEST-99', guestToken);

    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/orders/guest/ORD-GUEST-99', {
      headers: {
        Authorization: `Bearer ${guestToken}`,
      },
    });
    expect(order.order_number).toBe('ORD-GUEST-99');
    expect(order.subtotal).toBe(50.0);
    expect(order.shipping_fee).toBe(5.0);
    expect(order.shipping_snapshot.line1).toBe('123 Guest St');
    expect(order.items[0].line_total).toBe(50.0);
  });

  it('parses structured 422 validation errors cleanly into ApiError message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 422,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        detail: [
          { loc: ['body', 'otp'], msg: 'String should have at least 6 characters' },
        ],
      }),
    } as Response);

    await expect(
      api.guest.verifyOtp({
        order_number: 'ORD-123',
        email: 'guest@example.com',
        otp: '12',
      })
    ).rejects.toThrow('String should have at least 6 characters');
  });

  it('parses 401 unauthorized errors as ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 401,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        detail: 'Could not validate credentials',
      }),
    } as Response);

    await expect(api.orders.list('invalid_token')).rejects.toThrow('Could not validate credentials');
  });

  it('guarantees customer and guest tokens remain strictly isolated and guest tokens are never attached to /shopping/chat', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ destination: 'order_support', order_answer: 'Verified' }),
    } as Response);

    const customerToken = 'customer_jwt_token_111';
    const guestToken = 'guest_jwt_token_999';

    // 1. Call chat endpoint with customer token
    await api.chat.sendQuestion('Where is my order?', customerToken);
    expect(fetchSpy).toHaveBeenLastCalledWith(
      '/api/v1/shopping/chat',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: `Bearer ${customerToken}`,
        }),
      })
    );

    // 2. Call guest order endpoint with guest token
    await api.guest.getOrder('ORD-123', guestToken);
    expect(fetchSpy).toHaveBeenLastCalledWith(
      '/api/v1/orders/guest/ORD-123',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: `Bearer ${guestToken}`,
        }),
      })
    );

    // 3. Assert guest token is never attached to /shopping/chat across all calls
    for (const call of fetchSpy.mock.calls) {
      if (call[0] === '/api/v1/shopping/chat') {
        const headers = call[1]?.headers as Record<string, string>;
        expect(headers['Authorization']).not.toBe(`Bearer ${guestToken}`);
        expect(headers['Authorization']).toBe(`Bearer ${customerToken}`);
      }
    }
  });
});
