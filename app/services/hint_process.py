"""
app/services/hint_process.py

Post-processing for hint LLM output before returning to the client.

Responsibilities:
  - Fix common ref tag formatting issues (e.g. [ref-eq-1] → [ref:ref-eq-1])
  - Strip invented ref IDs that are not in the anchor map
  - Repair bleeding where ref tags are fused with surrounding text/LaTeX
  - Accumulate streaming chunks and apply fixes on complete ref tags only

Usage (streaming):
    processor = HintPostProcessor(valid_ids=anchor_ids)
    async for raw_chunk in llm.astream(prompt):
        cleaned = processor.feed(raw_chunk.content)
        if cleaned:
            yield cleaned
    yield processor.flush()  # flush any remaining buffer

Usage (non-streaming):
    cleaned = postprocess_hint(raw_text, valid_ids=anchor_ids)
"""

import re
from collections.abc import Iterator


# Matches intended ref patterns the LLM commonly produces, including malformed ones:
#   [ref:ref-eq-1]       ← correct
#   [ref:eq-1]           ← missing "ref-" prefix (common mistake)
#   [ref-eq-1]           ← missing colon
#   ref-eq-1]            ← missing opening bracket (mid-token bleed)
#   someword[ref:ref-p-1] ← fused with preceding text
_RAW_REF_PATTERN = re.compile(
    r"(\w*)?"           # optional preceding fused word (bleed)
    r"\[?"              # optional [
    r"ref[-:]"          # ref: or ref-
    r"([\w-]+)"         # the ID body
    r"\]?",             # optional ]
    re.IGNORECASE,
)

# Strict pattern for a well-formed ref tag — used to find already-correct tags
_CLEAN_REF_PATTERN = re.compile(r"\[ref:([\w-]+)\]")

# Detects an *incomplete* ref tag at the end of a buffer (for streaming safety)
# Matches any suffix that looks like the start of a ref tag but isn't closed yet
_INCOMPLETE_REF_SUFFIX = re.compile(
    r"(\w*\[ref:[\w-]*|\w*\[ref-[\w-]*|\[ref$|\[re$|\[r$|\[$)$",
    re.IGNORECASE,
)


def _normalize_ref_id(raw_id: str, valid_ids: set[str]) -> str | None:
    """Try to match a raw extracted ID to a valid anchor ID.

    Attempts direct match first, then tries adding 'ref-' prefix if missing.
    Returns the valid ID string or None if no match found.
    """
    if raw_id in valid_ids:
        return raw_id
    # Model sometimes drops the "ref-" prefix, e.g. writes "eq-1" for "ref-eq-1"
    prefixed = f"ref-{raw_id}"
    if prefixed in valid_ids:
        return prefixed
    return None


def _replace_refs(text: str, valid_ids: set[str]) -> str:
    """Replace all ref patterns (well-formed or malformed) in a complete text block.

    - Repairs formatting
    - Strips invented IDs (replaces with empty string)
    - Fixes bleeding (removes fused preceding word characters)
    """
    def replacer(m: re.Match) -> str:
        fused_prefix = m.group(1) or ""
        raw_id = m.group(2)

        valid_id = _normalize_ref_id(raw_id, valid_ids)

        # Reconstruct: put back any fused word, then the ref tag (or nothing)
        if valid_id:
            return f"{fused_prefix}[ref:{valid_id}]"
        else:
            # Invented ID — drop the tag entirely, keep any fused word
            return fused_prefix

    return _RAW_REF_PATTERN.sub(replacer, text)


def postprocess_hint(text: str, valid_ids: list[str]) -> str:
    """Post-process a complete hint response string.

    Args:
        text: Full LLM output string.
        valid_ids: List of valid anchor IDs from the anchor map.

    Returns:
        Cleaned string with all ref tags normalized or removed.
    """
    id_set = set(valid_ids)
    return _replace_refs(text, id_set)


class HintPostProcessor:
    """Stateful post-processor for streaming hint output.

    Buffers incoming chunks and only emits text once any in-flight ref tag
    is complete, preventing partial/broken tags from reaching the client.

    Example:
        processor = HintPostProcessor(valid_ids=["ref-eq-1", "ref-p-3"])
        async for chunk in llm.astream(prompt):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            output = processor.feed(token)
            if output:
                yield output
        remainder = processor.flush()
        if remainder:
            yield remainder
    """

    def __init__(self, valid_ids: list[str]) -> None:
        self._valid_ids = set(valid_ids)
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        """Accept a new chunk. Returns any safe-to-emit text.

        Holds back the tail of the buffer if it looks like an incomplete ref tag.
        """
        self._buffer += chunk

        # Check if the buffer ends with what looks like an incomplete ref tag
        m = _INCOMPLETE_REF_SUFFIX.search(self._buffer)
        if m:
            # Hold back the suspicious suffix, emit everything before it
            safe_end = m.start()
            safe_text = self._buffer[:safe_end]
            self._buffer = self._buffer[safe_end:]
        else:
            safe_text = self._buffer
            self._buffer = ""

        return _replace_refs(safe_text, self._valid_ids) if safe_text else ""

    def flush(self) -> str:
        """Flush any remaining buffered text at end of stream."""
        remainder = self._buffer
        self._buffer = ""
        return _replace_refs(remainder, self._valid_ids) if remainder else ""