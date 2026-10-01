"""T-074: heading-aware chunker with overlap + `chunk_config_hash`.

AC: FR-012/AC-001 (exact heading_path for citations), FR-012/AC-003 (config change => re-chunk).
"""

from __future__ import annotations

import pytest
from mcp_ingest.pipeline.chunk import ChunkConfig, chunk_text, embed_input

DOC = """# Payments

Intro paragraph of the payments handbook.

## Retry policy

Failed transactions are retried three times.

### Backoff

The delay doubles after every attempt.

## Dead letter queue

After the last retry the message is parked.
"""


def test_FR_012_AC_001_heading_path_is_exact_on_a_multi_level_document() -> None:
    chunks = chunk_text(DOC, ChunkConfig(64, 8))
    paths = [c.heading_path for c in chunks]
    assert paths == [
        "Payments",
        "Payments > Retry policy",
        "Payments > Retry policy > Backoff",
        "Payments > Dead letter queue",
    ]
    assert [c.index for c in chunks] == [0, 1, 2, 3]
    assert "doubles after every attempt" in chunks[2].content
    assert all(c.token_count == len(c.content.split()) for c in chunks)


def test_a_heading_inside_a_code_fence_is_not_a_heading() -> None:
    text = "# Deploy\n\n```bash\n# not a heading\nmake release\n```\n"
    chunks = chunk_text(text, ChunkConfig(64, 8))
    assert len(chunks) == 1 and chunks[0].heading_path == "Deploy"
    assert "# not a heading" in chunks[0].content


def test_long_sections_are_split_with_overlap_and_stay_under_budget() -> None:
    words = " ".join(f"w{i}" for i in range(200))
    config = ChunkConfig(50, 10)
    chunks = chunk_text(f"# Long\n\n{words}", config)
    assert len(chunks) > 3
    assert all(c.token_count <= config.tokens + config.overlap for c in chunks)
    assert all(c.heading_path == "Long" for c in chunks)
    first_tail = chunks[0].content.split()[-config.overlap :]
    assert chunks[1].content.split()[: config.overlap] == first_tail  # overlap is real


def test_paragraphs_are_packed_not_cut_when_they_fit() -> None:
    text = "# A\n\none two three\n\nfour five six\n\nseven eight nine"
    chunks = chunk_text(text, ChunkConfig(64, 8))
    assert len(chunks) == 1 and chunks[0].content.count("\n\n") == 2


def test_a_single_oversized_line_is_cut_by_words() -> None:
    chunks = chunk_text(" ".join(f"x{i}" for i in range(300)), ChunkConfig(40, 0))
    assert len(chunks) == 8 and chunks[0].heading_path is None


def test_empty_and_heading_only_documents_produce_no_chunks() -> None:
    assert chunk_text("", ChunkConfig(64, 8)) == []
    assert chunk_text("# Only a heading\n\n## and another", ChunkConfig(64, 8)) == []


def test_FR_012_AC_003_changing_size_or_overlap_changes_the_config_hash() -> None:
    base = ChunkConfig(256, 32)
    assert base.hash == ChunkConfig(256, 32).hash
    assert base.hash != ChunkConfig(128, 32).hash
    assert base.hash != ChunkConfig(256, 16).hash


@pytest.mark.parametrize("bad", [(4, 0), (64, 64), (64, -1)])
def test_invalid_configs_are_rejected(bad: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        ChunkConfig(*bad)


def test_embed_input_prefixes_the_heading_path_for_reembed_parity() -> None:
    assert embed_input("A > B", "body") == "A > B\n\nbody"
    assert embed_input(None, "body") == "body"
