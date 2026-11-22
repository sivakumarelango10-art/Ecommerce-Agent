import React, { useState } from 'react';
import {
  X,
  ShieldCheck,
  ArrowRight,
  RotateCcw,
  CheckCircle,
} from 'lucide-react';
import { useGuestOrder } from '../../context/GuestOrderContext';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Alert } from '../ui/Alert';
import { OrderInspector } from '../orders/OrderInspector';

interface GuestOrderPortalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const GuestOrderPortal: React.FC<GuestOrderPortalProps> = ({ isOpen, onClose }) => {
  const {
    step,
    orderNumber,
    email,
    verifiedOrder,
    isLoading,
    error,
    infoMessage,
    requestOtp,
    verifyOtp,
    clearGuestSession,
    clearError,
  } = useGuestOrder();

  const [inputOrderNumber, setInputOrderNumber] = useState('');
  const [inputEmail, setInputEmail] = useState('');
  const [otp, setOtp] = useState('');

  if (!isOpen) return null;

  const handleRequestOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    if (!inputOrderNumber.trim() || !inputEmail.trim()) return;
    try {
      await requestOtp(inputOrderNumber.trim(), inputEmail.trim());
    } catch {
      // Handled in context
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    if (!otp.trim()) return;
    try {
      await verifyOtp(otp.trim());
    } catch {
      // Handled in context
    }
  };

  const handleReset = () => {
    clearGuestSession();
    setInputOrderNumber('');
    setInputEmail('');
    setOtp('');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="relative p-6 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img
              src="/storeflow-logo.svg"
              alt="StoreFlow - Order Support & Shopping Agent"
              className="h-8 sm:h-9 w-auto object-contain hidden sm:block"
            />
            <div className="sm:border-l sm:border-slate-200 sm:pl-3">
              <h3 className="text-base sm:text-lg font-bold text-slate-900 flex items-center gap-1.5">
                <ShieldCheck className="w-5 h-5 text-emerald-600" />
                <span>Guest Order Verification</span>
              </h3>
              <p className="text-xs text-slate-500">Secure OTP verification for StoreFlow guest checkouts</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6">
          {error && (
            <Alert variant="error" title="Verification Error" className="mb-5">
              {error}
            </Alert>
          )}

          {/* STEP 1: Enter Order # & Email */}
          {step === 'request' && (
            <form onSubmit={handleRequestOtp} className="space-y-4">
              <p className="text-xs text-slate-600 leading-relaxed">
                Placed an order as a guest? Enter your Order Number (e.g., <code className="bg-slate-100 px-1 py-0.5 rounded text-slate-800">ORD-20261122-XXXX</code>) and the email address used during checkout.
              </p>

              <div>
                <Input
                  label="Order Number"
                  placeholder="ORD-20261122-A1B2C3"
                  value={inputOrderNumber}
                  onChange={(e) => setInputOrderNumber(e.target.value)}
                  required
                  autoFocus
                />
              </div>

              <div>
                <Input
                  label="Checkout Email Address"
                  type="email"
                  placeholder="guest@example.com"
                  value={inputEmail}
                  onChange={(e) => setInputEmail(e.target.value)}
                  required
                />
              </div>

              <div className="pt-2">
                <Button
                  type="submit"
                  variant="primary"
                  size="md"
                  className="w-full font-medium"
                  isLoading={isLoading}
                >
                  <span>Send 6-Digit Verification Code</span>
                  <ArrowRight className="w-4 h-4 ml-1.5" />
                </Button>
              </div>
            </form>
          )}

          {/* STEP 2: Enter OTP Code */}
          {step === 'verify' && (
            <form onSubmit={handleVerifyOtp} className="space-y-5">
              {infoMessage && (
                <Alert variant="info" title="Verification Code Sent">
                  {infoMessage}
                </Alert>
              )}

              <div className="p-3 bg-slate-50 border border-slate-200/80 rounded-xl text-xs space-y-1 text-slate-600">
                <div className="flex justify-between">
                  <span className="font-semibold text-slate-700">Order:</span>
                  <span className="font-mono text-slate-900 font-medium">{orderNumber}</span>
                </div>
                <div className="flex justify-between">
                  <span className="font-semibold text-slate-700">Email:</span>
                  <span className="text-slate-900">{email}</span>
                </div>
              </div>

              <div>
                <Input
                  label="6-Digit Verification Code"
                  placeholder="123456"
                  maxLength={6}
                  value={otp}
                  onChange={(e) => setOtp(e.target.value)}
                  className="text-center font-mono text-lg tracking-widest"
                  required
                  autoFocus
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Enter the one-time code sent to your email. Codes expire in 10 minutes.
                </p>
              </div>

              <div className="flex gap-2.5 pt-2">
                <Button
                  type="button"
                  variant="outline"
                  size="md"
                  onClick={handleReset}
                  className="flex-1"
                >
                  Change Email / Order
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  size="md"
                  className="flex-1"
                  isLoading={isLoading}
                >
                  <span>Verify & View Order</span>
                  <CheckCircle className="w-4 h-4 ml-1" />
                </Button>
              </div>
            </form>
          )}

          {/* STEP 3: View Verified Order */}
          {step === 'view' && verifiedOrder && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-md border border-emerald-200">
                    Verified Guest Session
                  </span>
                  <span className="text-xs text-slate-500 font-mono">{verifiedOrder.order_number}</span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleReset}
                  className="text-xs gap-1 text-slate-600 hover:text-slate-900"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span>Look up another order</span>
                </Button>
              </div>

              {/* Render embedded Order Inspector card */}
              <OrderInspector order={verifiedOrder} isCompact />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
