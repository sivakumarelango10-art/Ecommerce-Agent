import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import { api, ApiError } from '../api/client';
import { UserResponse } from '../types/api';

interface AuthContextType {
  accessToken: string | null;
  currentUser: UserResponse | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (email: string, password: string, role?: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => void;
  clearError: () => void;
  setUserRole: (role: string) => void;
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<UserResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const setUserRole = useCallback((role: string) => {
    setCurrentUser((prev) => (prev ? { ...prev, role } : null));
  }, []);

  const login = useCallback(async (email: string, password: string, role?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const tokenRes = await api.auth.login({ email, password });
      setAccessToken(tokenRes.access_token);

      try {
        const userProfile = await api.auth.getMe(tokenRes.access_token);
        setCurrentUser(userProfile);
      } catch {
        setCurrentUser({
          id: '',
          email: email,
          is_active: true,
          role: role || 'customer',
        });
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
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
    try {
      const userRes = await api.auth.register({
        email,
        password,
        display_name: displayName,
      });
      // After registration, automatically log in
      const tokenRes = await api.auth.login({ email, password });
      setAccessToken(tokenRes.access_token);
      setCurrentUser(userRes);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Registration failed. Please try again.');
      }
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(() => {
    setAccessToken(null);
    setCurrentUser(null);
    setError(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        accessToken,
        currentUser,
        isAuthenticated: !!accessToken,
        isLoading,
        error,
        login,
        register,
        logout,
        clearError,
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
