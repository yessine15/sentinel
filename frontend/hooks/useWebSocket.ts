"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// ── Event types (mirrors the Python server) ──────────────────

export interface TokenEvent {
  type: "token";
  text: string;
}

export interface ToolEvent {
  type: "tool";
  name: string;
  args: Record<string, unknown>;
}

export interface ToolResultEvent {
  type: "tool_result";
  name: string;
  result: string;
}

export interface SourcesEvent {
  type: "sources";
  sources: Array<{
    path: string;
    lines: string;
    snippet: string;
  }>;
}

export interface DoneEvent {
  type: "done";
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export type ServerEvent =
  TokenEvent | ToolEvent | ToolResultEvent | SourcesEvent | DoneEvent | ErrorEvent;

// ── Hook state ────────────────────────────────────────────────

export interface UseWebSocketReturn {
  /** Send a chat message to the agent. */
  send: (query: string) => void;
  /** Accumulated answer text. */
  answer: string;
  /** Tool calls emitted by the agent. */
  toolCalls: Array<{ name: string; args: Record<string, unknown>; result?: string }>;
  /** Sources cited by the agent (from rag_search). */
  sources: SourcesEvent["sources"];
  /** True while the agent is running. */
  isStreaming: boolean;
  /** Abort the current run. */
  stop: () => void;
  /** The last error message, if any. */
  error: string | null;
}

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL
    ? process.env.NEXT_PUBLIC_WS_URL
    : typeof window !== "undefined"
      ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/chat/ws`
      : "ws://localhost:8000/chat/ws";

export function useWebSocket(): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const [answer, setAnswer] = useState("");
  const [toolCalls, setToolCalls] = useState<UseWebSocketReturn["toolCalls"]>([]);
  const [sources, setSources] = useState<SourcesEvent["sources"]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Connect on mount ──
  useEffect(() => {
    let ws: WebSocket;
    let retries = 0;
    const maxRetries = 5;

    function connect() {
      ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        retries = 0;
        setError(null);
      };

      ws.onmessage = (e: MessageEvent) => {
        try {
          const evt: ServerEvent = JSON.parse(e.data as string);
          switch (evt.type) {
            case "token":
              setAnswer((prev) => prev + evt.text);
              break;
            case "tool":
              setToolCalls((prev) => [...prev, { name: evt.name, args: evt.args }]);
              break;
            case "tool_result":
              setToolCalls((prev) =>
                prev.map((tc) =>
                  tc.name === evt.name && !tc.result ? { ...tc, result: evt.result } : tc,
                ),
              );
              break;
            case "sources":
              setSources(evt.sources);
              break;
            case "done":
              setIsStreaming(false);
              break;
            case "error":
              setError(evt.message);
              setIsStreaming(false);
              break;
          }
        } catch {
          // Ignore non-JSON frames (e.g. binary data).
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (retries < maxRetries) {
          retries++;
          setTimeout(connect, 1000 * retries);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      retries = maxRetries; // stop reconnecting
      ws?.close();
    };
  }, []);

  // ── send ──
  const send = useCallback((query: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setError("WebSocket not connected — is the API server running?");
      return;
    }
    setAnswer("");
    setToolCalls([]);
    setSources([]);
    setError(null);
    setIsStreaming(true);
    wsRef.current.send(JSON.stringify({ type: "chat", query }));
  }, []);

  // ── stop ──
  const stop = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "stop" }));
    }
    setIsStreaming(false);
  }, []);

  return { send, answer, toolCalls, sources, isStreaming, stop, error };
}
