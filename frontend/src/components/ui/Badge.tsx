import React from 'react';
import { cn } from './Button';
import { OrderStatus } from '../../types/api';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: OrderStatus | 'neutral' | 'info' | string;
  children: React.ReactNode;
}

export const Badge: React.FC<BadgeProps> = ({ variant = 'neutral', className, children, ...props }) => {
  const styles: Record<string, string> = {
    // Order statuses
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    confirmed: 'bg-blue-50 text-blue-700 border-blue-200',
    shipped: 'bg-indigo-50 text-indigo-700 border-indigo-200',
    delivered: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    cancelled: 'bg-rose-50 text-rose-700 border-rose-200',
    // Payment statuses
    unpaid: 'bg-slate-100 text-slate-700 border-slate-200',
    authorized: 'bg-sky-50 text-sky-700 border-sky-200',
    captured: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    failed: 'bg-rose-50 text-rose-700 border-rose-200',
    refunded: 'bg-purple-50 text-purple-700 border-purple-200',
    // Fallbacks
    neutral: 'bg-slate-100 text-slate-700 border-slate-200',
    info: 'bg-sky-50 text-sky-700 border-sky-200',
  };

  const style = styles[variant] || styles.neutral;

  return (
    <span
      className={cn(
        'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border capitalize tracking-wide',
        style,
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
};
