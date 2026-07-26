"use client";

import { useWebSocket } from "@/hooks/useWebSocket";
import { useState, useRef, useEffect, type KeyboardEvent } from "react";
import { Send, Loader2, Wifi, WifiOff } from "lucide-react";
import ReactMarkdown from "react-markdown";

// ─────────────────────────────────────────────────────────────
// Chat page
// ─────────────────────────────────────────────────────────────

export default function ChatPage() {
  const { send, answer, toolCalls, sources, isStreaming, stop, error } = useWebSocket();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Track submitted questions (the hook only stores the streaming answer).
  const [questions, setQuestions] = useState<string[]>([]);

  // Auto-scroll to bottom when answer or tool calls change.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [answer, toolCalls]);

  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    setQuestions((prev) => [...prev, trimmed]);
    send(trimmed);
    setInput("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // ── Connection indicator ──
  const wsConnected = error === null;

  return (
    <div className="flex h-full flex-col">
      {/* Connection status bar */}
      <div className="flex h-7 items-center justify-center gap-2 border-b border-gray-800 bg-gray-900 text-xs text-gray-500">
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

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {questions.length === 0 && !answer && (
          <div className="flex h-full items-center justify-center">
            <div className="max-w-md text-center text-gray-500">
              <p className="text-lg font-medium text-gray-400">Sentinel SRE Agent</p>
              <p className="mt-2 text-sm">
                Ask me anything about your Kubernetes cluster.
                <br />I can check pods, query metrics, search logs, and more.
              </p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {[
                  "List pods in all namespaces",
                  "How much memory is available on nodes?",
                  "Show recent errors from demo-api logs",
                  "Find the /health endpoint in the codebase",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => {
                      setInput(q);
                      inputRef.current?.focus();
                    }}
                    className="rounded-full border border-gray-700 px-3 py-1 text-xs text-gray-400 transition hover:border-gray-500 hover:text-gray-200"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Questions + Answer */}
        <div className="mx-auto max-w-3xl space-y-4">
          {questions.map((q, i) => (
            <div key={i} className="space-y-3">
              {/* User message */}
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-2xl rounded-br-md bg-emerald-600 px-4 py-2 text-sm text-white">
                  {q}
                </div>
              </div>

              {/* Assistant answer (only shown for the last question) */}
              {i === questions.length - 1 && (
                <div className="space-y-3">
                  {/* Tool calls */}
                  {toolCalls.map((tc, j) => (
                    <div
                      key={j}
                      className="rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 text-xs"
                    >
                      <span className="font-medium text-cyan-400">🔧 {tc.name}</span>
                      <span className="ml-2 text-gray-400">
                        {Object.entries(tc.args)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(", ")}
                      </span>
                      {tc.result && (
                        <pre className="mt-1 max-h-24 overflow-auto rounded bg-gray-900 p-2 text-gray-300">
                          {tc.result.slice(0, 500)}
                        </pre>
                      )}
                    </div>
                  ))}

                  {/* Answer text */}
                  {answer && (
                    <div className="prose-chat text-sm leading-relaxed text-gray-200">
                      <ReactMarkdown>{answer}</ReactMarkdown>
                    </div>
                  )}

                  {/* Streaming indicator */}
                  {isStreaming && !answer && (
                    <div className="flex items-center gap-2 text-sm text-gray-500">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Thinking…
                    </div>
                  )}

                  {/* Sources */}
                  {sources.length > 0 && (
                    <div className="rounded-lg border border-gray-700 bg-gray-800/30 p-3">
                      <p className="mb-2 text-xs font-medium text-gray-400">📚 Sources</p>
                      <div className="space-y-1">
                        {sources.map((s, j) => (
                          <div
                            key={j}
                            className="flex items-start gap-2 rounded bg-gray-800/50 px-2 py-1 text-xs"
                          >
                            <code className="shrink-0 text-emerald-400">
                              {s.path}:{s.lines}
                            </code>
                            <span className="truncate text-gray-400">
                              {s.snippet.slice(0, 150)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-gray-800 px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isStreaming ? "Agent is thinking…" : "Ask about your cluster…"}
            disabled={isStreaming}
            className="flex-1 rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-100 placeholder-gray-500 transition outline-none focus:border-emerald-500 disabled:opacity-50"
            autoFocus
          />
          {isStreaming ? (
            <button
              onClick={stop}
              className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-red-500"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!input.trim()}
              className="rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
