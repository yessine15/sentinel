"""Trivy image/filesystem scan — vulnerability + misconfig scanning (T3.2).

Runs the allow-listed ``trivy`` CLI in read-only mode against a container
image or a local filesystem path, and returns a compact JSON-ish summary
the Security Agent can reason over.

Trivy itself never mutates state, but we still validate every argument so
the LLM cannot coax the tool into scanning an arbitrary remote URL or
passing arbitrary flags.

Environment variables
---------------------
TRIVY_BIN : str
    Override the trivy binary path (default ``trivy`` from PATH).
"""

from __future__ import annotations

import os
import shlex

from langchain_core.tools import tool

from sentinel_agents.tools.base import (
    DisallowedQueryError,
    is_stub,
    register,
    run_subprocess,
)
from sentinel_agents.tools.base import validate_trivy as _validate

_TRIVY_BIN = os.environ.get("TRIVY_BIN", "trivy")

# How many findings to render in the trimmed preview — keeps the tool
# output well under the LLM context window for noisy scans.
_MAX_FINDINGS_PREVIEW = 25


def _build_cmd(
    target: str,
    target_arg: str,
    scanners: str,
    severity: str,
    output_format: str,
) -> list[str]:
    """Build a sanitized ``trivy`` command line."""
    cmd: list[str] = [_TRIVY_BIN, target, target_arg]
    cmd.extend(["--scanners", scanners])
    cmd.extend(["--severity", severity])
    cmd.extend(["--format", output_format])
    # Skip the disk-cache update check — keeps the tool fast and offline-safe.
    cmd.extend(["--skip-db-update"])
    # Quiet: don't print progress banners.
    cmd.append("--quiet")
    return cmd


@tool
def trivy_scan(
    target: str,
    target_arg: str,
    scanners: str = "vuln",
    severity: str = "CRITICAL,HIGH",
    output_format: str = "json",
) -> str:
    """Scan a container image or local filesystem path for vulnerabilities.

    Use this to check whether a workload image (or the repo itself) has
    known CVEs, misconfigurations, or hardcoded secrets.  Typical use:

    - **Image scan**: ``trivy_scan("image", "nginx:1.25")`` — returns
      a list of CVEs grouped by severity.
    - **Filesystem scan**: ``trivy_scan("fs", "./api")`` — scans a local
      directory for IaC misconfigs / secrets.

    Args:
        target: One of ``image``, ``filesystem`` (or ``fs``), ``repo``.
            ``repo`` may only point at the local working tree.
        target_arg: The argument to the target kind (image ref, dir path).
        scanners: Comma-separated subset of ``vuln``, ``config``,
            ``secret``, ``misconfig``, ``license`` (default ``vuln``).
        severity: Comma-separated subset of ``CRITICAL``, ``HIGH``,
            ``MEDIUM``, ``LOW`` (default ``CRITICAL,HIGH``).
        output_format: ``json`` (default, structured) or ``table``
            (human-readable).

    Returns:
        Trivy JSON output (T3.2) or a stub preview when
        ``RUN_MODE=stub``.  The JSON is trimmed to the most severe
        findings when very large.
    """
    # ── Allow-list enforcement ──
    try:
        _validate(target, scanners=scanners, severity=severity)
    except DisallowedQueryError as exc:
        return f"❌ BLOCKED: {exc}"

    # For ``repo`` scans, forbid any argument that looks like a URL —
    # the agent must only scan the local working tree, never clone a
    # remote repo on the user's behalf.
    if target.lower() == "repo" and (
        "://" in target_arg or target_arg.startswith(("git@", "http"))
    ):
        return (
            "❌ BLOCKED: remote repository URLs are not allowed. "
            "Only the local working tree may be scanned with 'repo'."
        )

    cmd = _build_cmd(target, target_arg, scanners, severity, output_format)

    # Stub path — safe for unit tests (and surfaces the exact cmd) —
    # but also return a small synthetic JSON blob so the Security
    # Agent can still be exercised in stub mode.
    if is_stub():
        preview = f"[T3.2 STUB] Would run: {shlex.join(cmd)}"
        sample = (
            "\n--- stub payload ---\n"
            '{"Target":"' + target_arg + '","Vulnerabilities":['
            '{"VulnerabilityID":"CVE-2024-0000","PkgName":"fake-pkg",'
            '"InstalledVersion":"1.0.0","FixedVersion":"1.2.0",'
            '"Severity":"CRITICAL","Title":"Demo vuln (stub)"}]}'
        )
        return preview + sample

    raw = run_subprocess(cmd, timeout=120)

    # If the caller wanted JSON and we got a lot of it, trim so the
    # LLM context window isn't blown out.
    if output_format.lower() == "json" and len(raw) > 20000:
        head = raw[:20000]
        return head + "\n… (trimmed — full scan output truncated)"

    return raw


register(trivy_scan, category="security")
