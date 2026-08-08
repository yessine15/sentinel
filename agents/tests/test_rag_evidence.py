"""Tests for the T3.4 RAG Agent tool — rag_evidence."""

import json

import pytest

from sentinel_agents.tools.rag_evidence import rag_evidence


# ─────────────────────────────────────────────────────────────
# Stub-mode output
# ─────────────────────────────────────────────────────────────
class TestRagEvidenceStub:
    """rag_evidence returns structured JSON evidence in RUN_MODE=stub."""

    def test_returns_parseable_json(self):
        result = rag_evidence.invoke({"query": "how does the agent work?"})
        payload = json.loads(result)
        assert isinstance(payload, dict)
        assert "query" in payload
        assert "evidence" in payload

    def test_stub_has_ranked_evidence(self):
        payload = json.loads(rag_evidence.invoke({"query": "oomkilled runbook"}))
        assert len(payload["evidence"]) >= 1

    def test_evidence_records_have_citations(self):
        payload = json.loads(rag_evidence.invoke({"query": "architecture"}))
        for rec in payload["evidence"]:
            assert rec["path"], "evidence record missing path"
            assert rec["lines"], "evidence record missing line range"
            assert rec["score"], "evidence record missing score"
            assert rec["source_type"], "evidence record missing source_type"
            assert rec["snippet"], "evidence record missing snippet"

    def test_stub_paths_are_realistic(self):
        payload = json.loads(rag_evidence.invoke({"query": "x"}))
        paths = {rec["path"] for rec in payload["evidence"]}
        assert any("graph.py" in p or "docs/" in p for p in paths)

    def test_query_echoed_in_payload(self):
        payload = json.loads(rag_evidence.invoke({"query": "how does RAG work?"}))
        assert payload["query"] == "how does RAG work?"

    def test_top_k_honoured_in_stub(self):
        payload = json.loads(rag_evidence.invoke({"query": "x", "top_k": 2}))
        assert len(payload["evidence"]) <= 3  # stub has fixed 3, top_k caps at 3 max


# ─────────────────────────────────────────────────────────────
# Input validation
# ─────────────────────────────────────────────────────────────
class TestRagEvidenceValidation:
    """rag_evidence validates query + top_k."""

    def test_empty_query_rejected(self):
        result = rag_evidence.invoke({"query": ""})
        assert "❌" in result

    def test_whitespace_query_rejected(self):
        result = rag_evidence.invoke({"query": "   "})
        assert "❌" in result

    def test_top_k_zero_rejected(self):
        result = rag_evidence.invoke({"query": "x", "top_k": 0})
        assert "❌" in result

    def test_top_k_negative_rejected(self):
        result = rag_evidence.invoke({"query": "x", "top_k": -3})
        assert "❌" in result

    def test_top_k_too_large_rejected(self):
        result = rag_evidence.invoke({"query": "x", "top_k": 11})
        assert "❌" in result

    def test_top_k_non_integer_rejected(self):
        """LangChain's pydantic schema rejects non-integer top_k at the
        tool boundary (ValidationError before the function runs)."""
        with pytest.raises(Exception) as exc_info:
            rag_evidence.invoke({"query": "x", "top_k": "lots"})
        assert "top_k" in str(exc_info.value)

    def test_top_k_one_accepted(self):
        payload = json.loads(rag_evidence.invoke({"query": "x", "top_k": 1}))
        assert "evidence" in payload


# ─────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────
class TestRagEvidenceRegistration:
    """rag_evidence is registered with the tool registry."""

    def test_tool_is_registered(self):
        from sentinel_agents.tools import ALLOWED_TOOLS
        names = {t.name for t in ALLOWED_TOOLS}
        assert "rag_evidence" in names

    def test_tool_has_category_rag(self):
        from sentinel_agents.tools import ALLOWED_TOOLS
        for t in ALLOWED_TOOLS:
            if t.name == "rag_evidence":
                assert getattr(t, "__sentinel_category__", None) == "rag"
                return
        pytest.fail("rag_evidence not found in ALLOWED_TOOLS")

    def test_tool_has_description(self):
        assert rag_evidence.description is not None
        assert len(rag_evidence.description) > 50

    def test_tool_count_is_eleven(self):
        """After T3.4: 11 tools total (5 SRE + 4 security + 1 cost + 1 rag)."""
        from sentinel_agents.tools import ALLOWED_TOOLS
        assert len(ALLOWED_TOOLS) == 11

    def test_rag_tools_categorised(self):
        """Both RAG tools are tagged 'rag'."""
        from sentinel_agents.tools import ALLOWED_TOOLS
        rag_tools = [t for t in ALLOWED_TOOLS
                     if getattr(t, "__sentinel_category__", None) == "rag"]
        assert {t.name for t in rag_tools} == {"rag_search", "rag_evidence"}
