"""Tests for shared sparse vector utilities (T1.7 support)."""

from __future__ import annotations

import zlib

from sentinel_rag.sparse import sparse_query_vector, token_hash, tokenize

# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_lowercase_and_alphanumeric(self) -> None:
        assert tokenize("Hello, World! 123 foo-bar_baz") == [
            "hello",
            "world",
            "123",
            "foo",
            "bar",
            "baz",
        ]

    def test_empty_string(self) -> None:
        assert tokenize("") == []

    def test_whitespace_only(self) -> None:
        assert tokenize("   \n\t  ") == []

    def test_numbers(self) -> None:
        assert tokenize("42 is the answer to version 3.14") == [
            "42",
            "is",
            "the",
            "answer",
            "to",
            "version",
            "3",
            "14",
        ]

    def test_single_token(self) -> None:
        assert tokenize("hello") == ["hello"]

    def test_matches_ingest_tokenization(self) -> None:
        """Must produce identical output to SparseEncoder._tokenize."""
        from sentinel_rag.ingest import SparseEncoder

        cases = [
            "hello world",
            "def hello():\n    return 42",
            "# Markdown heading with **bold** and `code`",
            "foo-bar_baz 123.456 Q1_K",
        ]
        for text in cases:
            assert tokenize(text) == SparseEncoder._tokenize(text)


# ---------------------------------------------------------------------------
# token_hash
# ---------------------------------------------------------------------------


class TestTokenHash:
    def test_deterministic(self) -> None:
        a = token_hash("hello")
        b = token_hash("hello")
        assert a == b
        assert isinstance(a, int)
        assert a >= 0

    def test_different_tokens_different_hashes(self) -> None:
        # Hashes may collide probabilistically, but for these distinct
        # strings they are overwhelmingly likely to differ.
        assert token_hash("hello") != token_hash("world")
        assert token_hash("foo") != token_hash("bar")

    def test_31_bit_positive(self) -> None:
        for token in ["hello", "world", "python", "class", "fn", "x"]:
            h = token_hash(token)
            assert 0 <= h <= 0x7FFFFFFF, f"hash({token!r}) = {h} out of 31-bit range"

    def test_matches_crc32(self) -> None:
        """Verify the implementation uses CRC32 as documented."""
        expected = zlib.crc32(b"hello") & 0x7FFFFFFF
        assert token_hash("hello") == expected

    def test_case_sensitive(self) -> None:
        """Hashes differ for different casing — tokenizer lowercases first."""
        assert token_hash("Hello") != token_hash("hello")


# ---------------------------------------------------------------------------
# sparse_query_vector
# ---------------------------------------------------------------------------


class TestSparseQueryVector:
    def test_returns_qdrant_format(self) -> None:
        result = sparse_query_vector("hello world")
        assert "indices" in result
        assert "values" in result
        assert isinstance(result["indices"], list)
        assert isinstance(result["values"], list)
        assert len(result["indices"]) == len(result["values"])

    def test_empty_text(self) -> None:
        result = sparse_query_vector("")
        assert result == {"indices": [], "values": []}

    def test_whitespace_only(self) -> None:
        result = sparse_query_vector("   \n\t  ")
        assert result == {"indices": [], "values": []}

    def test_unique_tokens(self) -> None:
        result = sparse_query_vector("hello world foo")
        assert len(result["indices"]) == 3
        assert len(result["values"]) == 3

    def test_duplicate_tokens_summed_tf(self) -> None:
        """'hello hello world' — hello has TF=2/3, world has TF=1/3."""
        result = sparse_query_vector("hello hello world")
        assert len(result["indices"]) == 2  # unique tokens
        # Find the hello entry
        hello_hash = token_hash("hello")
        world_hash = token_hash("world")
        values_by_index = dict(zip(result["indices"], result["values"], strict=True))
        assert abs(values_by_index[hello_hash] - 2 / 3) < 1e-9
        assert abs(values_by_index[world_hash] - 1 / 3) < 1e-9

    def test_indices_are_hash_based(self) -> None:
        result = sparse_query_vector("hello")
        assert result["indices"] == [token_hash("hello")]

    def test_values_sum_to_one(self) -> None:
        """TF values should sum to 1 (normalized by token count)."""
        result = sparse_query_vector("a b c d")
        assert abs(sum(result["values"]) - 1.0) < 1e-9

    def test_no_idf_applied(self) -> None:
        """Query sparse vectors use TF only (no IDF)."""
        # 'the' repeated should have higher TF, and values reflect that
        result = sparse_query_vector("the the the rare")
        assert len(result["indices"]) == 2
        values_by_index = dict(zip(result["indices"], result["values"], strict=True))
        the_val = values_by_index[token_hash("the")]
        rare_val = values_by_index[token_hash("rare")]
        # 'the' has 3/4, 'rare' has 1/4 — no corpus IDF weighting
        assert the_val > rare_val
