"""Header-aware Markdown chunker (HIPPO-E02-F1-S01).

Splits on ``#``/``##``/``###`` boundaries (outside fenced code), keeps the
header breadcrumb per chunk, keeps fenced code blocks atomic unless they exceed
``max_code_tokens``, and applies a sliding overlap between consecutive chunks
of the same section.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

TokenCounter = Callable[[str], int]

_TOKEN_RE = re.compile(r"\w+|[^\w\s]")
_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def approx_tokens(text: str) -> int:
    """Cheap word/punctuation token estimate (undercounts WordPiece a little)."""
    return len(_TOKEN_RE.findall(text))


@dataclass(frozen=True)
class Chunk:
    index: int
    header_path: str
    content: str
    token_count: int


@dataclass
class _Block:
    text: str
    is_code: bool


def chunk_markdown(
    text: str,
    *,
    target_tokens: int = 400,
    max_code_tokens: int = 512,
    overlap_ratio: float = 0.12,
    count_tokens: TokenCounter = approx_tokens,
) -> list[Chunk]:
    if not 0.0 <= overlap_ratio < 0.5:
        raise ValueError("overlap_ratio must be in [0, 0.5)")
    chunks: list[Chunk] = []
    for path, blocks in _sections(text):
        for content in _pack(blocks, target_tokens, max_code_tokens, overlap_ratio, count_tokens):
            chunks.append(Chunk(len(chunks), path, content, count_tokens(content)))
    return chunks


def _sections(text: str) -> list[tuple[str, list[_Block]]]:
    """Return (header_path, blocks) per section, in document order."""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[_Block]]] = []
    blocks: list[_Block] = []
    para: list[str] = []
    fence: str | None = None
    code: list[str] = []

    def path() -> str:
        return " > ".join(t for _, t in stack)

    def flush_para() -> None:
        if para:
            blocks.append(_Block("\n".join(para), False))
            para.clear()

    def flush_section() -> None:
        flush_para()
        if blocks:
            sections.append((path(), list(blocks)))
        blocks.clear()

    for line in text.splitlines():
        if fence is not None:
            code.append(line)
            m = _FENCE_RE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and line.strip() == m.group(1):
                blocks.append(_Block("\n".join(code), True))
                fence, code = None, []
            continue
        m = _FENCE_RE.match(line)
        if m:
            flush_para()
            fence, code = m.group(1), [line]
            continue
        h = _HEADER_RE.match(line)
        if h:
            flush_section()
            level = len(h.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, h.group(2)))
            continue
        if line.strip():
            para.append(line)
        else:
            flush_para()
    if fence is not None:  # unterminated fence: keep what we saw as code
        blocks.append(_Block("\n".join(code), True))
    flush_section()
    return sections


def _split_prose(text: str, limit: int, count: TokenCounter) -> list[str]:
    if count(text) <= limit:
        return [text]
    words = text.split()
    parts, cur = [], []
    for w in words:
        if cur and count(" ".join([*cur, w])) > limit:
            parts.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        parts.append(" ".join(cur))
    return parts


def _split_code(text: str, limit: int, count: TokenCounter) -> list[str]:
    """Split an oversized fenced block by lines, re-wrapping each piece in the fence."""
    lines = text.split("\n")
    open_f = lines[0]
    has_close = len(lines) > 1 and _FENCE_RE.match(lines[-1]) is not None
    close_f = lines[-1] if has_close else open_f.strip()[:3]
    body = lines[1:-1] if has_close else lines[1:]
    pieces, cur = [], []
    for ln in body:
        if cur and count("\n".join([open_f, *cur, ln, close_f])) > limit:
            pieces.append("\n".join([open_f, *cur, close_f]))
            cur = []
        cur.append(ln)
    if cur:
        pieces.append("\n".join([open_f, *cur, close_f]))
    return pieces or [text]


def _pack(
    blocks: list[_Block],
    target: int,
    max_code: int,
    overlap_ratio: float,
    count: TokenCounter,
) -> list[str]:
    units: list[_Block] = []
    for b in blocks:
        if b.is_code:
            pieces = [b.text] if count(b.text) <= max_code else _split_code(b.text, max_code, count)
            units.extend(_Block(p, True) for p in pieces)
        else:
            units.extend(_Block(p, False) for p in _split_prose(b.text, target, count))

    groups: list[list[_Block]] = []
    cur: list[_Block] = []
    cur_tokens = 0
    for u in units:
        t = count(u.text)
        if cur and cur_tokens + t > target:
            groups.append(cur)
            cur, cur_tokens = [], 0
        cur.append(u)
        cur_tokens += t
    if cur:
        groups.append(cur)

    out: list[str] = []
    for i, g in enumerate(groups):
        body = "\n\n".join(u.text for u in g)
        if i > 0 and overlap_ratio > 0:
            prev = groups[i - 1]
            if not prev[-1].is_code:
                words = prev[-1].text.split()
                n = max(1, round(count(" ".join(w for u in prev for w in u.text.split())) * overlap_ratio))
                n = min(n, len(words))
                body = " ".join(words[-n:]) + "\n\n" + body
        out.append(body)
    return out
