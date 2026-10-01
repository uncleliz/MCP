"""Stage `chunk` (T-074): heading-aware chunker with overlap.

* `heading_path` ("Parent > Child") is exact: it is the stack of Markdown headings above the chunk
  (headings inside code fences are not headings), which is what makes a citation point at the
  right section.
* Size is measured in whitespace-separated words (`token_count`) — a model-independent, cheap
  approximation; `MCP_INGEST_CHUNK_TOKENS` must stay below the embedding model's
  `max_input_tokens`.
* `ChunkConfig.hash` goes into `kb.documents.chunk_config_hash`: changing size/overlap changes
  the hash, so existing documents are re-chunked instead of skipped (FR-012/AC-003).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

__all__ = ["Chunk", "ChunkConfig", "chunk_text", "embed_input"]

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_CHUNKER_VERSION = 1


@dataclass(frozen=True)
class ChunkConfig:
    tokens: int = 256
    overlap: int = 32

    def __post_init__(self) -> None:
        if self.tokens < 8:
            raise ValueError("chunk size must be at least 8 tokens")
        if not 0 <= self.overlap < self.tokens:
            raise ValueError("chunk overlap must be >= 0 and smaller than the chunk size")

    @property
    def hash(self) -> str:
        payload = json.dumps(
            {"v": _CHUNKER_VERSION, "tokens": self.tokens, "overlap": self.overlap},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    heading_path: str | None
    token_count: int


def embed_input(heading_path: str | None, content: str) -> str:
    """Exactly what is embedded for a stored chunk (shared with `reembed`, so both agree)."""
    return f"{heading_path}\n\n{content}" if heading_path else content


def _sections(text: str) -> list[tuple[str | None, list[str]]]:
    """[(heading_path, blocks)] where blocks are paragraphs / whole code fences."""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str | None, list[str]]] = []
    path: str | None = None
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush_paragraph() -> None:
        if current:
            blocks.append("\n".join(current).strip("\n"))
            current.clear()

    def flush_section() -> None:
        flush_paragraph()
        if any(b.strip() for b in blocks):
            sections.append((path, [b for b in blocks if b.strip()]))
        blocks.clear()

    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            current.append(line)
            continue
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            flush_section()
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            path = " > ".join(title for _, title in stack)
        elif not in_fence and not line.strip():
            flush_paragraph()
        else:
            current.append(line)
    flush_section()
    return sections


def _split_oversize(block: str, limit: int) -> list[str]:
    if len(block.split()) <= limit:
        return [block]
    pieces: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in block.split("\n"):
        words = len(line.split())
        if words > limit:  # a single huge line: cut by words
            if buffer:
                pieces.append("\n".join(buffer))
                buffer, size = [], 0
            tokens = line.split()
            pieces.extend(" ".join(tokens[i : i + limit]) for i in range(0, len(tokens), limit))
            continue
        if size + words > limit and buffer:
            pieces.append("\n".join(buffer))
            buffer, size = [], 0
        buffer.append(line)
        size += words
    if buffer:
        pieces.append("\n".join(buffer))
    return pieces


def _pack(
    path: str | None, pieces: list[str], config: ChunkConfig, start_index: int
) -> list[Chunk]:
    """Greedy packing of one section's pieces; each chunk after the first starts with the tail
    words of the previous chunk (overlap)."""
    out: list[Chunk] = []
    current: list[str] = []
    size = 0
    carry = ""

    def make(group: list[str], overlap_from: str) -> tuple[Chunk | None, str]:
        body = "\n\n".join(group).strip()
        if not body:
            return None, overlap_from
        content = f"{overlap_from}\n\n{body}".strip() if overlap_from else body
        tail = body.split()[-config.overlap :] if config.overlap else []
        return Chunk(start_index + len(out), content, path, len(content.split())), " ".join(tail)

    for piece in pieces:
        words = len(piece.split())
        carried = len(carry.split()) if carry else 0
        if current and carried + size + words > config.tokens:
            chunk, carry = make(current, carry)
            if chunk is not None:
                out.append(chunk)
            current, size = [], 0
        current.append(piece)
        size += words
    chunk, carry = make(current, carry)
    if chunk is not None:
        out.append(chunk)
    return out


def chunk_text(text: str, config: ChunkConfig) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, blocks in _sections(text):
        pieces = [p for block in blocks for p in _split_oversize(block, config.tokens)]
        chunks.extend(_pack(path, pieces, config, len(chunks)))
    return chunks
