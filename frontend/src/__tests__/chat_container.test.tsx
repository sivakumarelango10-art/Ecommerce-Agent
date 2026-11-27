import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { ChatContainer } from '../components/chat/ChatContainer';
import { AuthProvider } from '../context/AuthContext';
import { App } from '../App';
import { api, ApiError } from '../api/client';

describe('Phase 5: ChatContainer & Guest Question / Login Replay Flows', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    window.history.replaceState(null, '', '/');
  });

  it('renders welcome screen when empty with quick prompts', () => {
    render(
      <AuthProvider>
        <ChatContainer
          onInspectOrderNumber={vi.fn()}
          onOpenAuth={vi.fn()}
          onOpenGuestPortal={vi.fn()}
        />
      </AuthProvider>
    );

    expect(screen.getByText(/How can I assist your shopping today\?/i)).toBeInTheDocument();
    expect(screen.getByText('What are my recent orders?')).toBeInTheDocument();
    expect(screen.getByText('Recommend popular electronics')).toBeInTheDocument();
  });

  it('allows unauthenticated guests to ask public shopping and policy questions directly', async () => {
    const sendSpy = vi.spyOn(api.chat, 'sendQuestion').mockResolvedValueOnce({
      destination: 'shopping_agent',
      shopping_response: {
        message: 'Here are popular electronics in stock right now.',
        products: [],
      },
      order_answer: null,
      general_answer: null,
    });
    const onOpenAuth = vi.fn();

    render(
      <AuthProvider>
        <ChatContainer
          onInspectOrderNumber={vi.fn()}
          onOpenAuth={onOpenAuth}
          onOpenGuestPortal={vi.fn()}
        />
      </AuthProvider>
    );

    fireEvent.click(screen.getByText('Recommend popular electronics'));

    await waitFor(() => {
      expect(sendSpy).toHaveBeenCalledTimes(1);
      expect(sendSpy).toHaveBeenCalledWith('Recommend popular electronics', null);
      expect(screen.getByText('Here are popular electronics in stock right now.')).toBeInTheDocument();
    });
    expect(onOpenAuth).not.toHaveBeenCalled();
  });

  it('guides unauthenticated users asking order questions to sign in or guest portal and invokes onOpenAuth', async () => {
    const onOpenAuth = vi.fn();
    const onOpenGuestPortal = vi.fn();
    const sendSpy = vi.spyOn(api.chat, 'sendQuestion');

    render(
      <AuthProvider>
        <ChatContainer
          onInspectOrderNumber={vi.fn()}
          onOpenAuth={onOpenAuth}
          onOpenGuestPortal={onOpenGuestPortal}
        />
      </AuthProvider>
    );

    const input = screen.getByLabelText(/Ask StoreFlow Assistant/i);
    fireEvent.change(input, { target: { value: 'Where is my order?' } });
    fireEvent.click(screen.getByRole('button', { name: /Send message/i }));

    expect(screen.getByText('Where is my order?')).toBeInTheDocument();
    expect(
      screen.getByText(/To access personal order details, please sign in to your account/i)
    ).toBeInTheDocument();
    expect(onOpenAuth).toHaveBeenCalledWith('Where is my order?', expect.any(String));
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it('preserves protected guest question across failed login, opens modal on same view, and auto-replays once on success', async () => {
    const loginSpy = vi
      .spyOn(api.auth, 'login')
      .mockRejectedValueOnce(new ApiError('Invalid email or password.', 401))
      .mockResolvedValueOnce({
        access_token: 'customer-fresh-jwt',
        token_type: 'bearer',
        expires_in: 1800,
      });
    vi.spyOn(api.auth, 'getMe').mockResolvedValueOnce({
      id: 'cust-10',
      email: 'shopper@example.com',
      is_active: true,
      role: 'customer',
    });
    const sendSpy = vi.spyOn(api.chat, 'sendQuestion').mockResolvedValueOnce({
      destination: 'order_support',
      order_answer: 'Order ORD-2026-991 is shipped via express courier.',
      shopping_response: null,
      general_answer: null,
    });

    render(<App />);

    const input = screen.getByLabelText(/Ask StoreFlow Assistant/i);
    fireEvent.change(input, { target: { value: 'Track my order ORD-2026-991' } });
    fireEvent.click(screen.getByRole('button', { name: /Send message/i }));

    // Auth modal opens with preserved question banner while staying in chat view
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/Track my order ORD-2026-991/i)).toBeInTheDocument();

    // First attempt: wrong password fails cleanly, keeps question preserved
    fireEvent.change(screen.getByLabelText(/Email Address/i), {
      target: { value: 'shopper@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'WrongPassword123!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign In to Account/i }));

    expect(await screen.findByText(/Invalid email or password\./i)).toBeInTheDocument();
    expect(sendSpy).not.toHaveBeenCalled();

    // Second attempt: valid credentials log in and replay pending question ONCE
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'CorrectPassword123!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign In to Account/i }));

    await waitFor(() => {
      expect(loginSpy).toHaveBeenCalledTimes(2);
      expect(sendSpy).toHaveBeenCalledTimes(1);
      expect(sendSpy).toHaveBeenCalledWith('Track my order ORD-2026-991', 'customer-fresh-jwt');
    });

    expect(
      await screen.findByText(/Order ORD-2026-991 is shipped via express courier\./i)
    ).toBeInTheDocument();
    // Question bubble appears once in chat (not duplicated)
    expect(screen.getAllByText('Track my order ORD-2026-991')).toHaveLength(1);
  });

  it('cancelling AuthModal prevents unexpected auto-replay on later unrelated login, while inline button still works', async () => {
    vi.spyOn(api.auth, 'login').mockResolvedValueOnce({
      access_token: 'later-jwt',
      token_type: 'bearer',
      expires_in: 1800,
    });
    vi.spyOn(api.auth, 'getMe').mockResolvedValueOnce({
      id: 'cust-11',
      email: 'shopper@example.com',
      is_active: true,
      role: 'customer',
    });
    const sendSpy = vi.spyOn(api.chat, 'sendQuestion');

    render(<App />);

    const input = screen.getByLabelText(/Ask StoreFlow Assistant/i);
    fireEvent.change(input, { target: { value: 'Where is my order?' } });
    fireEvent.click(screen.getByRole('button', { name: /Send message/i }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    // User dismisses modal with Escape
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    // User signs in separately via Navbar -> old abandoned prompt must NOT auto-fire
    fireEvent.click(screen.getByRole('button', { name: /Customer Sign In/i }));
    fireEvent.change(screen.getByLabelText(/Email Address/i), {
      target: { value: 'shopper@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'ValidPassword1234!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign In to Account/i }));

    await waitFor(() => {
      expect(screen.getByText('shopper@example.com')).toBeInTheDocument();
    });
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it('prompts login and resumes cleanly if session returns 401 while asking a question', async () => {
    window.localStorage.setItem(
      'storeflow.auth.session.v1',
      JSON.stringify({ accessToken: 'expiring-jwt', expiresAt: Date.now() + 60_000 })
    );
    vi.spyOn(api.auth, 'getMe')
      .mockResolvedValueOnce({
        id: 'cust-12',
        email: 'shopper@example.com',
        is_active: true,
        role: 'customer',
      })
      .mockResolvedValueOnce({
        id: 'cust-12',
        email: 'shopper@example.com',
        is_active: true,
        role: 'customer',
      });
    vi.spyOn(api.auth, 'login').mockResolvedValueOnce({
      access_token: 'renewed-jwt',
      token_type: 'bearer',
      expires_in: 1800,
    });
    const sendSpy = vi
      .spyOn(api.chat, 'sendQuestion')
      .mockRejectedValueOnce(new ApiError('Token expired', 401))
      .mockResolvedValueOnce({
        destination: 'order_support',
        order_answer: 'Order ORD-77 is delivered.',
        shopping_response: null,
        general_answer: null,
      });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('shopper@example.com')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Ask StoreFlow Assistant/i), {
      target: { value: 'Check ORD-77 status' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send message/i }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/Your session expired\. Sign in to complete your question\./i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Email Address/i), {
      target: { value: 'shopper@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'ValidPassword1234!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign In to Account/i }));

    await waitFor(() => {
      expect(sendSpy).toHaveBeenCalledTimes(2);
      expect(sendSpy).toHaveBeenLastCalledWith('Check ORD-77 status', 'renewed-jwt');
    });
    expect(await screen.findByText('Order ORD-77 is delivered.')).toBeInTheDocument();
  });
});
