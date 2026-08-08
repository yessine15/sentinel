"""CVE lookup — query the OSV.dev API for a single CVE (T3.2).

OSV.dev (https://osv.dev) is a free, public, no-auth API run by Google +
OSS-Fuzz that aggregates vulnerabilities from many sources (CVE, GHSA,
PYSEC, etc.).  We use it instead of the NVD API because it requires no
API key and returns clean JSON.

This tool is **read-only** and has a strict input validator that only
accepts canonical ``CVE-YYYY-NNNN`` identifiers (see
:func:`validate_cve_lookup`).
"""

from __future__ import annotations

import os
from urllib.parse import quote

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedQueryError,
    _httpx_get_json,
    is_stub,
    register,
)
from sentinel_agents.tools.base import validate_cve_lookup as _validate

# OSV.dev batch + single-vuln endpoints.  No API key required.
_DEFAULT_OSV_URL = os.environ.get("OSV_API_URL", "https://api.osv.dev/v1")


def _summarise(payload: dict) -> str:
    """Render an OSV vulnerability payload as an agent-friendly string."""
    if "error" in payload:
        return f"❌ CVE lookup failed: {payload['error']}"

    vuln = payload.get("vuln") or payload  # OSV returns the vuln directly
    cve_id = vuln.get("id", "?")
    summary = vuln.get("summary") or vuln.get("details", "")[:300] or "(no summary)"
    severity_list = vuln.get("severity") or []
    sev_str = ", ".join(
        f"{s.get('type', '?')}:{s.get('score', '?')}" for s in severity_list
    ) or "UNSPECIFIED"

    affected = vuln.get("affected") or []
    pkg_lines: list[str] = []
    for a in affected[:10]:
        pkg = (a.get("package") or {}).get("name", "?")
        ecosystem = (a.get("package") or {}).get("ecosystem", "?")
        ranges = a.get("ranges") or []
        fixed = "?"
        for rng in ranges:
            for ev in rng.get("events", []):
                if "fixed" in ev:
                    fixed = ev["fixed"]
        pkg_lines.append(f"  - {pkg} ({ecosystem})  fixed in: {fixed}")

    refs = vuln.get("references") or []
    ref_lines = [f"  - {r.get('type', '?')}: {r.get('url', '')}" for r in refs[:5]]

    return (
        f"CVE: {cve_id}\n"
        f"Severity: {sev_str}\n"
        f"Summary: {summary}\n"
        f"Affected packages ({len(affected)}):\n" + "\n".join(pkg_lines) +
        ("\nReferences:\n" + "\n".join(ref_lines) if ref_lines else "")
    )


@tool
def cve_lookup(cve_id: str) -> str:
    """Look up a single CVE by its identifier and return a summary.

    Uses the public OSV.dev API (no key required).  The id MUST be in
    the canonical ``CVE-YYYY-NNNN`` form — anything else is rejected
    before any network call.

    Args:
        cve_id: A CVE identifier like ``CVE-2024-12345``.

    Returns:
        A human-readable summary: severity, summary, affected packages
        + fixed versions, and reference URLs.  In stub mode (no network),
        returns a synthetic payload so the Security Agent can still be
        exercised end-to-end.
    """
    # ── Allow-list enforcement ──
    try:
        _validate(cve_id)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    cid = cve_id.strip().upper()

    # Stub path — safe for unit tests
    if is_stub():
        return (
            f"[T3.2 STUB] Would query OSV.dev for: {cid}\n"
            f"  URL: {_DEFAULT_OSV_URL}/vulns/{quote(cid)}\n"
            f"--- stub payload ---\n"
            f'{{"id":"{cid}","summary":"(stub) demo vulnerability",'
            f'"severity":[{{"type":"CVSS_V3","score":"7.5"}}],'
            f'"affected":[{{"package":{{"name":"pkg-x","ecosystem":"PyPI"}},'
            f'"ranges":[{{"events":[{{"introduced":"0"}},{{"fixed":"1.2.0"}}]}}]}}]}}'
        )

    url = f"{_DEFAULT_OSV_URL}/vulns/{quote(cid)}"
    payload = _httpx_get_json(url, timeout=15)
    # OSV returns the vuln object directly; wrap for _summarise uniformity.
    if isinstance(payload, dict) and "id" in payload:
        payload = {"vuln": payload}

    return _summarise(payload)


register(cve_lookup, category="security")
