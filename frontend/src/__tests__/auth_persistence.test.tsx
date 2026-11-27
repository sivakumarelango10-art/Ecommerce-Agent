import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { AuthProvider, useAuth, AUTH_STORAGE_KEY } from '../context/AuthContext';
import { AdminGuard } from '../components/admin/AdminGuard';
import { api, ApiError } from '../api/client';

function makeJwtWithExp(expOffsetSeconds: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ sub: 'user-1', exp }));
  return `${header}.${payload}.signature`;
}

const AuthProbe: React.FC = () => {
  const {
    isAuthenticated,
    isHydrating,
    currentUser,
    accessToken,
    sessionError,
    login,
    logout,
    retrySessionVerification,
  } = useAuth();

  return (
    <div>
      <div data-testid="hydrating">{String(Boolean(isHydrating))}</div>
      <div data-testid="authenticated">{String(isAuthenticated)}</div>
      <div data-testid="email">{currentUser?.email ?? 'none'}</div>
      <div data-testid="role">{currentUser?.role ?? 'none'}</div>
      <div data-testid="token">{accessToken ?? 'none'}</div>
      <div data-testid="session-error">{sessionError ?? 'none'}</div>
      <button onClick={() => void login('user@example.com', 'Password123456!').catch(() => {})}>
        SignIn
      </button>
      <button onClick={logout}>SignOut</button>
      <button onClick={() => void retrySessionVerification?.()}>RetrySession</button>
    </div>
  );
};

describe('Phase 4: Authentication Session Persistence & Hydration', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it('persists session on login and restores authenticated user via GET /api/v1/auth/me after remount (reload)', async () => {
    const validJwt = makeJwtWithExp(1800);
    vi.spyOn(api.auth, 'login').mockResolvedValueOnce({
      access_token: validJwt,
      token_type: 'bearer',
      expires_in: 1800,
    });
    const getMeSpy = vi.spyOn(api.auth, 'getMe').mockResolvedValue({
      id: 'cust-1',
      email: 'customer@example.com',
      is_active: true,
      role: 'customer',
    });

    const { unmount } = render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );

    expect(screen.getByTestId('hydrating')).toHaveTextContent('false');
    fireEvent.click(screen.getByRole('button', { name: 'SignIn' }));

    await waitFor(() => {
      expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    });
    expect(screen.getByTestId('email')).toHaveTextContent('customer@example.com');
    expect(window.localStorage.getItem(AUTH_STORAGE_KEY)).toContain(validJwt);

    // Simulate full browser reload by unmounting and mounting a fresh AuthProvider
    unmount();

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('hydrating')).toHaveTextContent('false');
      expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    });
    expect(getMeSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('email')).toHaveTextContent('customer@example.com');
    expect(screen.getByTestId('role')).toHaveTextContent('customer');
  });

  it('clears expired stored token on reload without calling backend /me', async () => {
    const expiredJwt = makeJwtWithExp(-60);
    window.localStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ accessToken: expiredJwt, expiresAt: Date.now() - 10_000 })
    );
    const getMeSpy = vi.spyOn(api.auth, 'getMe');

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('hydrating')).toHaveTextContent('false');
    });
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
    expect(getMeSpy).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it('invalidates session cleanly when /api/v1/auth/me rejects stored token with 401', async () => {
    const invalidJwt = makeJwtWithExp(1800);
    window.localStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ accessToken: invalidJwt, expiresAt: Date.now() + 60_000 })
    );
    vi.spyOn(api.auth, 'getMe').mockRejectedValueOnce(new ApiError('Invalid token', 401));

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('hydrating')).toHaveTextContent('false');
    });
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
    expect(window.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(screen.getByTestId('session-error')).toHaveTextContent(/session expired/i);
  });

  it('does not authenticate with unverified role when backend is unreachable and recovers on retry', async () => {
    const validJwt = makeJwtWithExp(1800);
    window.localStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ accessToken: validJwt, expiresAt: Date.now() + 60_000 })
    );
    const getMeSpy = vi
      .spyOn(api.auth, 'getMe')
      .mockRejectedValueOnce(new ApiError('Service Unavailable', 503))
      .mockResolvedValueOnce({
        id: 'admin-1',
        email: 'admin@storeflow.io',
        is_active: true,
        role: 'super_admin',
      });

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('hydrating')).toHaveTextContent('false');
    });
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
    expect(screen.getByTestId('role')).toHaveTextContent('none');
    expect(screen.getByTestId('session-error')).toHaveTextContent(/unable to verify/i);

    // Retry succeeds once backend wakes up
    fireEvent.click(screen.getByRole('button', { name: 'RetrySession' }));

    await waitFor(() => {
      expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    });
    expect(getMeSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('role')).toHaveTextContent('super_admin');
    expect(screen.getByTestId('session-error')).toHaveTextContent('none');
  });

  it('AdminGuard waits for session hydration instead of flashing login screen and allows super_admin on reload', async () => {
    const adminJwt = makeJwtWithExp(1800);
    window.localStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ accessToken: adminJwt, expiresAt: Date.now() + 60_000 })
    );

    let resolveMe!: (val: any) => void;
    vi.spyOn(api.auth, 'getMe').mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveMe = resolve;
        })
    );
    vi.spyOn(api.admin, 'getOverview').mockResolvedValueOnce({
      total_registered_users: 1,
      active_users: 1,
      inactive_users: 0,
      new_users_today: 0,
      new_users_this_week: 0,
      new_users_this_month: 1,
      total_orders: 0,
      total_logical_ai_requests: 0,
      total_provider_attempts: 0,
      company_provider_attempts: 0,
      personal_provider_attempts: 0,
      guest_ai_requests: 0,
      successful_attempts: 0,
      failed_attempts: 0,
      total_input_tokens: null,
      total_output_tokens: null,
      total_tokens: null,
      personal_to_company_fallbacks: 0,
      fallback_recovery_rate: 100,
    });

    render(
      <AuthProvider>
        <AdminGuard onOpenLogin={vi.fn()}>
          <div>Super Admin Secret Panel</div>
        </AdminGuard>
      </AuthProvider>
    );

    // While /auth/me is still in-flight, AdminGuard must stay in verifying state (never flash login prompt)
    expect(screen.getByText(/Verifying Administrative Privileges/i)).toBeInTheDocument();
    expect(screen.queryByText(/Administrator Login Required/i)).not.toBeInTheDocument();

    await act(async () => {
      resolveMe({
        id: 'sa-1',
        email: 'super@storeflow.io',
        is_active: true,
        role: 'super_admin',
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Super Admin Secret Panel')).toBeInTheDocument();
    });
  });

  it('AdminGuard distinguishes 404/503 verification failures from 403 Access Denied', async () => {
    const jwt = makeJwtWithExp(1800);
    window.localStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ accessToken: jwt, expiresAt: Date.now() + 60_000 })
    );
    vi.spyOn(api.auth, 'getMe').mockResolvedValueOnce({
      id: 'sa-1',
      email: 'super@storeflow.io',
      is_active: true,
      role: 'super_admin',
    });
    vi.spyOn(api.admin, 'getOverview').mockRejectedValueOnce(new ApiError('Not Found', 404));

    render(
      <AuthProvider>
        <AdminGuard onOpenLogin={vi.fn()}>
          <div>Protected Admin Panel</div>
        </AdminGuard>
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/Verification Error \(404\)/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Access Denied \(403\)/i)).not.toBeInTheDocument();
  });

  it('clears persisted session on explicit logout and synchronizes sign-out across tabs via StorageEvent', async () => {
    const validJwt = makeJwtWithExp(1800);
    vi.spyOn(api.auth, 'login').mockResolvedValueOnce({
      access_token: validJwt,
      token_type: 'bearer',
      expires_in: 1800,
    });
    vi.spyOn(api.auth, 'getMe').mockResolvedValueOnce({
      id: 'u1',
      email: 'user@example.com',
      is_active: true,
      role: 'customer',
    });

    const { unmount } = render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'SignIn' }));
    await waitFor(() => {
      expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    });

    // Simulate another tab logging out
    await act(async () => {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: AUTH_STORAGE_KEY,
          newValue: null,
        })
      );
    });

    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
    unmount();

    // Reloading remains logged out
    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
  });

  it('never assigns fake customer role when login succeeds but /auth/me encounters transient error', async () => {
    const validJwt = makeJwtWithExp(1800);
    vi.spyOn(api.auth, 'login').mockResolvedValueOnce({
      access_token: validJwt,
      token_type: 'bearer',
      expires_in: 1800,
    });
    vi.spyOn(api.auth, 'getMe').mockRejectedValueOnce(new Error('Network timeout'));

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'SignIn' }));

    await waitFor(() => {
      expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    });
    expect(screen.getByTestId('role')).not.toHaveTextContent('customer');
  });
});
