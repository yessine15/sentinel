"""Source-code connector.

Walks a directory tree and returns one ``Document`` per recognised source
file. We deliberately keep the scope narrow for T1.3: AST-aware splitting at
function/class boundaries is the job of the T1.4 code chunker — here we just
identify *which* files are source code and load their text with exact line
ranges.

Recognised file types (by extension) and their ``language`` metadata:

    .py        → python
    .go        → go
    .ts .tsx   → typescript
    .js .jsx   → javascript
    .yaml .yml → yaml
    .hcl .tf   → hcl
    .sh        → shell

Symlink loops and binary files are skipped; common noise directories
(``.git``, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, `.next`,
`.tox`) are pruned.

Run standalone:

    python -m sentinel_rag.sources.code ./api ./rag

prints a preview of every discovered code file.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sentinel_rag.sources.base import Document, SourceConnector, print_documents

# Extension → language mapping.
_LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".hcl": "hcl",
    ".tf": "hcl",
    ".sh": "shell",
}

# Directories we never descend into.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".next",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "charts",  # helm chart deps — vendored upstream code
    }
)

# First-byte sniff for binary files (NUL or high proportion of non-text bytes).
_BINARY_SNIFF_BYTES = 4096


class CodeConnector(SourceConnector):
    """Load source-code files from one or more root directories."""

    source_type = "code"

    def __init__(self, *roots: str | Path) -> None:
        if not roots:
            raise ValueError("CodeConnector requires at least one root path")
        self.roots = [Path(r) for r in roots]

    def load(self) -> list[Document]:
        docs: list[Document] = []
        for root in self.roots:
            if not root.exists():
                continue
            for path in sorted(_walk_source_files(root)):
                text = path.read_text(encoding="utf-8")
                ext = path.suffix.lower()
                language = _LANGUAGE_BY_EXT.get(ext, "unknown")
                rel = _relative_to_any(path, self.roots)
                docs.append(
                    Document(
                        doc_id=f"code:{rel}",
                        source_type=self.source_type,
                        path=str(rel),
                        line_start=1,
                        line_end=len(text.splitlines()) or 1,
                        text=text,
                        metadata={
                            "language": language,
                            "ext": ext,
                            "char_count": str(len(text)),
                        },
                    )
                )
        return docs


def _walk_source_files(root: Path):
    """Yield source file paths, pruning noise dirs and binary files."""
    for dirpath, dirnames, filenames in root.walk():
        # Mutate dirnames in place to prune skipped directories.
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            path = dirpath / name
            ext = path.suffix.lower()
            if ext not in _LANGUAGE_BY_EXT:
                continue
            if _is_binary(path):
                continue
            yield path


def _is_binary(path: Path) -> bool:
    """Quick sniff: return True if the file looks binary."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    # Heuristic: if >30% of the sniffed bytes are non-text control chars,
    # treat as binary.
    if not chunk:
        return False
    text_chars = sum(1 for b in chunk if b in (9, 10, 13) or 32 <= b < 127)
    return (text_chars / len(chunk)) < 0.70


def _relative_to_any(path: Path, roots: list[Path]) -> Path:
    """Return ``path`` relative to the first root that contains it.

    Falls back to the path itself (absolute) if none match.
    """
    for root in roots:
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    return path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            "usage: python -m sentinel_rag.sources.code <root_dir> [root_dir ...]",
            file=sys.stderr,
        )
        return 2
    docs = CodeConnector(*args).load()
    print_documents(docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
