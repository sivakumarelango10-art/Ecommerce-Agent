import React, { useEffect, useState } from 'react';
import { ShieldAlert, LogIn, ArrowLeft, Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api, ApiError } from '../../api/client';
import { Button } from '../ui/Button';

export interface AdminGuardProps {
  children: React.ReactNode;
  onReturnToStore?: () => void;
  onBackToStore?: () => void;
  onOpenLogin?: () => void;
}

export const AdminGuard: React.FC<AdminGuardProps> = ({
  children,
  onReturnToStore,
  onBackToStore,
  onOpenLogin,
}) => {
  const { accessToken, currentUser, isAuthenticated, logout, setUserRole } = useAuth();
  const [isVerifying, setIsVerifying] = useState<boolean>(true);
  const [isAuthorized, setIsAuthorized] = useState<boolean>(false);
  const [forbiddenError, setForbiddenError] = useState<string | null>(null);

  const handleReturnToStore = onReturnToStore || onBackToStore || (() => {
    if (typeof window !== 'undefined') window.location.href = '/';
  });

  useEffect(() => {
    let isMounted = true;

    async function verifyAdminAccess() {
      if (!isAuthenticated || !accessToken) {
        if (isMounted) {
          setIsVerifying(false);
          setIsAuthorized(false);
          setForbiddenError(null);
        }
        return;
      }

      // If already cached as customer, reject immediately without unnecessary roundtrip
      if (currentUser?.role === 'customer') {
        if (isMounted) {
          setIsVerifying(false);
          setIsAuthorized(false);
          setForbiddenError('Access restricted: You do not possess administrator privileges.');
        }
        return;
      }

      setIsVerifying(true);
      setForbiddenError(null);

      try {
        // Backend API is the true authorization authority
        await api.admin.getOverview(accessToken);
        if (isMounted) {
          setIsAuthorized(true);
          setIsVerifying(false);
        }
      } catch (err: unknown) {
        if (!isMounted) return;

        if (err instanceof ApiError) {
          if (err.status === 401) {
            // Revoked, expired or invalid token
            logout();
            setIsAuthorized(false);
          } else if (err.status === 403) {
            // Authenticated customer attempting admin access
            if (setUserRole) setUserRole('customer');
            setForbiddenError(err.message || 'Access restricted: Administrator privileges required.');
            setIsAuthorized(false);
          } else {
            setForbiddenError(`Unexpected server verification response: ${err.message}`);
            setIsAuthorized(false);
          }
        } else {
          setForbiddenError('Failed to verify administrative authorization with StoreFlow backend.');
          setIsAuthorized(false);
        }
        setIsVerifying(false);
      }
    }

    verifyAdminAccess();

    return () => {
      isMounted = false;
    };
  }, [accessToken, isAuthenticated, currentUser?.role, logout, setUserRole]);

  // Case 1: Checking credentials with backend
  if (isVerifying) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-12 h-12 rounded-2xl bg-emerald-50 border border-emerald-200 flex items-center justify-center mb-4 text-emerald-600 shadow-sm animate-pulse">
          <Loader2 className="w-6 h-6 animate-spin text-emerald-600" />
        </div>
        <h2 className="text-base font-semibold text-slate-800 tracking-tight">Verifying Administrative Privileges</h2>
        <p className="text-xs text-slate-500 mt-1 max-w-sm">
          StoreFlow is validating your cryptographic authorization credentials against the platform security layer...
        </p>
      </div>
    );
  }

  // Case 2: Unauthenticated Guest
  if (!isAuthenticated || !accessToken) {
    return (
      <div className="min-h-screen bg-slate-900 flex flex-col items-center justify-center p-6 text-white text-center">
        <div className="w-16 h-16 rounded-2xl bg-slate-800 border border-slate-700 flex items-center justify-center mb-6 shadow-xl text-emerald-400">
          <ShieldAlert className="w-8 h-8" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-white mb-2">Administrator Login Required</h1>
        <p className="text-slate-400 text-sm max-w-md mb-8">
          The StoreFlow Admin Dashboard is restricted to verified platform administrators. Please sign in with an authorized account to continue.
        </p>
        <div className="flex flex-col sm:flex-row items-center gap-3">
          <Button
            variant="primary"
            size="md"
            onClick={onOpenLogin || (() => { if (typeof window !== 'undefined') window.location.href = '/'; })}
            className="w-full sm:w-auto bg-emerald-600 hover:bg-emerald-700 text-white gap-2"
          >
            <LogIn className="w-4 h-4" />
            <span>Sign In as Administrator</span>
          </Button>
          <Button
            variant="outline"
            size="md"
            onClick={handleReturnToStore}
            className="w-full sm:w-auto border-slate-700 text-slate-300 hover:text-white hover:bg-slate-800 gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Return to Customer Store</span>
          </Button>
        </div>
      </div>
    );
  }

  // Case 3: Authenticated Customer without Admin Privileges (403 Forbidden)
  if (!isAuthorized || forbiddenError) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-16 h-16 rounded-2xl bg-rose-50 border border-rose-200 flex items-center justify-center mb-6 text-rose-600 shadow-sm">
          <ShieldAlert className="w-8 h-8" />
        </div>
        <h1 className="text-2xl font-bold text-slate-900 tracking-tight mb-2">Access Denied (403)</h1>
        <p className="text-slate-600 text-sm max-w-md mb-8">
          {forbiddenError || 'Access restricted: You do not possess administrator privileges.'}
        </p>
        <div className="flex flex-col sm:flex-row items-center gap-3">
          <Button
            variant="secondary"
            size="md"
            onClick={handleReturnToStore}
            className="w-full sm:w-auto bg-slate-900 hover:bg-slate-800 text-white gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Return to StoreFlow</span>
          </Button>
          <Button
            variant="outline"
            size="md"
            onClick={logout}
            className="w-full sm:w-auto border-slate-200 text-slate-700 hover:bg-slate-100 gap-2"
          >
            <LogIn className="w-4 h-4" />
            <span>Switch Account</span>
          </Button>
        </div>
      </div>
    );
  }

  // Case 4: Authorized Administrator
  return <>{children}</>;
};

export default AdminGuard;
