"use client";

import { useState } from "react";
import { FileCode, X } from "lucide-react";

export interface SourceData {
  path: string;
  lines: string;
  snippet: string;
}

interface Props {
  sources: SourceData[];
}

/** Clickable ``[path:lines]`` chips.  Hover shows the snippet in a popover;
 *  click pins the popover open. */
export default function SourceChip({ sources }: Props) {
  return (
    <div className="mt-3 rounded-lg border border-gray-700 bg-gray-800/40 p-3">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-gray-400">
        <FileCode className="h-3.5 w-3.5" />
        Sources
      </p>

      <div className="flex flex-wrap gap-2">
        {sources.map((s, i) => (
          <SourceBadge key={`${s.path}:${s.lines}-${i}`} source={s} index={i} />
        ))}
      </div>
    </div>
  );
}

function SourceBadge({ source, index }: { source: SourceData; index: number }) {
  const [pinned, setPinned] = useState(false);
  const show = pinned;

  return (
    <div className="relative">
      {/* Chip */}
      <button
        onClick={() => setPinned((p) => !p)}
        onMouseEnter={() => setPinned(true)}
        onMouseLeave={() => setPinned(false)}
        className="inline-flex items-center gap-1 rounded-md border border-gray-600 bg-gray-800 px-2 py-1 text-xs text-emerald-400 transition hover:border-emerald-500 hover:bg-gray-700"
        title="Click to pin popover"
      >
        <span className="font-mono">
          [{index + 1}] {source.path}:{source.lines}
        </span>
      </button>

      {/* Popover */}
      {show && (
        <div className="source-popover bg-gray-850 absolute bottom-full left-0 z-20 mb-2 w-80 rounded-lg border border-gray-600 p-3 shadow-2xl">
          <div className="mb-1.5 flex items-center justify-between">
            <code className="text-xs font-semibold text-emerald-400">
              {source.path}:{source.lines}
            </code>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setPinned(false);
              }}
              className="rounded p-0.5 text-gray-500 hover:bg-gray-700 hover:text-gray-300"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
          <pre className="max-h-48 overflow-auto rounded bg-gray-900 p-2 text-xs leading-relaxed whitespace-pre-wrap text-gray-300">
            {source.snippet}
          </pre>
        </div>
      )}
    </div>
  );
}
