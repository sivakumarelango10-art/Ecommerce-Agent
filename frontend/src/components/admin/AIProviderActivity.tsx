import React from 'react';
import { Activity, Clock } from 'lucide-react';
import { AdminProviders } from './AdminProviders';

export const AIProviderActivity: React.FC = () => {
  return (
    <div className="space-y-6" data-testid="ai-provider-activity">
      {/* Telemetry Architecture & Availability Context */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="p-4 bg-slate-900 text-white rounded-xl shadow-xs border border-slate-800 text-xs space-y-1.5">
          <div className="flex items-center gap-2 text-emerald-400 font-semibold text-sm">
            <Activity className="w-4 h-4 text-emerald-400" />
            Live Runtime Telemetry: Google Gemini Driver
          </div>
          <p className="text-slate-300 leading-relaxed">
            The metrics below represent real-time in-memory key pool slots for StoreFlow's active primary driver,
            Google Gemini (<code className="text-emerald-300 font-mono">gemini-3.8-flash</code>).
          </p>
          <p className="text-slate-400 text-[11px]">
            Slot health, cooldown states, and failure counts reflect the specific backend worker serving this request.
          </p>
        </div>

        <div className="p-4 bg-slate-100 border border-slate-200 rounded-xl text-xs space-y-1.5 text-slate-800">
          <div className="flex items-center gap-2 text-slate-700 font-semibold text-sm">
            <Clock className="w-4 h-4 text-slate-500" />
            Multi-Provider Activity Telemetry: Unavailable (Phase 6F)
          </div>
          <p className="text-slate-600 leading-relaxed">
            Activity streaming for non-Gemini catalogue providers (OpenAI, Anthropic, Groq, Mistral, etc.) is currently unavailable.
            Real-time latency, request counters, and slot telemetry for these providers will activate upon deployment of their respective runtime adapters.
          </p>
          <p className="text-slate-500 text-[11px]">
            Registry metadata in the Catalogue and Key Vault does not generate runtime telemetry until adapters are operational.
          </p>
        </div>
      </div>

      <AdminProviders />
    </div>
  );
};
