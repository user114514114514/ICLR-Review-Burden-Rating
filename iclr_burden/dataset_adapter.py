"""Adapter derived from the actual qhjqhj00 v1.0 JSONL records."""
from collections import defaultdict

from .constants import DEFAULT_ADAPTER_VERSION
from .dataset_source import content_fields, note_kind
from .errors import BurdenError
from .models import Paper, Review, ReviewRevision
from .normalization import make_author, normalize_decision

DATASET_ADAPTER_VERSION = "qhjqhj00-jsonl-v1"


def rating_adapter(year):
    # Keep OpenReview reviewer ratings on the native numeric scale.
    # Cross-year differences are handled only by yearly Q25/Q75/s_y calibration.
    if year == 2020:
        return "iclr2020-raw-1-8-v1", {}
    if year == 2026:
        return "iclr2026-raw-0-10-v1", {}
    return DEFAULT_ADAPTER_VERSION, {}


def apply_rating_adapter(paper_dict):
    """Overwrite any stored pre-normalization with the current native-scale adapter."""
    version, mapping = rating_adapter(paper_dict["year"])
    if paper_dict.get("adapter_version") == version and paper_dict.get("rating_mapping") == mapping:
        return paper_dict, False
    updated = dict(paper_dict)
    updated["adapter_version"] = version
    updated["rating_mapping"] = mapping
    return updated, True


def timestamp(note):
    return max((note.get(k) or 0 for k in ("tmdate", "mdate", "tcdate", "cdate")), default=0)


def normalize_paper(note, replies, year, *, source_snapshot, source_line=None):
    paper_id = note.get("forum") or note["id"]
    content = content_fields(note)
    names, ids = content.get("authors") or [], content.get("authorids") or []
    if not isinstance(names, list) or not isinstance(ids, list):
        raise BurdenError(f"Non-array authors/authorids: {paper_id}")
    flags = {"dataset_snapshot_only", "review_history_unavailable"}
    if year <= 2023:
        flags.add("early_year_submission_coverage_limited")
    if year == 2026:
        flags.add("decisions_may_change_after_snapshot")
    aligned = len(names) == len(ids)
    if not aligned:
        flags.add("author_id_order_unverified")
    if not names:
        flags.add("authors_missing")
    authors = [make_author(str(name) or "Unknown author", ids[i] if aligned else None, paper_id, i)
               for i, name in enumerate(names)]
    reviews_by_id, decisions, metas, comments = defaultdict(list), [], [], []
    for reply in replies:
        if reply.get("ddate"):
            continue
        if reply.get("_year", year) != year:
            raise BurdenError(f"Reply year mismatch: {reply['id']}")
        if (reply.get("_paper_forum") or reply.get("forum")) != paper_id:
            raise BurdenError(f"Reply forum mismatch: {reply['id']}")
        kind = note_kind(reply)
        if kind == "Official_Review":
            reviews_by_id[reply["id"]].append(reply)
        elif kind == "Decision":
            decisions.append(reply)
        elif kind == "Meta_Review":
            metas.append(reply["id"])
        else:
            comments.append(reply["id"])
    decisions.sort(key=lambda d: (timestamp(d), d["id"]))
    history = [{"decision_raw": str(content_fields(d).get("decision") or ""),
                "timestamp": timestamp(d), "source_id": d["id"]} for d in decisions]
    raw_decision = history[-1]["decision_raw"] if history else str(content.get("decision") or content.get("venue") or "")
    statuses = [normalize_decision(str(content.get(key) or "")) for key in ("venueid", "venue")]
    state = next((s for s in statuses if s != "unknown"), normalize_decision(raw_decision))
    # Venue status can reflect a withdrawal/reinstatement after the last decision.
    if state not in {"unknown", "withdraw", "desk_reject"} and normalize_decision(raw_decision) in {"unknown", "withdraw", "desk_reject"}:
        raw_decision = str(content.get("venue") or state)
    if state == "reject" and normalize_decision(raw_decision) not in {"reject", "unknown"}:
        raw_decision = "Reject"
        flags.add("decision_history_differs_from_current_status")
    reviews = []
    rating_field = "recommendation" if year in (2022, 2023) else "rating"
    for review_id, notes in sorted(reviews_by_id.items()):
        notes.sort(key=lambda r: (timestamp(r), str(r.get("version", ""))))
        revisions = []
        for index, r in enumerate(notes):
            rc = content_fields(r)
            revisions.append(ReviewRevision(
                rating_raw=rc.get(rating_field), confidence_raw=rc.get("confidence"),
                revision_timestamp=timestamp(r), revision_index=index, source_id=review_id,
                is_current=index == len(notes) - 1,
                phase="special_reset" if year == 2026 else "official_final",
                source_metadata={"rating_field": rating_field, "confidence_field": "confidence",
                                 "version": r.get("version"), "timestamps": {k: r.get(k) for k in ("cdate", "mdate", "tcdate", "tmdate")},
                                 "experience_assessment": rc.get("experience_assessment"),
                                 "history": "Only versions present in the dataset; not a complete revision history"}))
        reviews.append(Review(review_id, revisions, "dataset_current_only"))
    version, mapping = rating_adapter(year)
    return Paper(
        paper_id=paper_id, year=year, title=str(content.get("title") or ""), authors=authors,
        abstract=str(content.get("abstract") or ""), reviews=reviews,
        openreview_url="https://openreview.net/forum?id=" + paper_id,
        decision_raw=raw_decision, decision_history=history,
        is_withdrawn=state == "withdraw", is_desk_reject=state == "desk_reject",
        desk_reject_reason=content.get("desk_reject_reason"),
        submission_date=note.get("cdate") or note.get("tcdate"),
        decision_date=history[-1]["timestamp"] if history else None,
        review_phase_status="special_reset" if year == 2026 else "official_final",
        adapter_version=version, rating_mapping=mapping, data_quality=sorted(flags),
        source_author_ids=ids,
        source_urls=["https://github.com/qhjqhj00/iclr-openreview-reviews", "https://openreview.net/forum?id=" + paper_id],
        source_metadata={"snapshot": source_snapshot, "dataset_adapter": DATASET_ADAPTER_VERSION,
                         "source_note_id": note["id"], "original_note_id": note.get("original"),
                         "paper_file": f"iclr_{year}/papers.jsonl", "paper_line": source_line,
                         "venue": content.get("venue"), "venueid": content.get("venueid"),
                         "meta_review_ids": sorted(set(metas)), "comment_ids": sorted(set(comments)),
                         "api_version": 1 if year <= 2023 else 2})
