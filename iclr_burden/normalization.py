"""Conservative identity, decision and review normalization."""
import hashlib
import math
import re
from dataclasses import asdict

from .constants import DEFAULT_CONFIDENCE, RATING_MAX, RATING_MIN
from .errors import BurdenError
from .models import Author, Paper, Review

NUMERIC_PREFIX_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)")


def unwrap(value):
    return value.get("value") if isinstance(value, dict) else value


def parse_numeric(value) -> float | None:
    value = unwrap(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            result = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str):
        match = NUMERIC_PREFIX_RE.match(value)
        if not match:
            return None
        result = float(match.group(1))
    else:
        return None
    return result if math.isfinite(result) else None


def normalize_decision(raw: str) -> str:
    text = re.sub(r"[_\-/]+", " ", str(raw).lower()).strip()
    if re.search(r"\bdesk\s*reject", text):
        return "desk_reject"
    if re.search(r"\bwithdraw", text):
        return "withdraw"
    # Negative decisions have precedence over presentation words in free text.
    if re.search(r"\breject|\bnot\s+accept", text):
        return "reject"
    if re.search(r"\btalk\b|notable\s+top\s+5\s*%", text):
        return "oral"
    if re.search(r"notable\s+top\s+25\s*%", text):
        return "spotlight"
    for name in ("oral", "spotlight", "poster"):
        if re.search(rf"\b{name}\b", text):
            return name
    if re.search(r"\baccept(?:ed)?\b", text):
        return "accept"
    return "unknown"


def make_author(name: str, profile_id: str | None, paper_id: str, index: int) -> Author:
    if isinstance(profile_id, str) and profile_id.startswith("~"):
        return Author(profile_id, name)
    # Paper-scoped fallbacks never silently merge homonyms across papers.
    digest = hashlib.sha256(f"{paper_id}:{index}:{name}".encode()).hexdigest()[:24]
    return Author(f"name-fallback:{digest}", name, identity_source="name_fallback",
                  identity_ambiguous=True)


def _revision_values(revision, paper):
    rating = parse_numeric(revision.rating_raw)
    reason = None
    if rating is not None and paper.rating_mapping:
        mapping = {}
        for key, value in paper.rating_mapping.items():
            try:
                source, target = float(key), float(value)
            except (ValueError, TypeError, OverflowError) as exc:
                raise BurdenError("Rating adapter must map numeric values to numeric values") from exc
            if not math.isfinite(source) or not math.isfinite(target) or not RATING_MIN <= target <= RATING_MAX:
                raise BurdenError("Rating adapter targets must be finite and within the native 0–10 range")
            if source in mapping:
                raise BurdenError("Duplicate numeric keys in rating adapter")
            mapping[source] = target
        rating = mapping.get(rating)
        if rating is None:
            reason = "rating_not_in_adapter"
    if rating is None or not RATING_MIN <= rating <= RATING_MAX:
        return None, None, False, reason or "invalid_rating"
    raw_confidence = unwrap(revision.confidence_raw)
    missing = raw_confidence is None or (isinstance(raw_confidence, str) and not raw_confidence.strip())
    confidence = DEFAULT_CONFIDENCE if missing else parse_numeric(raw_confidence)
    if confidence is None or not 1 <= confidence <= 5:
        return None, None, False, "invalid_confidence"
    return rating, float(confidence), missing, None


def select_review(review: Review, paper: Paper) -> dict:
    """One rating per reviewer; a 2026 reset never falls back to pre-reset history."""
    candidates = sorted(enumerate(review.revisions), key=lambda pair: (
        pair[1].revision_timestamp, pair[1].revision_index, pair[0]))
    currents = [v for _, v in candidates if v.is_current]
    if len(currents) > 1:
        raise BurdenError(f"Review {review.review_id} has multiple current revisions")
    special = paper.year == 2026 or paper.review_phase_status == "special_reset"
    audit = []
    selected = None
    for position, revision in candidates:
        rating, confidence, imputed, error = _revision_values(revision, paper)
        eligible = revision.is_official and (not special or revision.is_current)
        row = asdict(revision) | {
            "input_index": position, "rating_numeric": rating, "confidence_numeric": confidence,
            "confidence_imputed": imputed, "eligible": eligible, "validation_error": error,
        }
        audit.append(row)
        if eligible and error is None:
            selected = row
    # The current Official Review is the server-materialized final state and is
    # authoritative even if its timestamp is older than a visible edit.
    current_valid = [r for r in audit if r["is_current"] and r["eligible"] and r["validation_error"] is None]
    if current_valid:
        selected = current_valid[0]
    result = {
        "review_id": review.review_id, "paper_id": paper.paper_id,
        "history_status": review.history_status, "revisions": audit,
        "valid": selected is not None, "q_i": None, "median_rating": None,
        "clipped_rating": None, "was_clipped": False,
    }
    if selected is None:
        return result | {"exclusion_reason": "no_usable_current_reset_review" if special else "no_valid_official_revision"}
    latest_official = next((r for r in reversed(audit) if r["is_official"]), None)
    return result | {key: selected[key] for key in (
        "rating_raw", "confidence_raw", "rating_numeric", "confidence_numeric",
        "confidence_imputed", "revision_timestamp", "revision_index", "source_id",
    )} | {
        "selected_input_index": selected["input_index"],
        "is_latest_public_revision": selected["is_current"] or selected is latest_official,
        "review_phase_status": "special_reset" if special else selected["phase"],
    }
