import React from 'react';
import {
  Bot,
  ShoppingBag,
  Sparkles,
  Package,
  ExternalLink,
} from 'lucide-react';
import { ChatMessage, ShoppingProductItem } from '../../types/api';

interface ChatMessageItemProps {
  message: ChatMessage;
  onInspectOrderNumber?: (orderNumber: string) => void;
}

export const ChatMessageItem: React.FC<ChatMessageItemProps> = ({
  message,
  onInspectOrderNumber,
}) => {
  const isUser = message.sender === 'user';

  // Extract order numbers like ORD-20261122-A1B2C3 from text
  const extractOrderNumbers = (text?: string | null): string[] => {
    if (!text) return [];
    const matches = text.match(/\bORD-[A-Za-z0-9_-]+\b/g);
    return matches ? Array.from(new Set(matches)) : [];
  };

  const detectedOrderNumbers = extractOrderNumbers(message.orderAnswer || message.text);

  const destinationBadge = () => {
    switch (message.destination) {
      case 'order_support':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
            <Package className="w-2.5 h-2.5" /> Order Support
          </span>
        );
      case 'shopping_agent':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-50 text-blue-700 border border-blue-200">
            <ShoppingBag className="w-2.5 h-2.5" /> Shopping Assistant
          </span>
        );
      case 'general_assistant':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-purple-50 text-purple-700 border border-purple-200">
            <Sparkles className="w-2.5 h-2.5" /> General Support
          </span>
        );
      default:
        return null;
    }
  };

  const formatCurrency = (amt: number, curr = 'USD') => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: curr,
    }).format(amt);
  };

  if (isUser) {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-2.5 bg-slate-900 text-white shadow-sm text-sm">
          <p className="whitespace-pre-wrap leading-relaxed">{message.text}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3 mb-5">
      {/* Bot Avatar */}
      <div className="w-8 h-8 rounded-xl bg-slate-100 border border-slate-200/90 flex items-center justify-center text-slate-700 flex-shrink-0 mt-0.5 shadow-xs">
        <Bot className="w-4 h-4 text-emerald-600" />
      </div>

      <div className="flex-1 max-w-[92%] sm:max-w-[85%] space-y-2.5">
        {/* Agent Destination Header */}
        <div className="flex items-center gap-2">
          {destinationBadge()}
          <span className="text-[11px] text-slate-400 font-medium">{message.timestamp}</span>
        </div>

        {/* Main Content Box */}
        <div className="bg-white border border-slate-200/90 rounded-2xl p-4 shadow-xs text-sm text-slate-800 space-y-3">
          {/* Order Support Answer */}
          {message.orderAnswer && (
            <div className="prose prose-sm max-w-none text-slate-800 whitespace-pre-wrap leading-relaxed">
              {message.orderAnswer}
            </div>
          )}

          {/* Fallback / General Text */}
          {!message.orderAnswer && message.text && (
            <div className="whitespace-pre-wrap leading-relaxed text-slate-800">
              {message.text}
            </div>
          )}

          {/* General Assistant Answer */}
          {message.generalAnswer && !message.orderAnswer && (
            <div className="whitespace-pre-wrap leading-relaxed text-slate-800">
              {message.generalAnswer}
            </div>
          )}

          {/* Shopping Response / Product Recommendations */}
          {message.shoppingResponse && (
            <div className="space-y-3 pt-1">
              <p className="text-slate-700">{message.shoppingResponse.message}</p>

              {message.shoppingResponse.products && message.shoppingResponse.products.length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-1">
                  {message.shoppingResponse.products.map((prod: ShoppingProductItem) => (
                    <div
                      key={prod.id || prod.sku}
                      className="p-3 rounded-xl border border-slate-200/80 bg-slate-50/50 hover:bg-slate-50 hover:border-slate-300 transition-all text-xs space-y-1.5"
                    >
                      <div className="flex items-start justify-between gap-1">
                        <span className="font-semibold text-slate-900 line-clamp-1">{prod.name}</span>
                        <span className="font-bold font-mono text-emerald-700 flex-shrink-0">
                          {formatCurrency(prod.price, prod.currency)}
                        </span>
                      </div>

                      {prod.categories && prod.categories.length > 0 && (
                        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] bg-slate-200/60 text-slate-700 capitalize">
                          {prod.categories[0].name}
                        </span>
                      )}

                      {prod.recommendation_reason && (
                        <p className="text-[11px] text-slate-600 italic leading-snug">
                          "{prod.recommendation_reason}"
                        </p>
                      )}

                      <div className="pt-1 flex items-center justify-between text-[11px]">
                        <span className={prod.in_stock ? 'text-emerald-600 font-medium' : 'text-rose-600 font-medium'}>
                          {prod.in_stock ? '● In Stock' : '○ Out of Stock'}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Detected Order Number Chips (One-click inspection) */}
          {detectedOrderNumbers.length > 0 && onInspectOrderNumber && (
            <div className="pt-2 border-t border-slate-100 flex flex-wrap items-center gap-2">
              <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                Inspect Order:
              </span>
              {detectedOrderNumbers.map((ordNum) => (
                <button
                  key={ordNum}
                  type="button"
                  onClick={() => onInspectOrderNumber(ordNum)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono font-semibold bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border border-emerald-200 transition-colors shadow-2xs"
                >
                  <Package className="w-3.5 h-3.5" />
                  <span>{ordNum}</span>
                  <ExternalLink className="w-3 h-3 opacity-60" />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
