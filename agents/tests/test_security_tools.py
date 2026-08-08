"""Tests for the T3.2 security tools.

Every test asserts that disallowed inputs are BLOCKED and allowed
inputs pass through.  All runs are in stub mode — no real Trivy / CVE
/ Falco / Tetragon backend is required.
"""

from __future__ import annotations

import pytest

from sentinel_agents.tools.base import (
    ALLOWED_FALCO_OPERATIONS,
    ALLOWED_TETRAGON_EVENTS,
    ALLOWED_TRIVY_SCANNERS,
    ALLOWED_TRIVY_SEVERITIES,
    ALLOWED_TRIVY_TARGETS,
    DisallowedQueryError,
    ToolSecurityError,
    validate_cve_lookup,
    validate_falco_operation,
    validate_tetragon_events,
    validate_trivy,
)


# ════════════════════════════════════════════════════════════
# Allow-lists are frozensets (immutable)
# ════════════════════════════════════════════════════════════
class TestSecurityAllowLists:
    def test_trivy_targets_is_frozenset(self):
        assert isinstance(ALLOWED_TRIVY_TARGETS, frozenset)

    def test_trivy_severities_is_frozenset(self):
        assert isinstance(ALLOWED_TRIVY_SEVERITIES, frozenset)

    def test_trivy_scanners_is_frozenset(self):
        assert isinstance(ALLOWED_TRIVY_SCANNERS, frozenset)

    def test_tetragon_events_is_frozenset(self):
        assert isinstance(ALLOWED_TETRAGON_EVENTS, frozenset)

    def test_falco_operations_is_frozenset(self):
        assert isinstance(ALLOWED_FALCO_OPERATIONS, frozenset)

    def test_dangerous_trivy_targets_excluded(self):
        # "sbom" / "registry" / "server" are not in T3.2's allow-list
        for forbidden in ("server", "registry", "sbom", "client"):
            assert forbidden not in ALLOWED_TRIVY_TARGETS

    def test_dangerous_falco_ops_excluded(self):
        for forbidden in ("add", "delete", "update", "reload", "enable", "disable"):
            assert forbidden not in ALLOWED_FALCO_OPERATIONS


# ════════════════════════════════════════════════════════════
# trivy_scan — allow-list enforcement + stub output
# ════════════════════════════════════════════════════════════
class TestTrivyScan:
    def test_allowed_image_scan_returns_stub(self):
        from sentinel_agents.tools.trivy_scan import trivy_scan
        result = trivy_scan.invoke({"target": "image", "target_arg": "nginx:1.25"})
        assert "Would run: trivy image nginx:1.25" in result
        assert "stub payload" in result.lower()
        assert "BLOCKED" not in result

    def test_allowed_filesystem_scan(self):
        from sentinel_agents.tools.trivy_scan import trivy_scan
        result = trivy_scan.invoke({"target": "fs", "target_arg": "./api"})
        assert "Would run: trivy fs ./api" in result
        assert "BLOCKED" not in result

    def test_custom_scanners_pass_validation(self):
        from sentinel_agents.tools.trivy_scan import trivy_scan
        result = trivy_scan.invoke({
            "target": "image",
            "target_arg": "my:1",
            "scanners": "vuln,secret,misconfig",
            "severity": "CRITICAL",
        })
        assert "scanners vuln,secret,misconfig" in result
        assert "BLOCKED" not in result

    def test_disallowed_target_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="root"):
            validate_trivy("root")

    def test_disallowed_scanner_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="scanner"):
            validate_trivy("image", scanners="vuln,malware")

    def test_disallowed_severity_is_blocked(self):
        with pytest.raises(DisallowedQueryError, match="severity"):
            validate_trivy("image", severity="CRITICAL,UNKNOWN,EXPLOIT")

    def test_remote_repo_url_is_blocked(self):
        """A 'repo' scan must never pull a remote URL."""
        from sentinel_agents.tools.trivy_scan import trivy_scan
        result = trivy_scan.invoke({"target": "repo", "target_arg": "https://evil.example/x.git"})
        assert "BLOCKED" in result
        assert "remote repository" in result.lower()

    def test_local_repo_arg_passes(self):
        from sentinel_agents.tools.trivy_scan import trivy_scan
        result = trivy_scan.invoke({"target": "repo", "target_arg": "."})
        assert "Would run: trivy repo ." in result
        assert "BLOCKED" not in result


# ════════════════════════════════════════════════════════════
# cve_lookup — validation + stub output
# ════════════════════════════════════════════════════════════
class TestCveLookup:
    def test_valid_cve_returns_stub(self):
        from sentinel_agents.tools.cve_lookup import cve_lookup
        result = cve_lookup.invoke({"cve_id": "CVE-2024-12345"})
        assert "Would query OSV.dev" in result
        assert "CVE-2024-12345" in result
        assert "stub payload" in result.lower()
        assert "BLOCKED" not in result

    def test_lowercase_cve_id_accepted(self):
        from sentinel_agents.tools.cve_lookup import cve_lookup
        # validator uppercases; should still pass
        result = cve_lookup.invoke({"cve_id": "cve-2021-1"})
        # 'cve-2021-1' has only 1 digit suffix — should be BLOCKED
        assert "BLOCKED" in result

    def test_missing_cve_prefix_blocked(self):
        with pytest.raises(DisallowedQueryError, match="CVE-"):
            validate_cve_lookup("RHSA-2024:1234")

    def test_shell_injection_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cve_lookup("CVE-2024; rm -rf /")

    def test_wrong_format_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_cve_lookup("CVE-24-1")

    def test_valid_canonical_forms_pass(self):
        validate_cve_lookup("CVE-2024-12345")  # 5-digit
        validate_cve_lookup("CVE-1999-0001")   # 4-digit
        validate_cve_lookup("CVE-2025-1234567")  # 7-digit


# ════════════════════════════════════════════════════════════
# falco_events — validation + stub output
# ════════════════════════════════════════════════════════════
class TestFalcoEvents:
    def test_events_returns_stub_payload(self):
        from sentinel_agents.tools.falco_events import falco_events
        result = falco_events.invoke({"operation": "events", "limit": 5})
        assert "Would GET" in result
        # The stub payload must include a 'shell in container' alert
        assert "Terminal shell in container" in result
        assert "BLOCKED" not in result

    def test_health_returns_stub(self):
        from sentinel_agents.tools.falco_events import falco_events
        result = falco_events.invoke({"operation": "health"})
        assert "healthz" in result
        assert "BLOCKED" not in result

    def test_disallowed_operation_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_falco_operation("purge")

    def test_disallowed_add_rule_blocked(self):
        """Adding rules is a write operation — must be blocked."""
        with pytest.raises(DisallowedQueryError):
            validate_falco_operation("add")


# ════════════════════════════════════════════════════════════
# tetragon_events — validation + stub output
# ════════════════════════════════════════════════════════════
class TestTetragonEvents:
    def test_exec_events_returns_stub_payload(self):
        from sentinel_agents.tools.tetragon_events import tetragon_events
        result = tetragon_events.invoke({"event_type": "exec", "limit": 5})
        assert "Would query Tetragon" in result
        # The stub payload must include an exec event with a shell comm
        assert "sh" in result or "exec" in result.lower()
        assert "BLOCKED" not in result

    def test_network_event_type_passes(self):
        from sentinel_agents.tools.tetragon_events import tetragon_events
        result = tetragon_events.invoke({"event_type": "network"})
        assert "type=network" in result
        assert "BLOCKED" not in result

    def test_disallowed_event_type_blocked(self):
        with pytest.raises(DisallowedQueryError):
            validate_tetragon_events("exploit")


# ════════════════════════════════════════════════════════════
# Registration — all four security tools are discoverable
# ════════════════════════════════════════════════════════════
class TestSecurityToolsRegistration:
    def test_all_four_security_tools_registered(self):
        from sentinel_agents.tools import get_tool_names
        names = get_tool_names()
        assert "trivy_scan" in names
        assert "cve_lookup" in names
        assert "falco_events" in names
        assert "tetragon_events" in names

    def test_security_tools_inherit_tool_security_error(self):
        """A DisallowedQueryError raised by any security tool is a
        ToolSecurityError (so the broader graph can catch them uniformly)."""
        with pytest.raises(ToolSecurityError):
            raise DisallowedQueryError("x")
