import React from 'react';
import {
  Sliders,
  ShieldAlert,
  Sparkles,
  Info,
  Compass,
} from 'lucide-react';

export const AIRoutingPreferences: React.FC = () => {
  return (
    <div className="space-y-6" data-testid="ai-routing-preferences">
      {/* Top Header */}
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">AI Traffic Routing Preferences</h2>
          <span className="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-amber-100 text-amber-800 border border-amber-200">
            Preview / Inactive (Phase 6F)
          </span>
        </div>
        <p className="text-sm text-slate-500 mt-1">
          Future-ready policies for dynamic multi-provider routing, free-first selection, and data-privacy gates.
        </p>
      </div>

      {/* Prominent Architectural Banner */}
      <div className="p-4 bg-slate-900 text-white rounded-xl shadow-xs border border-slate-800 space-y-2">
        <div className="flex items-center gap-2 text-amber-400 font-semibold text-sm">
          <Info className="w-4 h-4 text-amber-400" />
          Runtime Routing Status: Direct Single-Driver Mode
        </div>
        <p className="text-xs text-slate-300 leading-relaxed">
          StoreFlow's live conversational agent is currently wired directly to Google Gemini (<code className="text-emerald-400 font-mono">gemini-3.8-flash</code>)
          via the in-memory company key pool and personal key runtime. Multi-provider dynamic routing is disabled in this phase to maintain operational stability.
        </p>
      </div>

      {/* Future Policy Specifications */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Policy 1: Free-First Routing */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-2 bg-emerald-50 rounded-lg text-emerald-700">
                <Sparkles className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-slate-900">Free-First Model Selection</h3>
                <span className="text-[11px] text-slate-500">Cost Optimization</span>
              </div>
            </div>
            <span className="px-2 py-0.5 text-[10px] font-semibold rounded bg-slate-100 text-slate-600">
              Target: Phase 6F
            </span>
          </div>

          <p className="text-xs text-slate-600 leading-relaxed">
            Routes non-confidential product discovery and recommendation queries to verified Free Tier models before consuming paid quota.
          </p>

          <div className="text-[11px] text-slate-500 bg-slate-50 p-2.5 rounded border border-slate-100 flex items-center justify-between">
            <span>Enforce Free Models Priority</span>
            <span className="text-slate-400 font-mono">Disabled</span>
          </div>
        </div>

        {/* Policy 2: Data Privacy Guard */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-2 bg-rose-50 rounded-lg text-rose-700">
                <ShieldAlert className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-slate-900">Customer PII Privacy Gate</h3>
                <span className="text-[11px] text-slate-500">Compliance & Safety</span>
              </div>
            </div>
            <span className="px-2 py-0.5 text-[10px] font-semibold rounded bg-slate-100 text-slate-600">
              Target: Phase 6F
            </span>
          </div>

          <p className="text-xs text-slate-600 leading-relaxed">
            Inspects request context for customer shipping addresses, orders, and credentials. Automatically blocks sensitive payloads from free-tier endpoints where model improvement training applies.
          </p>

          <div className="text-[11px] text-slate-500 bg-slate-50 p-2.5 rounded border border-slate-100 flex items-center justify-between">
            <span>Strict PII Free-Tier Filter</span>
            <span className="text-slate-400 font-mono">Pre-Configured</span>
          </div>
        </div>

        {/* Policy 3: Personal to Company Key Fallback */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-2 bg-sky-50 rounded-lg text-sky-700">
                <Compass className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-slate-900">Personal Key Fallback Hierarchy</h3>
                <span className="text-[11px] text-slate-500">Graceful Degradation</span>
              </div>
            </div>
            <span className="px-2 py-0.5 text-[10px] font-semibold rounded bg-emerald-50 text-emerald-800 border border-emerald-200">
              Active in Runtime
            </span>
          </div>

          <p className="text-xs text-slate-600 leading-relaxed">
            Customer personal keys are attempted first. On quota exhaustion or temporary provider error, requests transparently fall back to company key pools without session interruption.
          </p>

          <div className="text-[11px] text-slate-500 bg-slate-50 p-2.5 rounded border border-slate-100 flex items-center justify-between">
            <span>Fallback to Company Pool</span>
            <span className="text-emerald-700 font-medium">Enabled (Runtime)</span>
          </div>
        </div>

        {/* Policy 4: Quota-Aware Load Balancing */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-2 bg-purple-50 rounded-lg text-purple-700">
                <Sliders className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-slate-900">Cross-Key Quota Balancing</h3>
                <span className="text-[11px] text-slate-500">Traffic Distribution</span>
              </div>
            </div>
            <span className="px-2 py-0.5 text-[10px] font-semibold rounded bg-slate-100 text-slate-600">
              Target: Phase 6F
            </span>
          </div>

          <p className="text-xs text-slate-600 leading-relaxed">
            Distributes requests across company keys within the same quota group and automatically routes around slots undergoing cooldown.
          </p>

          <div className="text-[11px] text-slate-500 bg-slate-50 p-2.5 rounded border border-slate-100 flex items-center justify-between">
            <span>Round-Robin with Cooldown Avoidance</span>
            <span className="text-slate-400 font-mono">Simulated</span>
          </div>
        </div>
      </div>
    </div>
  );
};
