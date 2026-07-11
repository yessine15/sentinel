"""Markdown source connector.

Loads every ``.md`` file under a directory (recursively) and returns one
``Document`` per file. Markdown files are not split here — that is the job of
the T1.4 prose chunker. We keep the full text plus the full line range so
that the chunker can carve it up later.

Run standalone:

    python -m sentinel_rag.sources.markdown ./docs

prints a preview of every discovered markdown document.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sentinel_rag.sources.base import Document, SourceConnector, print_documents


class MarkdownConnector(SourceConnector):
    """Load all ``.md`` files under a directory."""

    source_type = "markdown"

    def __init__(self, root: str | Path, *, suffix: str = ".md") -> None:
        self.root = Path(root)
        self.suffix = suffix

    def load(self) -> list[Document]:
        docs: list[Document] = []
        for path in sorted(self.root.rglob(f"*{self.suffix}")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            # Repo-relative path when possible, else absolute.
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                rel = path
            docs.append(
                Document(
                    doc_id=f"md:{rel}",
                    source_type=self.source_type,
                    path=str(rel),
                    line_start=1,
                    line_end=len(text.splitlines()) or 1,
                    text=text,
                    metadata={
                        "title": _extract_title(text) or path.stem,
                        "char_count": str(len(text)),
                    },
                )
            )
        return docs


def _extract_title(text: str) -> str:
    """Return the first ``# heading`` text, or empty string."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m sentinel_rag.sources.markdown <root_dir>", file=sys.stderr)
        return 2
    docs = MarkdownConnector(args[0]).load()
    print_documents(docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
