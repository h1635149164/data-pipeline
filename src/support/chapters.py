"""
chapters.py — Pure functions for chapter diffing, fuzzy-name anchoring, and iid stability.

Design goals:
- No classes, only composable functions.
- stdlib difflib for fuzzy matching (no extra runtime dependency).
- Two kinds of "expired" detection:
    1. API-driven: the official endpoint sets expired=True (or endDate in the past).
    2. Disappearance-driven: a chapter present in our mirror is no longer in the
       official list at all; we mark it as expired with today's endDate.
- A chapter is considered "renamed" when the official list contains no exact match
  but a fuzzy match above the threshold is found; in that case we update the name
  (and id) in place, preserving iid.
"""

import json
from datetime import date
from difflib import SequenceMatcher

# Similarity threshold for fuzzy name matching (0.0–1.0).
# A score >= this value is treated as the same organisation.
DEFAULT_FUZZY_THRESHOLD: float = 0.85


def _similarity(a: str, b: str) -> float:
    """Return normalised similarity score between two strings (0.0–1.0)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def fuzzy_match_chapter(
    name: str,
    existing: list[dict],
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> dict | None:
    """Find the best fuzzy-name match for *name* in *existing*.

    Args:
        name:      Name from the official API to look up.
        existing:  List of our chapter dicts (each must have a "name" key).
        threshold: Minimum similarity score to accept as a match.

    Returns:
        The best-matching chapter dict, or None if no match exceeds the threshold.
    """
    if not existing:
        return None

    best: dict | None = None
    best_score: float = 0.0

    for chapter in existing:
        score = _similarity(name, chapter["name"])
        if score > best_score:
            best_score = score
            best = chapter

    if best_score >= threshold:
        return best
    return None


def next_iid(chapters: list[dict]) -> int:
    """Return the next available iid (max existing iid + 1, or 1 for empty lists).

    Args:
        chapters: List of our chapter dicts (each must have an "iid" key).

    Returns:
        Next integer iid.
    """
    if not chapters:
        return 1
    return max(ch["iid"] for ch in chapters) + 1


def _sync_mutable_fields(target: dict, source: dict) -> bool:
    """Sync endDate and expired from *source* into *target*.

    Returns:
        True if any field changed, False otherwise.
    """
    changed = False
    for field in ("endDate", "expired"):
        new_val = source.get(field)
        if new_val is not None and target.get(field) != new_val:
            target[field] = new_val
            changed = True
    return changed


def merge_chapters(
    official: list[dict],
    ours: list[dict],
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> tuple[list[dict], bool]:
    """Merge the official chapter list into our mirror, preserving iid stability.

    Merge rules (applied in order):
    1. **Exact name match** (case-insensitive) — sync endDate and expired in place.
    2. **Fuzzy name match** (score >= threshold, no exact match) — treat as a
       rename: update name, id, endDate, expired in place; keep iid.
    3. **No match** — new organisation; assign next_iid and append.
    4. **Disappearance** — chapters in our mirror that no longer appear in the
       official list (even fuzzily) and are not already marked expired are marked
       expired with today's date as endDate.

    Args:
        official:  Fresh chapter list from the state API.
        ours:      Our current mirror (list of chapter dicts with iid).
        threshold: Fuzzy similarity threshold for rename / disappearance detection.

    Returns:
        Tuple of (merged_list, changed) where changed is True if any record
        was added or modified.
    """
    # Deep-copy our list so the input is not mutated.
    result: list[dict] = [dict(ch) for ch in ours]
    changed: bool = False

    # --- Step 1, 2 & 3: process each official chapter ---
    for off_ch in official:
        off_name: str = off_ch["name"]

        # Step 1: Try exact match first (case-insensitive).
        exact: dict | None = next(
            (r for r in result if r["name"].lower() == off_name.lower()), None
        )

        if exact is not None:
            if _sync_mutable_fields(exact, off_ch):
                changed = True

        else:
            # Step 2: No exact match — try fuzzy.
            fuzzy: dict | None = fuzzy_match_chapter(off_name, result, threshold)
            if fuzzy is not None:
                # Rename detected: update name and id too.
                field_changed = False
                if fuzzy["name"] != off_name:
                    fuzzy["name"] = off_name
                    field_changed = True
                if fuzzy["id"] != off_ch["id"]:
                    fuzzy["id"] = off_ch["id"]
                    field_changed = True
                if _sync_mutable_fields(fuzzy, off_ch):
                    field_changed = True
                if field_changed:
                    changed = True

            else:
                # Step 3: Completely new organisation.
                new_ch: dict = {
                    "iid": next_iid(result),
                    "id": off_ch["id"],
                    "name": off_ch["name"],
                    "startDate": off_ch.get("startDate", ""),
                    "endDate": off_ch.get("endDate", "9999-12-31"),
                    "expired": off_ch.get("expired", False),
                }
                result.append(new_ch)
                changed = True

    # --- Step 4: Disappearance detection ---
    # Build a list of official names for matching (original casing; _similarity handles lowering).
    official_names: list[str] = [ch["name"] for ch in official]
    today_str: str = date.today().isoformat()

    for our_ch in result:
        if our_ch.get("expired"):
            continue  # Already expired — skip.

        # Check whether this chapter still appears in the official list (fuzzy).
        still_present = any(
            _similarity(our_ch["name"], off_name) >= threshold
            for off_name in official_names
        )
        if not still_present:
            our_ch["expired"] = True
            our_ch["endDate"] = today_str
            changed = True

    return result, changed


def chapters_to_json(chapters: list[dict], indent: int = 2) -> str:
    """Serialise a list of chapter dicts to a pretty-printed JSON string.

    Args:
        chapters: List of chapter dicts.
        indent:   JSON indentation width (default 2).

    Returns:
        JSON string.
    """
    return json.dumps(chapters, indent=indent, ensure_ascii=False)