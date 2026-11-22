import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import { api, ApiError } from '../api/client';
import { GuestOrderResponse } from '../types/api';

export type GuestFlowStep = 'request' | 'verify' | 'view';

interface GuestOrderContextType {
  guestToken: string | null;
  verifiedOrder: GuestOrderResponse | null;
  orderNumber: string;
  email: string;
  step: GuestFlowStep;
  isLoading: boolean;
  error: string | null;
  infoMessage: string | null;
  requestOtp: (orderNumber: string, email: string) => Promise<void>;
  verifyOtp: (otp: string) => Promise<void>;
  clearGuestSession: () => void;
  clearError: () => void;
  setStep: (step: GuestFlowStep) => void;
}

const GuestOrderContext = createContext<GuestOrderContextType | undefined>(undefined);

export const GuestOrderProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [guestToken, setGuestToken] = useState<string | null>(null);
  const [verifiedOrder, setVerifiedOrder] = useState<GuestOrderResponse | null>(null);
  const [orderNumber, setOrderNumber] = useState<string>('');
  const [email, setEmail] = useState<string>('');
  const [step, setStep] = useState<GuestFlowStep>('request');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const requestOtp = useCallback(async (ordNum: string, guestEmail: string) => {
    setIsLoading(true);
    setError(null);
    setInfoMessage(null);
    try {
      const res = await api.guest.requestOtp({
        order_number: ordNum.trim(),
        email: guestEmail.trim(),
      });
      setOrderNumber(ordNum.trim());
      setEmail(guestEmail.trim());
      setInfoMessage(res.message || 'If the order matches, a verification code was sent to your email.');
      setStep('verify');
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Failed to request verification code. Please try again.');
      }
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const verifyOtp = useCallback(async (otp: string) => {
    if (!orderNumber || !email) {
      setError('Missing order number or email. Please start over.');
      setStep('request');
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const tokenRes = await api.guest.verifyOtp({
        order_number: orderNumber,
        email: email,
        otp: otp.trim(),
      });
      const token = tokenRes.access_token;
      setGuestToken(token);

      // Immediately fetch guest order details using the newly acquired guest token
      const order = await api.guest.getOrder(orderNumber, token);
      setVerifiedOrder(order);
      setStep('view');
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Failed to verify OTP. Please try again.');
      }
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [orderNumber, email]);

  const clearGuestSession = useCallback(() => {
    setGuestToken(null);
    setVerifiedOrder(null);
    setOrderNumber('');
    setEmail('');
    setStep('request');
    setError(null);
    setInfoMessage(null);
    setIsLoading(false);
  }, []);

  return (
    <GuestOrderContext.Provider
      value={{
        guestToken,
        verifiedOrder,
        orderNumber,
        email,
        step,
        isLoading,
        error,
        infoMessage,
        requestOtp,
        verifyOtp,
        clearGuestSession,
        clearError,
        setStep,
      }}
    >
      {children}
    </GuestOrderContext.Provider>
  );
};

export function useGuestOrder(): GuestOrderContextType {
  const context = useContext(GuestOrderContext);
  if (!context) {
    throw new Error('useGuestOrder must be used within a GuestOrderProvider');
  }
  return context;
}
