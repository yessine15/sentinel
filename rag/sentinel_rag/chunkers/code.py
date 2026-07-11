"""AST-aware code chunker using tree-sitter.

Splits source-code documents at function / class / method boundaries so that
a chunk **never** cuts mid-function.  Exact line ranges come from AST node
positions, giving precise ``path:line_start-line_end`` citations.

Supported languages (by ``metadata["language"]`` on the input Document):

===========  ================================  ==============================
Language     Grammar module                     Boundary node types
===========  ================================  ==============================
python       ``tree_sitter_python``             ``function_definition``,
                                                ``class_definition``,
                                                ``decorated_definition``
go           ``tree_sitter_go``                 ``function_declaration``,
                                                ``method_declaration``,
                                                ``type_declaration``
typescript   ``tree_sitter_typescript`` (TSX)   ``function_declaration``,
javascript   ``tree_sitter_javascript`` (JSX)   ``class_declaration``,
                                                ``method_definition``,
                                                ``export_statement``,
                                                ``lexical_declaration``
                                                (top-level arrow / const fn)
===========  ================================  ==============================

Unsupported languages fall back to blank-line-based chunking with a line cap.

Run standalone:

    python -m sentinel_rag.chunkers.code <dir> [dir ...]

Loads all source files via ``CodeConnector`` and prints the resulting chunks.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from sentinel_rag.chunkers.base import Chunk, Chunker, print_chunks
from sentinel_rag.sources.code import CodeConnector

if TYPE_CHECKING:
    from sentinel_rag.sources.base import Document

# --------------------------------------------------------------------------- #
# Grammar registry  (lazy-loaded on first use)
# --------------------------------------------------------------------------- #
_GRAMMAR_CACHE: dict[str, tree_sitter.Language] = {}  # noqa: F821


def _get_language(name: str) -> tree_sitter.Language | None:  # noqa: F821
    """Return a tree-sitter Language for *name*, or None if unavailable."""
    import tree_sitter  # lazy — heavy import

    if name in _GRAMMAR_CACHE:
        return _GRAMMAR_CACHE[name]

    grammar_map = {
        "python": "tree_sitter_python",
        "go": "tree_sitter_go",
        "typescript": "tree_sitter_typescript",
        "javascript": "tree_sitter_javascript",
    }

    mod_name = grammar_map.get(name)
    if mod_name is None:
        return None

    try:
        mod = __import__(mod_name, fromlist=["language"])
        lang = tree_sitter.Language(mod.language())
        _GRAMMAR_CACHE[name] = lang
        return lang
    except (ImportError, TypeError) as exc:
        print(f"Warning: could not load tree-sitter grammar for '{name}': {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# Boundary node types per language
# --------------------------------------------------------------------------- #
_BOUNDARY_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset(
        {
            "function_definition",
            "class_definition",
            "decorated_definition",
        }
    ),
    "go": frozenset(
        {
            "function_declaration",
            "method_declaration",
            "type_declaration",
        }
    ),
    "typescript": frozenset(
        {
            "function_declaration",
            "class_declaration",
            "method_definition",
            "export_statement",
            "lexical_declaration",
        }
    ),
    "javascript": frozenset(
        {
            "function_declaration",
            "class_declaration",
            "method_definition",
            "export_statement",
            "lexical_declaration",
        }
    ),
}


# --------------------------------------------------------------------------- #
# CodeChunker
# --------------------------------------------------------------------------- #
class CodeChunker(Chunker):
    """AST-aware chunker that splits code at function/class/method boundaries.

    Parameters:
        max_chunk_lines: If a single AST node exceeds this many lines, it is
            split further into sub-chunks at blank-line boundaries (preserving
            the guarantee that we never cut mid-line-group).
    """

    source_type = "code"

    def __init__(self, max_chunk_lines: int = 150) -> None:
        if max_chunk_lines < 1:
            raise ValueError("max_chunk_lines must be >= 1")
        self.max_chunk_lines = max_chunk_lines

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def chunk(self, document: Document) -> list[Chunk]:
        """Split *document* into AST-aware chunks."""
        if not document.text.strip():
            return []

        language = document.metadata.get("language", "unknown")

        # Try AST-aware chunking first.
        tree_lang = _get_language(language)
        if tree_lang is not None:
            try:
                return self._chunk_ast(document, tree_lang, language)
            except Exception as exc:
                print(
                    f"Warning: tree-sitter parsing failed for {document.path} "
                    f"({language}): {exc}. Falling back to line-based.",
                    file=sys.stderr,
                )

        # Fallback: line-based chunking for unsupported or failed languages.
        return self._chunk_lines(document)

    # ------------------------------------------------------------------ #
    # AST-aware chunking
    # ------------------------------------------------------------------ #
    def _chunk_ast(
        self,
        document: Document,
        tree_lang: tree_sitter.Language,  # noqa: F821
        language: str,
    ) -> list[Chunk]:
        """Parse *document* and split at AST boundary nodes."""
        import tree_sitter  # lazy

        parser = tree_sitter.Parser(tree_lang)
        code_bytes = document.text.encode("utf-8")
        tree = parser.parse(code_bytes)
        lines = document.text.splitlines()

        boundary_types = _BOUNDARY_TYPES.get(language, frozenset())

        # Collect all top-level boundary nodes.
        boundary_nodes = _collect_boundary_nodes(tree.root_node, boundary_types)

        if not boundary_nodes:
            # No boundaries found — treat the whole file as one chunk.
            return [
                self._make_chunk(
                    document=document,
                    lines=lines,
                    first=1,
                    last=len(lines),
                    metadata={"language": language, "node_type": "file"},
                    chunk_idx=0,
                )
            ]

        # Build chunks, splitting oversized ones.
        chunks: list[Chunk] = []
        chunk_idx = 0

        for node in boundary_nodes:
            first = node.start_point.row + 1
            last = node.end_point.row + 1
            node_lines = last - first + 1

            if node_lines > self.max_chunk_lines:
                # Split oversized node at blank-line boundaries.
                sub_chunks = self._split_oversized(
                    document, lines, first, last, language, node.type
                )
                for sc in sub_chunks:
                    chunks.append(
                        Chunk(
                            chunk_id=f"{document.doc_id}:c{chunk_idx}",
                            parent_doc_id=document.doc_id,
                            source_type=document.source_type,
                            path=document.path,
                            line_start=sc[0],
                            line_end=sc[1],
                            text="\n".join(lines[sc[0] - 1 : sc[1]]),
                            metadata={
                                **document.metadata,
                                "chunk_index": str(chunk_idx),
                                "language": language,
                                "node_type": node.type,
                            },
                        )
                    )
                    chunk_idx += 1
            else:
                text = "\n".join(lines[first - 1 : last])
                chunks.append(
                    self._make_chunk(
                        document=document,
                        lines=lines,
                        first=first,
                        last=last,
                        metadata={
                            "language": language,
                            "node_type": node.type,
                        },
                        chunk_idx=chunk_idx,
                        text_override=text,
                    )
                )
                chunk_idx += 1

        return chunks

    # ------------------------------------------------------------------ #
    # Line-based fallback
    # ------------------------------------------------------------------ #
    def _chunk_lines(self, document: Document) -> list[Chunk]:
        """Fallback chunker: split at blank-line boundaries, capped by
        ``max_chunk_lines``."""
        lines = document.text.splitlines()
        if not lines or all(not ln.strip() for ln in lines):
            return []

        chunks: list[Chunk] = []
        chunk_idx = 0
        start = 1

        for i in range(len(lines)):
            line_num = i + 1
            is_blank = not lines[i].strip()
            oversize = (line_num - start + 1) >= self.max_chunk_lines

            if (is_blank or oversize) and line_num > start:
                end = line_num - 1 if is_blank else line_num
                chunks.append(self._make_chunk(document, lines, start, end, {}, chunk_idx))
                chunk_idx += 1
                start = line_num + 1 if is_blank else line_num

        # Trailing lines
        if start <= len(lines):
            chunks.append(self._make_chunk(document, lines, start, len(lines), {}, chunk_idx))

        return chunks

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _make_chunk(
        document: Document,
        lines: list[str],
        first: int,
        last: int,
        metadata: dict[str, str],
        chunk_idx: int,
        text_override: str | None = None,
    ) -> Chunk:
        """Build a Chunk from line range *first*-*last* (1-indexed, inclusive)."""
        text = text_override if text_override is not None else "\n".join(lines[first - 1 : last])
        return Chunk(
            chunk_id=f"{document.doc_id}:c{chunk_idx}",
            parent_doc_id=document.doc_id,
            source_type=document.source_type,
            path=document.path,
            line_start=first,
            line_end=last,
            text=text,
            metadata={
                **document.metadata,
                "chunk_index": str(chunk_idx),
                **metadata,
            },
        )

    @staticmethod
    def _split_oversized(
        document: Document,
        lines: list[str],
        first: int,
        last: int,
        language: str,
        node_type: str,
    ) -> list[tuple[int, int]]:
        """Split an oversized node into sub-ranges at blank-line boundaries.

        Returns a list of ``(first_line, last_line)`` tuples (1-indexed,
        inclusive).
        """
        sub_ranges: list[tuple[int, int]] = []
        sub_start = first
        for i in range(first - 1, last):
            line_num = i + 1
            if not lines[i].strip() and line_num > sub_start:
                sub_ranges.append((sub_start, line_num - 1))
                sub_start = line_num + 1
        if sub_start <= last:
            sub_ranges.append((sub_start, last))
        return sub_ranges


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _collect_boundary_nodes(
    node: tree_sitter.Node,  # noqa: F821
    boundary_types: frozenset[str],
) -> list[tree_sitter.Node]:  # noqa: F821
    """Walk *node* recursively, collecting direct children whose type is in
    *boundary_types*.

    We only collect **top-level** boundary nodes — we do not recurse into
    them, because the whole function/class should be one chunk (unless
    oversized).
    """
    result: list[tree_sitter.Node] = []  # noqa: F821
    for child in node.children:
        if child.type in boundary_types:
            result.append(child)
        else:
            result.extend(_collect_boundary_nodes(child, boundary_types))
    return result


# --------------------------------------------------------------------------- #
# Standalone CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m sentinel_rag.chunkers.code <dir> [dir ...]")
        sys.exit(1)

    roots = sys.argv[1:]
    chunker = CodeChunker()
    docs = CodeConnector(*roots).load()
    all_chunks: list[Chunk] = []
    for d in docs:
        all_chunks.extend(chunker.chunk(d))
    print_chunks(all_chunks)
