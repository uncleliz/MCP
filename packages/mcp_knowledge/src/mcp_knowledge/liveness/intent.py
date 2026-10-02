"""Knowledge-vs-live decision (spec §36 "Live Verification Policy", §37 "Knowledge vs Live").

A question is routed by *intent*, not by guessing an answer:

* historical / knowledge ("how does X work", "what happened in PAY-123", "architecture of Y") →
  the local knowledge snapshot is enough (``LiveNeed.KNOWLEDGE``);
* current-state ("currently", "now", "latest", "today", "is it still open", "who is currently
  assigned", "what is the latest commit") → a live check is required (``LiveNeed.LIVE``);
* a question carrying **both** a historical part and a current-state part → both
  (``LiveNeed.MIXED``), e.g. "how does payment timeout work *and is PAY-121 still being worked
  on*?" (spec §38).

This is deliberately a small, deterministic marker match (no model, no egress): the markers are the
ones the spec lists verbatim, extended with their obvious Vietnamese equivalents so a Vietnamese
question is routed the same way. False negatives degrade safely — the Live path still reconciles
against whatever live value it is given; the classifier only decides *whether to bother* asking the
live source, so an un-matched question simply stays on the snapshot (the honest default of §35).
"""

from __future__ import annotations

import re
from enum import StrEnum

__all__ = ["LiveNeed", "classify_live_need", "LIVE_MARKERS"]


class LiveNeed(StrEnum):
    """What a question needs: the local snapshot, a live check, or both."""

    KNOWLEDGE = "knowledge"
    LIVE = "live"
    MIXED = "mixed"


#: Markers that signal "the asker wants the *current* state" (spec §36). English markers verbatim
#: from the spec plus their common Vietnamese equivalents. Matched case-insensitively on word
#: boundaries so "nowhere" does not trip "now".
LIVE_MARKERS: tuple[str, ...] = (
    # English (spec §36 verbatim + close variants)
    "currently",
    "right now",
    "now",
    "latest",
    "today",
    "still open",
    "still being worked on",
    "still in progress",
    "currently assigned",
    "who is assigned",
    "latest commit",
    "up to date",
    "as of now",
    "at the moment",
    # Vietnamese equivalents (same intent)
    "hiện tại",
    "hiện giờ",
    "bây giờ",
    "mới nhất",
    "hôm nay",
    "còn mở",
    "còn đang làm",
    "đang làm",
    "đang mở",
    "ai đang",
    "mới cập nhật",
)

#: Markers that explicitly anchor a question in the *past* / knowledge base (spec §36 "normally do
#: not require live verification"). Used only to decide MIXED vs LIVE when a live marker is present.
_HISTORICAL_MARKERS: tuple[str, ...] = (
    "how does",
    "how do",
    "architecture",
    "what happened",
    "depend on",
    "depends on",
    "design",
    "cách hoạt động",
    "kiến trúc",
    "phụ thuộc",
    "đã xảy ra",
    "lịch sử",
    "thiết kế",
)


def _contains(text: str, marker: str) -> bool:
    # Word-boundary match for alphanumeric markers; plain substring for phrases with spaces or
    # non-ASCII (Vietnamese) where \b is unreliable.
    if marker.isascii() and " " not in marker:
        return re.search(rf"\b{re.escape(marker)}\b", text) is not None
    return marker in text


def classify_live_need(question: str) -> LiveNeed:
    """Classify whether *question* needs a live check, the snapshot, or both.

    Deterministic and offline. Returns :data:`LiveNeed.LIVE` when a current-state marker is present
    and no historical marker is; :data:`LiveNeed.MIXED` when both a current-state and a historical
    marker appear (the §38 mixed query); otherwise :data:`LiveNeed.KNOWLEDGE`.
    """
    text = question.casefold()
    has_live = any(_contains(text, m.casefold()) for m in LIVE_MARKERS)
    has_history = any(_contains(text, m.casefold()) for m in _HISTORICAL_MARKERS)
    if has_live and has_history:
        return LiveNeed.MIXED
    if has_live:
        return LiveNeed.LIVE
    return LiveNeed.KNOWLEDGE
