"""Runbook source connector.

A **runbook** is a structured markdown document that describes how to respond
to a specific alert or incident type. In Sentinel they live under
``docs/runbooks/`` and follow a small front-matter convention::

    ---
    title: Pod CrashLoopBackOff
    alert: PodCrashLoopBackOff
    severity: warning
    owner: sre
    ---
    # Runbook: Pod CrashLoopBackOff
    ...

This connector loads every ``.md`` file under the runbooks directory, parses
the YAML front matter (without depending on a YAML library — we keep the
dependency surface minimal), and attaches the parsed fields as document
metadata. The body becomes ``text``.

Run standalone:

    python -m sentinel_rag.sources.runbook ./docs/runbooks

prints a preview of every discovered runbook.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from sentinel_rag.sources.base import Document, SourceConnector, print_documents

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


class RunbookConnector(SourceConnector):
    """Load runbook markdown files from a directory."""

    source_type = "runbook"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self) -> list[Document]:
        docs: list[Document] = []
        if not self.root.is_dir():
            return docs
        for path in sorted(self.root.rglob("*.md")):
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8")
            front, body = _split_front_matter(raw)
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                rel = path
            metadata: dict[str, str] = {
                "title": front.get("title", path.stem),
                "char_count": str(len(body)),
            }
            for key in ("alert", "severity", "owner"):
                if key in front:
                    metadata[key] = front[key]
            docs.append(
                Document(
                    doc_id=f"runbook:{rel}",
                    source_type=self.source_type,
                    path=str(rel),
                    line_start=1,
                    line_end=len(body.splitlines()) or 1,
                    text=body,
                    metadata=metadata,
                )
            )
        return docs


def _split_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """Split ``---\\nkey: val\\n---\\n<body>`` into (front, body).

    A tiny hand-rolled parser: we don't want a PyYAML dependency in the
    connector layer. Supports ``key: value`` lines and ignores blank lines.
    """
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    block = match.group(1)
    body = raw[match.end() :]
    front: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        front[key.strip()] = value.strip()
    return front, body


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            "usage: python -m sentinel_rag.sources.runbook <runbooks_dir>",
            file=sys.stderr,
        )
        return 2
    docs = RunbookConnector(args[0]).load()
    print_documents(docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
