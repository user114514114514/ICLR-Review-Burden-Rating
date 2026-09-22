"""Canonical Profile-ID identity policy. No name, email or fuzzy author guessing."""
import re

from .errors import BurdenError
from .profiles import is_profile_id

OPENREVIEW_PROFILE_URL = "https://openreview.net/profile?id="
OPENREVIEW_FORUM_URL = "https://openreview.net/forum?id="
_PROFILE_TRAILING_INDEX = re.compile(r"\d+$")

IDENTITY_POLICY_VERSION = "canonical-profile-id-v1"
AUTHOR_TABLE_FIELDS = ("canonical_profile_id",)
AUTHOR_METRICS = ("B", "G", "N_bad", "A", "S")
PAPER_METRICS = ("R_p", "yearly_accepted_calibration", "q25_acc", "q75_acc", "scale_s", "threshold_T", "threshold_H")
COVERAGE_NOTICE = (
    "Author-level scores use only positions bound to a canonical OpenReview Profile ID. "
    "Email IDs, missing IDs, and mismatched author lists are unresolved and excluded. "
    "Those papers still receive a paper score and yearly accepted-paper calibration. "
    "Names and emails are never used to guess identity. Scoring covers ICLR 2024 onward."
)


def require_scorable_author(author):
    """Author-level B/G/N_bad/A/S require a canonical OpenReview Profile ID."""
    if (getattr(author, "identity_source", None) != "openreview_id"
            or getattr(author, "identity_ambiguous", False)
            or not is_profile_id(getattr(author, "author_id", None))):
        raise BurdenError("Author-level B/G/N_bad/A/S require a canonical OpenReview Profile ID; "
                          "email, missing and unaligned identities are unresolved")


def public_author_row(canonical_profile_id):
    if not is_profile_id(canonical_profile_id):
        raise BurdenError("Author table only stores canonical OpenReview Profile IDs")
    return {"canonical_profile_id": canonical_profile_id}


def paper_author_row(canonical_profile_id):
    if is_profile_id(canonical_profile_id):
        return public_author_row(canonical_profile_id)
    return {"canonical_profile_id": None}


def display_name_from_profile_id(canonical_profile_id):
    """Split a Profile ID into a display label. Not an official OpenReview name."""
    if not is_profile_id(canonical_profile_id):
        raise BurdenError("Display name can only be derived from a canonical OpenReview Profile ID")
    body = _PROFILE_TRAILING_INDEX.sub("", canonical_profile_id[1:])
    return body.replace("_", " ").strip() or canonical_profile_id


def profile_url(canonical_profile_id):
    if not is_profile_id(canonical_profile_id):
        raise BurdenError("OpenReview profile URL requires a canonical Profile ID")
    return OPENREVIEW_PROFILE_URL + canonical_profile_id


def forum_url(paper_id):
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise BurdenError("OpenReview forum URL requires a paper id")
    return OPENREVIEW_FORUM_URL + paper_id


def identity_coverage(conn=None):
    coverage = {
        "identity_policy_version": IDENTITY_POLICY_VERSION,
        "author_table_fields": list(AUTHOR_TABLE_FIELDS),
        "author_metrics_require_canonical_profile_id": list(AUTHOR_METRICS),
        "paper_metrics_include_unresolved_authors": list(PAPER_METRICS),
        "name_or_email_guessing": False,
        "notice": COVERAGE_NOTICE,
        "missing_identity_concentration": "early years, especially 2020",
    }
    if conn is None:
        return coverage
    coverage["unresolved_positions"] = conn.execute("SELECT COUNT(*) FROM unresolved_paper_authors").fetchone()[0]
    coverage["unresolved_by_year"] = {
        str(year): count for year, count in conn.execute(
            """SELECT p.year, COUNT(*) FROM unresolved_paper_authors u
               JOIN papers p USING(paper_id) GROUP BY p.year ORDER BY p.year""")}
    coverage["resolved_author_positions"] = conn.execute("SELECT COUNT(*) FROM paper_authors").fetchone()[0]
    coverage["canonical_profile_authors"] = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
    return coverage
