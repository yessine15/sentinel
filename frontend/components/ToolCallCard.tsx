"use client";

import { useState } from "react";
import { Wrench, ChevronDown, ChevronRight, Check, Loader2 } from "lucide-react";

export interface ToolCallData {
  name: string;
  args: Record<string, unknown>;
  result?: string;
}

interface Props {
  toolCalls: ToolCallData[];
  isStreaming: boolean;
}

/** Collapsible tool-call cards.  Shows args inline; click expands the
 *  result.  Live calls show a spinner until the result arrives. */
export default function ToolCallCard({ toolCalls, isStreaming }: Props) {
  if (toolCalls.length === 0) return null;

  return (
    <div className="space-y-2">
      {toolCalls.map((tc, i) => (
        <ToolCallItem
          key={`${tc.name}-${i}`}
          toolCall={tc}
          isStreaming={isStreaming && !tc.result}
        />
      ))}
    </div>
  );
}

function ToolCallItem({ toolCall, isStreaming }: { toolCall: ToolCallData; isStreaming: boolean }) {
  const [expanded, setExpanded] = useState(true);

  const argStr = Object.entries(toolCall.args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(", ");

  const hasResult = !!toolCall.result;
  const isLive = isStreaming && !hasResult;

  return (
    <div className="overflow-hidden rounded-lg border border-gray-700 bg-gray-800/40 text-xs transition">
      {/* Header — always visible */}
      <button
        onClick={() => hasResult && setExpanded((p) => !p)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-gray-800"
      >
        {/* Status icon */}
        {isLive ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-amber-400" />
        ) : hasResult ? (
          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
        ) : (
          <Wrench className="h-3.5 w-3.5 shrink-0 text-gray-500" />
        )}

        {/* Tool name + args */}
        <span className="font-medium text-cyan-400">{toolCall.name}</span>
        <span className="truncate text-gray-400">({argStr})</span>

        {/* Status label */}
        <span className="ml-auto shrink-0 text-gray-500">
          {isLive ? "Running…" : hasResult ? "Done" : "Pending"}
        </span>

        {/* Expand chevron (only when result available) */}
        {hasResult && (
          <span className="text-gray-500">
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </span>
        )}
      </button>

      {/* Body — expandable result */}
      {hasResult && expanded && (
        <div className="border-t border-gray-700">
          <pre className="max-h-48 overflow-auto bg-gray-900 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-gray-300">
            {toolCall.result!.length > 1500
              ? toolCall.result!.slice(0, 1500) + "\n… (truncated)"
              : toolCall.result}
          </pre>
        </div>
      )}
    </div>
  );
}
