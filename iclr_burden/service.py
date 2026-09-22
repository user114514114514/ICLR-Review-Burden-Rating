"""Assemble one author's yearly scores from a paper snapshot."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict

from . import constants as C
from .errors import BurdenError, MissingCalibrationError
from .identity_policy import COVERAGE_NOTICE, public_author_row, require_scorable_author
from .models import AuthorSnapshot
from .scoring import (YearCalibration, alpha, annual_burden, annotate_year_rows, beta, effective_rank,
                      low_quality_factor, peak_scored_row, paper_deficit_credit, score_paper,
                      year_participates)
from .storage import content_hash


def decision_counts(papers):
    counts = Counter(p["decision_normalized"] for p in papers)
    accepted = sum(counts[d] for d in C.ACCEPTED_DECISIONS)
    # Explicit denominator: final accepted + final rejected + final desk rejected.
    decided = accepted + counts["reject"] + counts["desk_reject"]
    return {"submissions": len(papers), **{d: counts[d] for d in sorted(C.DECISIONS)},
            "accepted": accepted, "rejected": counts["reject"], "withdrawn": counts["withdraw"],
            "desk_rejected": counts["desk_reject"],
            "valid_reviewed": sum(p["review_data_status"] == "valid" for p in papers),
            "low_quality_reviewed": sum(p.get("p_p") is not None and p["p_p"] > 0 for p in papers),
            "decided_papers": decided,
            "decided_acceptance_rate": accepted / decided if decided else None}


def score_author(snapshot: AuthorSnapshot, calibrations: dict[int, YearCalibration], *, _paper_cache=None) -> dict:
    """Score only this snapshot's author. Missing required calibration fails explicitly."""
    require_scorable_author(snapshot.author)
    papers = []
    for p in sorted(snapshot.papers, key=lambda p: (p.year, p.paper_id)):
        scored = deepcopy(_paper_cache[p.paper_id]) if _paper_cache is not None else score_paper(p)
        order, rank = effective_rank(p, snapshot.author.author_id)
        scored.update(author_position=order, effective_rank=rank, alpha=alpha(rank), beta=beta(rank),
                      author_role_evidence=[asdict(r) for r in p.author_roles.get(snapshot.author.author_id, [])],
                      dr_contribution=0.0, bad_product_factor=1.0, weighted_good_credit=0.0,
                      N_bad_flag=False, contribution_category="unscored")
        if scored["is_desk_reject"]:
            scored.update(dr_contribution=C.DR_BASE_COEFFICIENT * alpha(rank),
                          N_bad_flag=True, contribution_category="desk_reject")
        elif scored["review_data_status"] == "valid":
            cal = calibrations.get(p.year)
            if cal is None:
                raise MissingCalibrationError(
                    f"Missing {p.year} accepted-paper calibration. Supply a verified calibration snapshot; "
                    "single-author lookup does not fetch or estimate the annual distribution.")
            if cal.year != p.year or cal.adapter_version != p.adapter_version:
                raise BurdenError(f"Paper {p.paper_id} and calibration year/adapter mismatch")
            deficit, credit = paper_deficit_credit(scored["R_p"], cal, is_accepted=scored["is_accepted"])
            scored.update(p_p=deficit, g_p=credit, year_threshold_T=cal.threshold_T,
                          year_threshold_H=cal.threshold_H, year_scale_s=cal.scale_s,
                          calibration_source_snapshot=cal.source_snapshot,
                          bad_product_factor=low_quality_factor(alpha(rank), deficit),
                          weighted_good_credit=beta(rank) * credit,
                          N_bad_flag=deficit > 0,
                          contribution_category="low" if deficit > 0 else "high" if credit > 0 else "neutral")
        papers.append(scored)
    rows = []
    for coverage in sorted(snapshot.coverage, key=lambda c: c.year):
        year_papers = [p for p in papers if p["year"] == coverage.year]
        contributions = [{"rank": p["effective_rank"], "deficit": p["p_p"] or 0.0,
                          "credit": p["g_p"] or 0.0, "is_desk_reject": p["is_desk_reject"],
                          "is_accepted": p["is_accepted"]}
                         for p in year_papers]
        components = annual_burden(contributions)
        observed_A = components["A"]
        if not coverage.complete:
            components["A"] = None
        cal = calibrations.get(coverage.year)
        rows.append({"year": coverage.year, **components,
                     "observed_base_burden": observed_A,
                     "score_status": "complete" if coverage.complete else "incomplete_coverage",
                     "coverage": asdict(coverage), "counts": decision_counts(year_papers),
                     "paper_ids": [p["paper_id"] for p in year_papers],
                     "calibration": asdict(cal) if cal else None})
    rows = annotate_year_rows(rows)
    badges = sorted({badge for p in papers for badge in p["data_quality"]})
    if snapshot.author.identity_ambiguous:
        badges = sorted(set(badges) | {"identity_ambiguous"})
    peak = peak_scored_row(rows)
    peak_score = peak["A"] if year_participates(peak) else None
    if peak_score is None:
        badges = sorted(set(badges) | {"incomplete_coverage"})
    return {
        "score_version": C.SCORE_VERSION, "normalizer_version": C.NORMALIZER_VERSION,
        "author": public_author_row(snapshot.author.author_id), "summary": decision_counts(papers),
        "current_score": {"year": peak["year"], "A": peak_score, "S": peak_score,
                          "N_bad": peak.get("N_bad"), "score_status": peak.get("score_status")},
        "years": rows, "papers": papers, "data_quality": badges,
        "metadata": {
            "data_snapshot_timestamp": snapshot.data_snapshot_timestamp,
            "source_snapshot": snapshot.source_snapshot,
            "author_snapshot_sha256": content_hash(snapshot.to_dict()),
            "calibrations_sha256": content_hash([asdict(calibrations[y]) for y in sorted(calibrations)
                                                 if y in {c.year for c in snapshot.coverage}]),
            "coverage_years": [r["year"] for r in rows],
            "withdrawn_calibration_policy": C.WITHDRAWN_CALIBRATION_POLICY,
            "acceptance_rate_denominator": "final accepted + reject + desk_reject; excludes withdraw and unknown",
            "minimum_valid_reviews": C.MIN_VALID_REVIEWS,
            "parameters": {name: dict(value) if name in {"BAD_AUTHOR_COEF", "GOOD_AUTHOR_COEF"} else value
                           for name, value in vars(C).items()
                           if name in {"CONFIDENCE_BASE", "CONFIDENCE_STEP", "DEFAULT_CONFIDENCE", "RATING_CLIP_DELTA",
                                       "IQR_TO_SIGMA", "MIN_YEAR_SCALE", "SUBMISSION_FLOOR_BUFFER",
                                       "SEVERITY_SATURATION", "BURDEN_PRODUCT_SCALE",
                                       "GOOD_TANH_MAX", "GOOD_TANH_SCALE", "ANNUAL_GOOD_CREDIT_CAP", "DR_BASE_COEFFICIENT",
                                       "BAD_AUTHOR_COEF", "GOOD_AUTHOR_COEF"}},
            "numeric_policy": "reviewer rating keeps the OpenReview native value on 0–10; confidence 1–5; impute missing confidence only",
            "gap_policy": "each year is scored independently; author ranking uses the highest annual A",
            "scope": "one author; public evidence only; no inference about intent",
            "identity_policy": COVERAGE_NOTICE,
            "input_metadata": snapshot.metadata,
        },
    }
