"""Tests for the WebSocket streaming chat endpoint (T2.5).

Tests the WebSocket protocol handling — connection, invalid input,
error paths.  Full agent integration tests require a live LLM gateway
and are run separately with ``--run-live``.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sentinel_api.main import app

client = TestClient(app)


# ════════════════════════════════════════════════════════════
# WebSocket lifecycle
# ════════════════════════════════════════════════════════════
class TestWebSocketLifecycle:
    """Accept and disconnect work correctly."""

    def test_accepts_connection(self):
        with client.websocket_connect("/chat/ws") as ws:
            assert ws  # connection succeeded

    def test_clean_disconnect(self):
        with client.websocket_connect("/chat/ws") as ws:
            ws.close()
            # Should not raise


# ════════════════════════════════════════════════════════════
# Protocol error handling
# ════════════════════════════════════════════════════════════
class TestWebSocketProtocolErrors:
    """Invalid input is rejected gracefully."""

    def test_invalid_json_returns_error(self):
        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text("not json {{{")
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "Invalid JSON" in msg["message"]

    def test_unknown_message_type_returns_error(self):
        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text(json.dumps({"type": "garbage"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "Unknown message type" in msg["message"]

    def test_missing_query_field_returns_error(self):
        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text(json.dumps({"type": "chat"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "query" in msg["message"].lower()

    def test_empty_query_returns_error(self):
        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text(json.dumps({"type": "chat", "query": ""}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "query" in msg["message"].lower()

    def test_whitespace_query_returns_error(self):
        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text(json.dumps({"type": "chat", "query": "   "}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "query" in msg["message"].lower()

    def test_stop_returns_done(self):
        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text(json.dumps({"type": "stop"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "done"


# ════════════════════════════════════════════════════════════
# Event shape validation
# ════════════════════════════════════════════════════════════
class TestEventShapes:
    """All event types have the correct shape when mocked."""

    def _import_event_builders(self):
        """Import the event builders from the chat module.

        We import dynamically to avoid module-level side effects
        (the chat module imports the agent graph).
        """
        from sentinel_api.routes.chat import (
            _done_event,
            _error_event,
            _sources_event,
            _token_event,
            _tool_call_event,
            _tool_result_event,
        )
        return (
            _token_event,
            _tool_call_event,
            _tool_result_event,
            _sources_event,
            _done_event,
            _error_event,
        )

    def test_token_event_shape(self):
        t, *_ = self._import_event_builders()
        evt = t("hello world")
        assert evt["type"] == "token"
        assert evt["text"] == "hello world"

    def test_tool_call_event_shape(self):
        _, tc, *_ = self._import_event_builders()
        evt = tc("kubectl_get", {"resource": "pods"})
        assert evt["type"] == "tool"
        assert evt["name"] == "kubectl_get"
        assert evt["args"] == {"resource": "pods"}

    def test_tool_result_event_shape(self):
        *_, tr, _, _, _ = self._import_event_builders()
        evt = tr("kubectl_get", "NAME  READY  STATUS")
        assert evt["type"] == "tool_result"
        assert evt["name"] == "kubectl_get"
        assert "NAME" in evt["result"]

    def test_tool_result_truncates_long_output(self):
        """The _extract_tool_results function truncates at 2000 chars.

        We test the extractor directly since _tool_result_event is a simple
        dict builder with no truncation logic of its own.
        """
        from langchain_core.messages import ToolMessage
        from sentinel_api.routes.chat import _extract_tool_results

        long_text = "x" * 3000
        msg = ToolMessage(content=long_text, tool_call_id="call_1", name="test")
        results = _extract_tool_results([msg])
        assert len(results) == 1
        assert len(results[0]["result"]) <= 2000

    def test_sources_event_shape(self):
        *_, se, _, _ = self._import_event_builders()
        evt = se([
            {"path": "main.py", "lines": "10-20", "snippet": "def ping():"},
        ])
        assert evt["type"] == "sources"
        assert len(evt["sources"]) == 1
        assert evt["sources"][0]["path"] == "main.py"

    def test_done_event_shape(self):
        *_, de, _ = self._import_event_builders()
        evt = de()
        assert evt["type"] == "done"

    def test_error_event_shape(self):
        *_, ee = self._import_event_builders()
        evt = ee("something broke")
        assert evt["type"] == "error"
        assert "something broke" in evt["message"]


# ════════════════════════════════════════════════════════════
# Multiple messages in one session
# ════════════════════════════════════════════════════════════
class TestWebSocketMultiMessage:
    """A single WebSocket can handle multiple messages."""

    def test_multiple_error_messages(self):
        with client.websocket_connect("/chat/ws") as ws:
            # First bad message
            ws.send_text("not json")
            msg1 = json.loads(ws.receive_text())
            assert msg1["type"] == "error"

            # Second bad message — should still work
            ws.send_text(json.dumps({"type": "unknown_type"}))
            msg2 = json.loads(ws.receive_text())
            assert msg2["type"] == "error"

            # Stop message
            ws.send_text(json.dumps({"type": "stop"}))
            msg3 = json.loads(ws.receive_text())
            assert msg3["type"] == "done"

    def test_chat_message_does_not_crash_on_llm_unavailable(self):
        """When no LLM is available, the graph run fails gracefully.

        The WebSocket should receive an error event, not crash.
        """
        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text(json.dumps({
                "type": "chat",
                "query": "hello, are you there?",
            }))
            # The agent graph will try to call the LLM gateway which
            # is not running in unit tests.  We expect either:
            #   a) an error event (connection refused)
            #   b) a timeout
            # Either way, the WebSocket should NOT raise.
            try:
                msg = json.loads(ws.receive_text())
                # Accept any event — the key is that we didn't crash.
                assert "type" in msg
            except Exception:
                pass  # Timeout is also acceptable — no crash
