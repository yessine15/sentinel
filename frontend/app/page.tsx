"use client";

import { useWebSocket } from "@/hooks/useWebSocket";
import { useState, useRef, useEffect, type KeyboardEvent } from "react";
import { Send, Loader2, Wifi, WifiOff, Bot, User, Sparkles, Cpu } from "lucide-react";
import ReactMarkdown from "react-markdown";
import ToolCallCard from "@/components/ToolCallCard";
import SourceChip from "@/components/SourceChip";

// ─────────────────────────────────────────────────────────────
// Chat page
// ─────────────────────────────────────────────────────────────

export default function ChatPage() {
  const { send, answer, toolCalls, sources, isStreaming, stop, error } = useWebSocket();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Track question/answer pairs for message history.
  const [history, setHistory] = useState<Array<{ question: string }>>([]);

  // ── Auto-scroll ──
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [answer, toolCalls, isStreaming]);

  // ── Submit ──
  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    setHistory((prev) => [...prev, { question: trimmed }]);
    send(trimmed);
    setInput("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // ── Connected? ──
  const wsConnected = error === null;
  const hasContent = history.length > 0 || answer.length > 0;

  return (
    <div className="flex h-full flex-col">
      {/* Connection status bar */}
      <div className="flex h-7 shrink-0 items-center justify-center gap-2 border-b border-gray-800 bg-gray-900 text-xs text-gray-500">
        {wsConnected ? (
          <>
            <Wifi className="h-3 w-3 text-emerald-500" /> Connected
          </>
        ) : (
          <>
            <WifiOff className="h-3 w-3 text-red-500" /> {error ?? "Disconnected"}
          </>
        )}
      </div>

      {/* ── Messages ── */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-4 py-6">
        {/* Empty state */}
        {!hasContent && (
          <div className="flex h-full items-center justify-center">
            <div className="max-w-md text-center">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-500/10">
                <Sparkles className="h-8 w-8 text-emerald-400" />
              </div>
              <p className="text-xl font-semibold text-gray-300">Sentinel SRE Agent</p>
              <p className="mt-2 text-sm leading-relaxed text-gray-500">
                Ask me anything about your Kubernetes cluster. I can check pods, query metrics,
                search logs, and pull answers from the knowledge base.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {[
                  "List pods in all namespaces",
                  "How much memory is on nodes?",
                  "Show errors from demo-api logs",
                  "Find the /health endpoint",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => {
                      setInput(q);
                      inputRef.current?.focus();
                    }}
                    className="rounded-full border border-gray-700 px-3 py-1.5 text-xs text-gray-400 transition hover:border-emerald-500 hover:text-gray-200"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Message list */}
        <div className="mx-auto max-w-3xl space-y-6">
          {history.map((h, i) => (
            <div key={i} className="space-y-4">
              {/* ── User message ── */}
              <div className="flex justify-end gap-3">
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-emerald-600 px-4 py-2.5 text-sm leading-relaxed text-white shadow-md shadow-emerald-600/20">
                  {h.question}
                </div>
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-600/20">
                  <User className="h-4 w-4 text-emerald-400" />
                </div>
              </div>

              {/* ── Assistant message (only for the LAST question) ── */}
              {i === history.length - 1 && (
                <div className="flex gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-cyan-500/20">
                    <Bot className="h-4 w-4 text-cyan-400" />
                  </div>
                  <div className="min-w-0 flex-1 space-y-3">
                    {/* Tool calls */}
                    <ToolCallCard toolCalls={toolCalls} isStreaming={isStreaming} />

                    {/* Thinking indicator */}
                    {isStreaming && !answer && toolCalls.length === 0 && (
                      <div className="flex items-center gap-2 py-1 text-sm text-gray-500">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span>Thinking…</span>
                      </div>
                    )}

                    {/* Answer text with streaming cursor */}
                    {answer && (
                      <div className="prose-chat text-sm leading-relaxed text-gray-200">
                        <ReactMarkdown>{answer}</ReactMarkdown>
                        {isStreaming && (
                          <span className="typing-cursor ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-emerald-400 align-middle" />
                        )}
                      </div>
                    )}

                    {/* Wait indicator after tools, before answer */}
                    {isStreaming && !answer && toolCalls.length > 0 && (
                      <div className="flex items-center gap-2 text-sm text-gray-500">
                        <Cpu className="h-4 w-4 animate-pulse text-cyan-400" />
                        <span>Synthesizing answer…</span>
                      </div>
                    )}

                    {/* Sources with popover chips */}
                    {sources.length > 0 && <SourceChip sources={sources} />}
                  </div>
                </div>
              )}
            </div>
          ))}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* ── Input ── */}
      <div className="shrink-0 border-t border-gray-800 bg-gray-900/50 px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isStreaming ? "Agent is thinking…" : "Ask about your cluster…"}
            disabled={isStreaming}
            className="flex-1 rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-100 placeholder-gray-500 transition outline-none focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            autoFocus
          />
          {isStreaming ? (
            <button
              onClick={stop}
              className="flex items-center gap-1.5 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-red-500"
            >
              <span className="hidden sm:inline">Stop</span>
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-white" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!input.trim()}
              className="rounded-xl bg-emerald-600 p-2.5 text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
              title="Send (Enter)"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
