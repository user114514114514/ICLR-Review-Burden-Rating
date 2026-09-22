"""Input models; outputs are JSON-compatible audit records."""
from dataclasses import asdict, dataclass, field
from typing import Any

from .constants import DEFAULT_ADAPTER_VERSION, SCHEMA_VERSION
from .errors import BurdenError


@dataclass(frozen=True)
class Author:
    author_id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    identity_source: str = "openreview_id"
    identity_ambiguous: bool = False

    def __post_init__(self):
        if not self.author_id or not self.display_name:
            raise BurdenError("Author requires author_id and display_name")
        if self.identity_source not in {"openreview_id", "name_fallback"}:
            raise BurdenError("Unsupported identity_source")
        if self.identity_source == "openreview_id" and not self.author_id.startswith("~"):
            raise BurdenError("OpenReview identity must use a Profile ID beginning with ~")
        if self.identity_source == "name_fallback" and not self.identity_ambiguous:
            raise BurdenError("Name-fallback identities must be marked ambiguous")
        if type(self.identity_ambiguous) is not bool:
            raise BurdenError("identity_ambiguous must be boolean")


@dataclass(frozen=True)
class RoleEvidence:
    role: str
    source: str

    def __post_init__(self):
        if self.role not in {"co_first", "corresponding"} or not self.source.strip():
            raise BurdenError("Author-role override requires a supported role and reliable source")


@dataclass(frozen=True)
class ReviewRevision:
    rating_raw: Any = None
    confidence_raw: Any = None
    revision_timestamp: int = 0
    revision_index: int = 0
    source_id: str = ""
    is_official: bool = True
    is_current: bool = False
    phase: str = "unknown"
    source_metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if type(self.is_current) is not bool or type(self.is_official) is not bool:
            raise BurdenError("Revision current/official flags must be boolean")
        if self.phase not in {"unknown", "official_final", "post_rebuttal_final", "pre_rebuttal", "special_reset"}:
            raise BurdenError("Unsupported review phase")
        if type(self.revision_timestamp) is not int or self.revision_timestamp < 0:
            raise BurdenError("Revision timestamp must be a nonnegative integer in milliseconds")
        if type(self.revision_index) is not int or self.revision_index < 0:
            raise BurdenError("Revision index must be a nonnegative integer")


@dataclass(frozen=True)
class Review:
    review_id: str
    revisions: list[ReviewRevision]
    history_status: str = "provided"


@dataclass(frozen=True)
class Paper:
    paper_id: str
    year: int
    title: str
    authors: list[Author]
    reviews: list[Review] = field(default_factory=list)
    abstract: str = ""
    openreview_url: str = ""
    decision_raw: str = ""
    decision_history: list[dict] = field(default_factory=list)
    is_withdrawn: bool = False
    is_desk_reject: bool = False
    desk_reject_reason: str | None = None
    submission_date: int | None = None
    decision_date: int | None = None
    review_phase_status: str = "unknown"
    author_roles: dict[str, list[RoleEvidence]] = field(default_factory=dict)
    technical_program_accepted: bool = False
    technical_program_evidence: str | None = None
    adapter_version: str = DEFAULT_ADAPTER_VERSION
    # Optional remapping of raw rating labels. Native ICLR ratings leave this empty.
    rating_mapping: dict[str, float] = field(default_factory=dict)
    source_urls: list[str] = field(default_factory=list)
    data_quality: list[str] = field(default_factory=list)
    source_author_ids: list = field(default_factory=list)
    source_metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.paper_id or type(self.year) is not int or self.year < 1900:
            raise BurdenError("Paper requires an ID and integer conference year")
        if any(type(v) is not bool for v in (self.is_withdrawn, self.is_desk_reject, self.technical_program_accepted)):
            raise BurdenError("Paper status flags must be boolean")
        if not self.adapter_version:
            raise BurdenError("Paper requires an explicit adapter version")
        if not self.authors and "authors_missing" not in self.data_quality:
            raise BurdenError(f"Paper {self.paper_id} has no ordered authors")
        ids = [a.author_id for a in self.authors]
        if len(ids) != len(set(ids)) and "verified_profile_alias_duplicate" not in self.data_quality:
            raise BurdenError(f"Duplicate author identity in {self.paper_id}")
        review_ids = [r.review_id for r in self.reviews]
        if any(not r for r in review_ids) or len(set(review_ids)) != len(review_ids):
            raise BurdenError(f"Missing or duplicate review ID in {self.paper_id}")
        if set(self.author_roles) - set(ids):
            raise BurdenError("Author-role evidence must refer to an author of the paper")
        if self.technical_program_accepted and not self.technical_program_evidence:
            raise BurdenError("Technical-program inclusion requires evidence")
        if self.rating_mapping and self.adapter_version == DEFAULT_ADAPTER_VERSION:
            raise BurdenError("Rating transformations require a distinct adapter_version")

    @classmethod
    def from_dict(cls, item):
        p = dict(item)
        p["authors"] = [Author(**a) for a in p["authors"]]
        p["reviews"] = [Review(r["review_id"], [ReviewRevision(**v) for v in r["revisions"]],
                               r.get("history_status", "provided")) for r in p.get("reviews", [])]
        p["author_roles"] = {key: [RoleEvidence(**r) for r in roles]
                             for key, roles in p.get("author_roles", {}).items()}
        return cls(**p)


@dataclass(frozen=True)
class Coverage:
    year: int
    complete: bool
    source_snapshot: str
    scope: str = "public_author_records"

    def __post_init__(self):
        if type(self.year) is not int or self.year < 1900 or type(self.complete) is not bool:
            raise BurdenError("Coverage requires an integer year and boolean complete")
        if not self.source_snapshot:
            raise BurdenError("Coverage must identify its source snapshot")


@dataclass(frozen=True)
class AuthorSnapshot:
    author: Author
    papers: list[Paper]
    coverage: list[Coverage]
    data_snapshot_timestamp: str
    source_snapshot: str
    schema_version: str = SCHEMA_VERSION
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION:
            raise BurdenError(f"Unsupported snapshot schema: {self.schema_version}")
        if not self.data_snapshot_timestamp or not self.source_snapshot or not self.coverage:
            raise BurdenError("Snapshot requires timestamp, source identifier and explicit coverage")
        years = [c.year for c in self.coverage]
        if len(set(years)) != len(years):
            raise BurdenError("Duplicate coverage years")
        ids = [p.paper_id for p in self.papers]
        if len(set(ids)) != len(ids):
            raise BurdenError("Duplicate paper IDs would double-count author contributions")
        for p in self.papers:
            if p.year not in years:
                raise BurdenError(f"Paper {p.paper_id} is outside declared coverage")
            if self.author.author_id not in [a.author_id for a in p.authors]:
                raise BurdenError(f"Paper {p.paper_id} does not contain requested author ID")

    @classmethod
    def from_dict(cls, data: dict):
        try:
            papers = []
            for item in data["papers"]:
                p = dict(item)
                p["authors"] = [Author(**a) for a in p["authors"]]
                p["reviews"] = [Review(
                    review_id=r["review_id"],
                    revisions=[ReviewRevision(**v) for v in r["revisions"]],
                    history_status=r.get("history_status", "provided"),
                ) for r in p.get("reviews", [])]
                p["author_roles"] = {key: [RoleEvidence(**r) for r in roles]
                                     for key, roles in p.get("author_roles", {}).items()}
                papers.append(Paper(**p))
            rest = {k: v for k, v in data.items() if k not in {"author", "papers", "coverage"}}
            return cls(author=Author(**data["author"]), papers=papers,
                       coverage=[Coverage(**c) for c in data["coverage"]], **rest)
        except (KeyError, TypeError, AttributeError) as exc:
            raise BurdenError(f"Invalid author snapshot: {exc}") from exc

    def to_dict(self):
        return asdict(self)
