import React, { createContext, useContext, useState, useCallback, useEffect, useRef, ReactNode } from 'react';
import { api, ApiError } from '../api/client';
import { UserResponse } from '../types/api';

export const AUTH_STORAGE_KEY = 'storeflow.auth.session.v1';

interface StoredAuthSession {
  accessToken: string;
  expiresAt: number;
}

export interface AuthContextType {
  accessToken: string | null;
  currentUser: UserResponse | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  isHydrating?: boolean;
  error: string | null;
  sessionError?: string | null;
  login: (email: string, password: string, role?: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => void;
  clearError: () => void;
  clearSessionError?: () => void;
  retrySessionVerification?: () => Promise<void>;
  setUserRole: (role: string) => void;
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined);

function parseJwtExpiryMs(token: string): number | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded)) as { exp?: unknown };
    if (typeof payload.exp === 'number' && Number.isFinite(payload.exp)) {
      return payload.exp * 1000;
    }
    return null;
  } catch {
    return null;
  }
}

function resolveExpiryMs(token: string, expiresInSeconds?: number): number {
  const jwtExp = parseJwtExpiryMs(token);
  if (jwtExp !== null) {
    return jwtExp;
  }
  const ttlSeconds = typeof expiresInSeconds === 'number' && expiresInSeconds > 0 ? expiresInSeconds : 900;
  return Date.now() + ttlSeconds * 1000;
}

function readStoredSession(): StoredAuthSession | null {
  if (typeof window === 'undefined' || !window.localStorage) {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredAuthSession>;
    if (
      typeof parsed.accessToken !== 'string' ||
      !parsed.accessToken.trim() ||
      typeof parsed.expiresAt !== 'number' ||
      !Number.isFinite(parsed.expiresAt)
    ) {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      return null;
    }
    if (parsed.expiresAt <= Date.now()) {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      return null;
    }
    return {
      accessToken: parsed.accessToken,
      expiresAt: parsed.expiresAt,
    };
  } catch {
    try {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
    } catch {
      // Ignore storage access errors in locked-down contexts
    }
    return null;
  }
}

function writeStoredSession(session: StoredAuthSession): void {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Graceful no-op when storage quota is unavailable
  }
}

function clearStoredSession(): void {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // Graceful no-op
  }
}

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const initialStoredRef = useRef<StoredAuthSession | null>(readStoredSession());

  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<UserResponse | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(initialStoredRef.current?.expiresAt ?? null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isHydrating, setIsHydrating] = useState<boolean>(() => initialStoredRef.current !== null);
  const [error, setError] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);
  const clearSessionError = useCallback(() => setSessionError(null), []);

  const setUserRole = useCallback((role: string) => {
    setCurrentUser((prev) => (prev ? { ...prev, role } : null));
  }, []);

  const logout = useCallback(() => {
    clearStoredSession();
    setAccessToken(null);
    setCurrentUser(null);
    setExpiresAt(null);
    setError(null);
    setSessionError(null);
    setIsHydrating(false);
  }, []);

  const verifyAndApplyToken = useCallback(async (candidate: StoredAuthSession, isInitialMount = false) => {
    if (candidate.expiresAt <= Date.now()) {
      clearStoredSession();
      setAccessToken(null);
      setCurrentUser(null);
      setExpiresAt(null);
      setSessionError(null);
      if (isInitialMount) setIsHydrating(false);
      return;
    }

    if (isInitialMount) {
      setIsHydrating(true);
    }

    try {
      const userProfile = await api.auth.getMe(candidate.accessToken);
      if (userProfile.is_active === false) {
        clearStoredSession();
        setAccessToken(null);
        setCurrentUser(null);
        setExpiresAt(null);
        setSessionError('This account is inactive. Please contact support or sign in with another account.');
        return;
      }
      setAccessToken(candidate.accessToken);
      setCurrentUser(userProfile);
      setExpiresAt(candidate.expiresAt);
      setSessionError(null);
    } catch (err: unknown) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        clearStoredSession();
        setAccessToken(null);
        setCurrentUser(null);
        setExpiresAt(null);
        setSessionError('Your session expired. Please sign in again.');
      } else {
        // Keep user unauthenticated until server verification succeeds, but avoid clearing
        // an unexpired token on transient 5xx/cold-start errors so reload/retry can recover.
        setAccessToken(null);
        setCurrentUser(null);
        setSessionError('Unable to verify your saved session with the server. Please retry or sign in.');
      }
    } finally {
      if (isInitialMount) {
        setIsHydrating(false);
      }
    }
  }, []);

  const retrySessionVerification = useCallback(async () => {
    const stored = readStoredSession();
    if (!stored) {
      logout();
      return;
    }
    setIsHydrating(true);
    try {
      await verifyAndApplyToken(stored, false);
    } finally {
      setIsHydrating(false);
    }
  }, [logout, verifyAndApplyToken]);

  // Verify any persisted token on mount against GET /api/v1/auth/me
  useEffect(() => {
    const candidate = initialStoredRef.current;
    if (!candidate) {
      setIsHydrating(false);
      return;
    }
    let active = true;
    (async () => {
      if (!active) return;
      await verifyAndApplyToken(candidate, true);
    })();
    return () => {
      active = false;
    };
  }, [verifyAndApplyToken]);

  // Automatically log out when token expiry elapses
  useEffect(() => {
    if (!accessToken || !expiresAt) return;
    const msRemaining = expiresAt - Date.now();
    if (msRemaining <= 0) {
      logout();
      setSessionError('Your session expired. Please sign in again.');
      return;
    }
    // Cap timer delay to signed 32-bit int max (~24.8 days)
    const boundedDelay = Math.min(msRemaining, 2_147_483_647);
    const timer = window.setTimeout(() => {
      logout();
      setSessionError('Your session expired. Please sign in again.');
    }, boundedDelay);
    return () => window.clearTimeout(timer);
  }, [accessToken, expiresAt, logout]);

  // Synchronize sign-in / sign-out across browser tabs via StorageEvent
  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== AUTH_STORAGE_KEY) return;
      if (!event.newValue) {
        setAccessToken(null);
        setCurrentUser(null);
        setExpiresAt(null);
        setSessionError(null);
        return;
      }
      const nextSession = readStoredSession();
      if (!nextSession) {
        setAccessToken(null);
        setCurrentUser(null);
        setExpiresAt(null);
        return;
      }
      void verifyAndApplyToken(nextSession, false);
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, [verifyAndApplyToken]);

  const login = useCallback(async (email: string, password: string, role?: string) => {
    setIsLoading(true);
    setError(null);
    setSessionError(null);
    try {
      const tokenRes = await api.auth.login({ email, password });
      const tokenExpiresAt = resolveExpiryMs(tokenRes.access_token, tokenRes.expires_in);

      let resolvedUser: UserResponse;
      try {
        resolvedUser = await api.auth.getMe(tokenRes.access_token);
      } catch (meErr: unknown) {
        if (meErr instanceof ApiError && (meErr.status === 401 || meErr.status === 403)) {
          clearStoredSession();
          throw meErr;
        }
        // Preserve session across transient /auth/me outages without assuming 'customer' role;
        // protected admin views still enforce server verification via AdminGuard.
        resolvedUser = {
          id: '',
          email,
          is_active: true,
          role: role || '',
        };
      }

      if (resolvedUser.is_active === false) {
        clearStoredSession();
        throw new ApiError('User account is inactive.', 403);
      }

      writeStoredSession({
        accessToken: tokenRes.access_token,
        expiresAt: tokenExpiresAt,
      });
      setAccessToken(tokenRes.access_token);
      setExpiresAt(tokenExpiresAt);
      setCurrentUser(resolvedUser);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError('Login failed. Please check your credentials.');
      }
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const register = useCallback(async (email: string, password: string, displayName: string) => {
    setIsLoading(true);
    setError(null);
    setSessionError(null);
    try {
      const userRes = await api.auth.register({
        email,
        password,
        display_name: displayName,
      });
      const tokenRes = await api.auth.login({ email, password });
      const tokenExpiresAt = resolveExpiryMs(tokenRes.access_token, tokenRes.expires_in);
      writeStoredSession({
        accessToken: tokenRes.access_token,
        expiresAt: tokenExpiresAt,
      });
      setAccessToken(tokenRes.access_token);
      setExpiresAt(tokenExpiresAt);
      setCurrentUser(userRes);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError('Registration failed. Please check your details.');
      }
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        accessToken,
        currentUser,
        isAuthenticated: !!accessToken,
        isLoading,
        isHydrating,
        error,
        sessionError,
        login,
        register,
        logout,
        clearError,
        clearSessionError,
        retrySessionVerification,
        setUserRole,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
