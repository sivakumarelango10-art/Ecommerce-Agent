import React from 'react';
import { AlertCircle, CheckCircle, Info, XCircle } from 'lucide-react';
import { cn } from './Button';

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'error' | 'success' | 'warning' | 'info';
  title?: string;
  children: React.ReactNode;
}

export const Alert: React.FC<AlertProps> = ({ variant = 'info', title, children, className, ...props }) => {
  const icons = {
    error: <XCircle className="w-5 h-5 text-rose-600 flex-shrink-0" />,
    success: <CheckCircle className="w-5 h-5 text-emerald-600 flex-shrink-0" />,
    warning: <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0" />,
    info: <Info className="w-5 h-5 text-sky-600 flex-shrink-0" />,
  };

  const styles = {
    error: 'bg-rose-50 border-rose-200 text-rose-800',
    success: 'bg-emerald-50 border-emerald-200 text-emerald-800',
    warning: 'bg-amber-50 border-amber-200 text-amber-800',
    info: 'bg-sky-50 border-sky-200 text-sky-800',
  };

  return (
    <div
      role="alert"
      className={cn('flex items-start gap-3 p-3.5 border rounded-lg text-sm', styles[variant], className)}
      {...props}
    >
      {icons[variant]}
      <div className="flex-1">
        {title && <h5 className="font-semibold mb-0.5">{title}</h5>}
        <div className="text-xs leading-relaxed opacity-95">{children}</div>
      </div>
    </div>
  );
};
