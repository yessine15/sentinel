"use client";

import type { Plan } from "@/hooks/useWebSocket";
import { Check, X, ShieldAlert, Loader2 } from "lucide-react";

// ─────────────────────────────────────────────────────────────
// Plan card — shows a pending remediation plan with Approve/Reject
// buttons (T3.6 human-in-the-loop approval).
// ─────────────────────────────────────────────────────────────

interface PlanCardProps {
  plan: Plan;
  status: string;
  planId: string;
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}

const PRIORITY_STYLES: Record<string, string> = {
  high: "bg-red-500/10 text-red-400 border-red-500/30",
  medium: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  low: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
};

export default function PlanCard({ plan, status, onApprove, onReject, busy }: PlanCardProps) {
  const priority = (plan.priority ?? "medium").toLowerCase();
  const priorityStyle = PRIORITY_STYLES[priority] ?? PRIORITY_STYLES.medium;
  const decided = status === "approved" || status === "rejected";
  const steps = plan.steps ?? [];

  return (
    <div className="overflow-hidden rounded-xl border border-amber-500/30 bg-gray-900/80 shadow-lg shadow-amber-900/10">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-gray-800 bg-gray-900 px-4 py-3">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-amber-400" />
          <span className="text-sm font-semibold text-gray-200">Remediation plan</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${priorityStyle}`}>
            {priority}
          </span>
          {plan.draft && (
            <span className="rounded-full border border-gray-600 px-2 py-0.5 text-xs text-gray-400">
              draft
            </span>
          )}
          <span
            className={`rounded-full border px-2 py-0.5 text-xs capitalize ${
              decided
                ? "border-emerald-500/30 text-emerald-400"
                : "border-amber-500/30 text-amber-400"
            }`}
          >
            {status === "awaiting_approval" ? "pending review" : status}
          </span>
        </div>
      </div>

      {/* Body */}
      <div className="space-y-3 px-4 py-3">
        {plan.rationale && (
          <p className="text-sm leading-relaxed text-gray-400">{plan.rationale}</p>
        )}

        <ol className="space-y-2">
          {steps.map((step, i) => (
            <li key={i} className="flex gap-3 rounded-lg border border-gray-800 bg-gray-950/60 px-3 py-2">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-cyan-500/15 text-xs font-medium text-cyan-400">
                {i + 1}
              </span>
              <div className="min-w-0">
                <div className="text-sm text-gray-200">
                  <span className="font-medium capitalize">{step.action}</span>{" "}
                  <span className="font-mono text-xs text-cyan-400">{step.target}</span>
                </div>
                {step.detail && (
                  <p className="mt-0.5 text-xs leading-relaxed text-gray-500">{step.detail}</p>
                )}
              </div>
            </li>
          ))}
          {steps.length === 0 && <li className="text-xs text-gray-500">No steps proposed.</li>}
        </ol>

        {/* Actions */}
        {!decided && (
          <div className="flex gap-2 pt-1">
            <button
              onClick={onApprove}
              disabled={busy}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              Approve
            </button>
            <button
              onClick={onReject}
              disabled={busy}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-gray-700 px-3 py-2 text-sm font-medium text-gray-300 transition hover:border-red-500/40 hover:text-red-400 disabled:opacity-50"
            >
              <X className="h-4 w-4" />
              Reject
            </button>
          </div>
        )}
        {decided && (
          <p className="pt-1 text-center text-xs text-gray-500">
            {status === "approved"
              ? "✅ Approved — the graph has been unblocked."
              : "❌ Rejected — no action will be taken."}
          </p>
        )}
      </div>
    </div>
  );
}
