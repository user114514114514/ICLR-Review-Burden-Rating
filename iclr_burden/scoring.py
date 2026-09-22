"""Paper and yearly scoring. No network or filesystem access."""
import math
from dataclasses import asdict, dataclass
from statistics import median

from . import constants as C
from .errors import BurdenError, ScoreOverflowError
from .models import Paper
from .normalization import normalize_decision, select_review
from .identity_policy import paper_author_row
from .profiles import is_profile_id


def finite_nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise BurdenError(f"{name} must be a finite nonnegative number")


def _finite_result(value):
    if not math.isfinite(value):
        raise ScoreOverflowError("Score exceeds finite float range; refusing to export Infinity or cap the score")
    return value


def confidence_weight(confidence: float) -> float:
    finite_nonnegative(confidence, "confidence")
    if not 1 <= confidence <= 5:
        raise BurdenError("Confidence must be within 1–5")
    return C.CONFIDENCE_BASE + C.CONFIDENCE_STEP * confidence


def _rank_key(rank):
    if type(rank) is not int or rank < 1:
        raise BurdenError("Author rank must be a positive integer")
    return "rank1" if rank == 1 else "rank2_4" if rank <= 4 else "rank5_plus"


def alpha(rank: int) -> float:
    return C.BAD_AUTHOR_COEF[_rank_key(rank)]


def beta(rank: int) -> float:
    return C.GOOD_AUTHOR_COEF[_rank_key(rank)]


def effective_rank(paper: Paper, author_id: str) -> tuple[int, int]:
    try:
        order = [a.author_id for a in paper.authors].index(author_id) + 1
    except ValueError as exc:
        raise BurdenError(f"Author {author_id} is not on paper {paper.paper_id}") from exc
    return order, 1 if paper.author_roles.get(author_id) else order


def score_paper(paper: Paper) -> dict:
    decision = normalize_decision(paper.decision_raw)
    dr = paper.is_desk_reject or decision == "desk_reject"
    withdrawn = paper.is_withdrawn or decision == "withdraw"
    if dr:
        decision = "desk_reject"
    elif withdrawn:
        decision = "withdraw"
    # Accepted-then-withdrawn status is retained separately from final decision.
    was_accepted = (normalize_decision(paper.decision_raw) in C.ACCEPTED_DECISIONS or any(
        normalize_decision(item.get("decision_raw", "")) in C.ACCEPTED_DECISIONS
        for item in paper.decision_history))
    accepted = decision in C.ACCEPTED_DECISIONS or (
        withdrawn and paper.technical_program_accepted and was_accepted and not dr)
    reviews = [select_review(review, paper) for review in paper.reviews]
    valid = [r for r in reviews if r["valid"]]
    badges = set(paper.data_quality)
    if any(a.identity_ambiguous for a in paper.authors):
        badges.add("identity_ambiguous")
    if decision == "unknown":
        badges.add("decision_unknown")
    if any(r.get("confidence_imputed") for r in valid):
        badges.add("confidence_imputed")
    if paper.rating_mapping:
        badges.add("historical_schema_adapter")
    if any(r["history_status"] not in {"provided", "public_history"} for r in reviews):
        badges.add("review_history_incomplete")
    phase = paper.review_phase_status
    if paper.year == 2026 or phase == "special_reset":
        phase = "special_reset"
        badges.add("special_reset_2026" if paper.year == 2026 else "special_reset")
    elif valid:
        phases = {r["review_phase_status"] for r in valid}
        phase = next(iter(phases)) if len(phases) == 1 else "unknown"
        if phase == "post_rebuttal_final":
            badges.add("verified_post_rebuttal")
        elif phase == "official_final":
            badges.add("official_final")
    result = asdict(paper) | {
        "authors": [a.author_id if is_profile_id(a.author_id) else None for a in paper.authors],
        "author_ids": [a.author_id if is_profile_id(a.author_id) else None for a in paper.authors],
        "author_identities": [paper_author_row(a.author_id) for a in paper.authors],
        "decision_normalized": decision, "is_accepted": accepted,
        "was_accepted": was_accepted, "is_withdrawn": withdrawn, "is_desk_reject": dr,
        "reviews": reviews, "valid_review_count": len(valid), "review_phase_status": phase,
        "R_p": None, "p_p": None, "g_p": None,
        "year_threshold_T": None, "year_threshold_H": None, "year_scale_s": None,
    }
    if dr:
        # Keep every raw review/revision, but DR never earns a quality score or credit.
        status = "desk_reject"
    elif len(valid) < C.MIN_VALID_REVIEWS:
        status = "no_reviews" if not reviews else "insufficient"
        badges.add("insufficient_reviews")
    else:
        status = "valid"
        center = median(r["rating_numeric"] for r in valid)
        for r in valid:
            r["q_i"] = confidence_weight(r["confidence_numeric"])
            r["median_rating"] = center
            r["clipped_rating"] = min(max(r["rating_numeric"], center - C.RATING_CLIP_DELTA),
                                      center + C.RATING_CLIP_DELTA)
            r["was_clipped"] = r["clipped_rating"] != r["rating_numeric"]
        # Center the equivalent weighted mean to preserve identical ratings
        # exactly: roundoff must not create a tiny deficit and increment N_bad.
        result["R_p"] = center + math.fsum(r["q_i"] * (r["clipped_rating"] - center) for r in valid) / math.fsum(r["q_i"] for r in valid)
    return result | {"review_data_status": status, "data_quality": sorted(badges)}


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0 <= fraction <= 1:
        raise BurdenError("Percentile requires nonempty scores and a fraction within [0, 1]")
    for value in values:
        finite_nonnegative(value, "accepted score")
        if not C.RATING_MIN <= value <= C.RATING_MAX:
            raise BurdenError("Accepted scores must stay on the native OpenReview 0–10 scale")
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


@dataclass(frozen=True)
class YearCalibration:
    year: int
    accepted_valid_n: int
    q25_acc: float
    q75_acc: float
    scale_s: float
    threshold_T: float
    threshold_H: float
    source_snapshot: str
    score_version: str = C.SCORE_VERSION
    adapter_version: str = C.DEFAULT_ADAPTER_VERSION
    percentile_method: str = C.PERCENTILE_METHOD
    withdrawn_policy: str = C.WITHDRAWN_CALIBRATION_POLICY

    def __post_init__(self):
        if type(self.year) is not int or self.year < 1900:
            raise BurdenError("Calibration year must be an integer")
        if self.score_version != C.SCORE_VERSION or self.percentile_method != C.PERCENTILE_METHOD:
            raise BurdenError("Calibration score version or percentile method is incompatible")
        if self.withdrawn_policy != C.WITHDRAWN_CALIBRATION_POLICY:
            raise BurdenError("Calibration withdrawn-paper policy is incompatible")
        if type(self.accepted_valid_n) is not int or self.accepted_valid_n < 1 or not self.source_snapshot:
            raise BurdenError("Calibration requires a positive sample size and source snapshot")
        for name in ("q25_acc", "q75_acc", "scale_s", "threshold_T", "threshold_H"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise BurdenError(f"Calibration {name} must be finite")
        if not C.RATING_MIN <= self.q25_acc <= self.q75_acc <= C.RATING_MAX:
            raise BurdenError("Invalid accepted-score quartiles")
        expected_s = max((self.q75_acc - self.q25_acc) / C.IQR_TO_SIGMA, C.MIN_YEAR_SCALE)
        expected_T = self.q25_acc - C.SUBMISSION_FLOOR_BUFFER * expected_s
        if not all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) for a, b in (
            (self.scale_s, expected_s), (self.threshold_T, expected_T), (self.threshold_H, self.q75_acc))):
            raise BurdenError("Calibration s/T/H do not match the scoring formulas")


def calibrate_year(year: int, accepted_scores: list[float], source_snapshot: str,
                   adapter_version: str = C.DEFAULT_ADAPTER_VERSION) -> YearCalibration:
    """Fit yearly accepted-paper thresholds from accepted scores in this snapshot."""
    q25, q75 = percentile(accepted_scores, 0.25), percentile(accepted_scores, 0.75)
    scale = max((q75 - q25) / C.IQR_TO_SIGMA, C.MIN_YEAR_SCALE)
    return YearCalibration(year, len(accepted_scores), q25, q75, scale,
                           q25 - C.SUBMISSION_FLOOR_BUFFER * scale, q75,
                           source_snapshot, adapter_version=adapter_version)


def calibration_from_papers(year: int, papers: list[Paper], source_snapshot: str) -> YearCalibration:
    """Caller must supply a complete year's candidate set; no discovery or I/O."""
    if any(p.year != year for p in papers) or len({p.paper_id for p in papers}) != len(papers):
        raise BurdenError("Calibration papers must be unique and belong to exactly one year")
    adapters = {p.adapter_version for p in papers}
    if len(adapters) != 1:
        raise BurdenError("Calibration papers must share one explicit scale adapter")
    scored = [score_paper(p) for p in papers]
    accepted = [p["R_p"] for p in scored if p["is_accepted"] and p["review_data_status"] == "valid"]
    return calibrate_year(year, accepted, source_snapshot, next(iter(adapters)))


def paper_deficit_credit(R: float, calibration: YearCalibration, *, is_accepted: bool) -> tuple[float, float]:
    finite_nonnegative(R, "R_p")
    if not C.RATING_MIN <= R <= C.RATING_MAX:
        raise BurdenError("R_p must stay on the native OpenReview 0–10 scale")
    raw_deficit = max(0.0, (calibration.threshold_T - R) / calibration.scale_s)
    high_z = max(0.0, (R - calibration.threshold_H) / calibration.scale_s)
    raw_credit = C.GOOD_TANH_MAX * math.tanh(C.GOOD_TANH_SCALE * high_z)
    # Accepted papers never enter bad; good credit is accepted-only.
    if is_accepted:
        return 0.0, raw_credit
    return raw_deficit, 0.0


def paper_severity(deficit: float) -> float:
    """Saturating severity of one raw deficit. Alpha is applied by the caller, not here."""
    finite_nonnegative(deficit, "deficit")
    return deficit / (1.0 + C.SEVERITY_SATURATION * deficit)


def low_quality_factor(rank_alpha: float, deficit: float) -> float:
    """Multiplicative term 1 + alpha * h(p). Alpha stays outside h."""
    finite_nonnegative(rank_alpha, "alpha")
    return 1.0 + rank_alpha * paper_severity(deficit)


def annual_burden(contributions: list[dict]) -> dict:
    """Each row: rank, deficit, credit, is_desk_reject, is_accepted (one row per paper)."""
    log_factors, credits, dr_values = [], [], []
    n_acc = 0
    for p in contributions:
        if p.get("is_accepted"):
            n_acc += 1
        a, b = alpha(p["rank"]), beta(p["rank"])
        if p.get("is_desk_reject", False):
            dr_values.append(C.DR_BASE_COEFFICIENT * a)
            continue
        # Gates: accepted never contributes deficit; non-accepted never contributes credit.
        deficit = 0.0 if p.get("is_accepted") else p.get("deficit", 0.0)
        credit = p.get("credit", 0.0) if p.get("is_accepted") else 0.0
        finite_nonnegative(deficit, "deficit")
        finite_nonnegative(credit, "credit")
        if deficit > 0 and credit > 0:
            raise BurdenError("A paper cannot simultaneously have deficit and credit")
        if credit > C.GOOD_TANH_MAX:
            raise BurdenError("Per-paper surplus exceeds the configured maximum")
        if deficit > 0:
            log_factors.append(math.log(low_quality_factor(a, deficit)))
        credits.append(b * credit)
    log_product = math.fsum(log_factors)
    try:
        # B = 4 * (product(1 + alpha * h(p)) - 1). expm1 avoids cancellation near zero.
        product = _finite_result(math.exp(log_product))
        B = _finite_result(C.BURDEN_PRODUCT_SCALE * math.expm1(log_product))
    except OverflowError as exc:
        raise ScoreOverflowError("Annual product exceeds finite float range") from exc
    G_raw = math.fsum(credits)
    G = min(C.ANNUAL_GOOD_CREDIT_CAP, G_raw)
    D = math.fsum(dr_values)
    n_low, n_dr = len(log_factors), len(dr_values)
    n_bad = n_low + n_dr
    rho = 0.0 if (n_bad + n_acc) == 0 else n_bad / (n_bad + n_acc)
    if not 0.0 <= rho <= 1.0:
        raise BurdenError("rho must be in [0, 1]")
    reviewed_net = max(0.0, B - G)
    # Annual score uses (1+rho) on the reviewed net, plus N_bad * D. Do not also scale by N_bad.
    A = _finite_result((1.0 + rho) * reviewed_net + n_bad * D)
    return {"B": B, "G_raw": G_raw, "G": G, "D": D,
            "N_low": n_low, "N_DR": n_dr, "N_bad": n_bad, "N_acc": n_acc, "rho": rho,
            "product_term": product, "reviewed_net": reviewed_net,
            "annual_unit_burden": reviewed_net + D, "A": A}


def year_participates(row: dict) -> bool:
    """Submitted years with a finite annual score enter peak-score ranking."""
    if row.get("A") is None:
        return False
    paper_ids = row.get("paper_ids")
    if paper_ids is not None and len(paper_ids) == 0:
        return False
    counts = row.get("counts")
    if counts is not None and counts.get("submissions", None) == 0:
        return False
    return True


def peak_scored_row(rows: list[dict]) -> dict:
    """Author-level score is the highest annual A among submitted years."""
    if not rows:
        raise BurdenError("No annual rows")
    participating = [row for row in rows if year_participates(row)]
    if not participating:
        return rows[-1]
    return max(participating, key=lambda row: (row["A"], row["year"]))


def annotate_year_rows(year_rows: list[dict]) -> list[dict]:
    """Keep each year independent. No cross-year accumulation."""
    rows = sorted((dict(row) for row in year_rows), key=lambda row: row["year"])
    if len({r["year"] for r in rows}) != len(rows):
        raise BurdenError("Duplicate annual rows")
    for row in rows:
        A = row["A"]
        if A is not None:
            finite_nonnegative(A, "A")
        row["S"] = None
    return rows
