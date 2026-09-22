"""Local SQLite scores and the fields the site reads. Source papers are not retained."""
import json
import math
import sqlite3
import zlib
from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .constants import SCORE_VERSION, SCORE_YEAR_MIN
from .dataset_adapter import (DATASET_ADAPTER_VERSION, apply_rating_adapter, normalize_paper,
                              rating_adapter, timestamp)
from .dataset_source import (REPOSITORY, SOURCE_DATE, YEARS, file_sha256, inspect_dataset,
                             iter_jsonl, note_kind)
from .errors import BurdenError, ScoreOverflowError
from .models import Coverage, Paper
from .scoring import (annual_burden, annotate_year_rows, calibrate_year, peak_scored_row,
                      paper_deficit_credit, score_paper, year_participates)
from .service import decision_counts
from .storage import content_hash
from .identity import (ensure_identity_schema, profile_status_conn, resolve_profile_id,
                       search_profiles)
from .identity_policy import (COVERAGE_NOTICE, display_name_from_profile_id, forum_url,
                              identity_coverage, paper_author_row, profile_url, public_author_row)
from .profiles import is_profile_id, public_output

DATABASE_VERSION = "iclr-local-db-v2"
DEFAULT_DATABASE = "data/iclr.sqlite3"
LEADERBOARD_MAX_K = 100
STAT_PERCENTILES = (0.5, 0.75, 0.9, 0.95, 0.99, 0.999)
TAIL_FRACTIONS = ((0.90, "Top 10%"), (0.95, "Top 5%"), (0.99, "Top 1%"), (0.999, "Top 0.1%"))
REFERENCE_FRACTIONS = (0.5, 0.75, 0.9, 0.99)
SCORE_BANDS = (
    (0.0, 0.25, "0–0.25"),
    (0.25, 0.5, "0.25–0.5"),
    (0.5, 1.0, "0.5–1"),
    (1.0, 2.0, "1–2"),
    (2.0, 3.0, "2–3"),
    (3.0, 5.0, "3–5"),
    (5.0, 8.0, "5–8"),
    (8.0, None, "8+"),
)
CCDF_SAMPLES = 160

SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE papers (paper_id TEXT PRIMARY KEY, year INTEGER NOT NULL);
CREATE INDEX papers_year ON papers(year);
CREATE TABLE authors (author_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                      identity_source TEXT NOT NULL, identity_ambiguous INTEGER NOT NULL);
CREATE TABLE author_names (author_id TEXT REFERENCES authors, name TEXT, folded TEXT,
                           PRIMARY KEY(author_id,name));
CREATE INDEX author_names_folded ON author_names(folded,author_id);
CREATE TABLE paper_authors (paper_id TEXT REFERENCES papers, author_id TEXT REFERENCES authors,
                           rank INTEGER NOT NULL, PRIMARY KEY(paper_id,author_id));
CREATE INDEX paper_authors_author ON paper_authors(author_id,paper_id,rank);
CREATE TABLE paper_scores (paper_id TEXT PRIMARY KEY REFERENCES papers, year INTEGER NOT NULL,
                           R REAL, deficit REAL, credit REAL, accepted INTEGER, desk_reject INTEGER,
                           decision TEXT, review_status TEXT, reviews TEXT NOT NULL);
CREATE INDEX paper_scores_year ON paper_scores(year,accepted,review_status);
CREATE TABLE calibrations (year INTEGER PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE author_years (author_id TEXT REFERENCES authors, year INTEGER, A REAL, S REAL,
                          payload TEXT NOT NULL, PRIMARY KEY(author_id,year));
CREATE TABLE author_scores (author_id TEXT PRIMARY KEY REFERENCES authors, year INTEGER,
                           A REAL, S REAL, N_bad INTEGER, status TEXT NOT NULL);
"""
# Present only while dataset-import is scoring. Dropped before the database is published.
PAPER_WORK_DDL = """
CREATE TABLE paper_work (paper_id TEXT PRIMARY KEY REFERENCES papers, normalized TEXT NOT NULL);
"""
# Scratch store for one source year. Deleted when that year has been scored into the tables above.
RAW_SCHEMA = """
CREATE TABLE raw_papers (year INTEGER, line INTEGER, note_id TEXT, forum TEXT, payload BLOB NOT NULL,
                         PRIMARY KEY(year,line));
CREATE INDEX raw_papers_forum ON raw_papers(year,forum);
CREATE TABLE raw_replies (year INTEGER, line INTEGER, note_id TEXT, forum TEXT, kind TEXT,
                         timestamp INTEGER, payload BLOB NOT NULL, PRIMARY KEY(year,line));
CREATE INDEX raw_replies_forum ON raw_replies(year,forum);
"""
_YEAR_COUNT_KEYS = ("submissions", "accepted", "rejected", "withdrawn", "desk_rejected",
                    "oral", "spotlight", "poster", "decided_acceptance_rate")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _slim_year_payload(row):
    """Keep the year fields the query API reads."""
    counts = row.get("counts") or {}
    kept = {}
    for key in _YEAR_COUNT_KEYS:
        if key == "decided_acceptance_rate":
            kept[key] = counts.get(key)
        else:
            kept[key] = counts.get(key, 0)
    return {
        "year": row["year"],
        "A": row.get("A"),
        "B": row.get("B"), "G": row.get("G"), "D": row.get("D"),
        "N_bad": row.get("N_bad"), "N_low": row.get("N_low"),
        "N_DR": row.get("N_DR"), "N_acc": row.get("N_acc"),
        "rho": row.get("rho"),
        "score_status": row.get("score_status"),
        "counts": kept,
    }


def _unresolved_reason(paper, rank):
    names, ids = paper.authors, paper.source_author_ids or []
    if len(names) != len(ids):
        return "authors_authorids_length_mismatch"
    raw = ids[rank - 1] if 0 <= rank - 1 < len(ids) else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "missing_or_null_authorids"
    if isinstance(raw, str) and "@" in raw and not is_profile_id(raw):
        return "email_not_profile_id"
    return "missing_or_unaligned_profile_id"


def _pack(value):
    return zlib.compress(_json(value).encode("utf-8"), level=1)


def _unpack(value):
    return json.loads(zlib.decompress(value))


def _set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, _json(value)))


def metadata(conn):
    return {row[0]: json.loads(row[1]) for row in conn.execute("SELECT key,value FROM metadata")}


def connect_database(path=DEFAULT_DATABASE, *, writable=False):
    file = Path(path).resolve()
    if not file.is_file():
        raise BurdenError(f"Local database not found: {file}. Run dataset-setup or dataset-import first.")
    conn = sqlite3.connect(file.as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if metadata(conn).get("database_version") != DATABASE_VERSION:
        conn.close()
        raise BurdenError("Unsupported local database schema")
    return conn


def build_database(source_root, db_path=DEFAULT_DATABASE, *, years=YEARS,
                   source_date=SOURCE_DATE, progress=None):
    years = sorted(set(years))
    if not years or any(y not in YEARS for y in years):
        raise BurdenError("Dataset adapter supports ICLR 2020–2026")
    source = Path(source_root)
    files = [{"path": f"iclr_{y}/{name}.jsonl", "sha256": file_sha256(source / f"iclr_{y}/{name}.jsonl")}
             for y in years for name in ("papers", "reviews")]
    snapshot_id = "sha256:" + content_hash({"files": files, "source_date": source_date})
    db = Path(db_path)
    if db.exists():
        existing = connect_database(db)
        try:
            meta = metadata(existing)
            if meta.get("source_snapshot") == snapshot_id and meta.get("dataset_adapter") == DATASET_ADAPTER_VERSION:
                return meta["import_summary"]
        finally:
            existing.close()
        raise BurdenError("Database already contains a different snapshot/adapter; use a new --db path")
    # Audit all actual fields before constructing normalized representations.
    audit = inspect_dataset(source, years, progress=progress)
    db.parent.mkdir(parents=True, exist_ok=True)
    staging = db.with_name(db.name + ".building")
    raw_path = staging.with_name(staging.name + ".raw")
    if staging.exists() or raw_path.exists():
        raise BurdenError(f"Unfinished database build exists: {staging}; inspect it before retrying")
    conn = None
    raw = None
    try:
        conn = sqlite3.connect(staging)
        raw = sqlite3.connect(raw_path)
        raw.execute("PRAGMA auto_vacuum=INCREMENTAL")
        raw.executescript(RAW_SCHEMA)
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(PAPER_WORK_DDL)
        ensure_identity_schema(conn)
        _set_meta(conn, "database_version", DATABASE_VERSION)
        _set_meta(conn, "source_snapshot", snapshot_id)
        _set_meta(conn, "source_date", source_date)
        _set_meta(conn, "repository", REPOSITORY)
        _set_meta(conn, "dataset_adapter", DATASET_ADAPTER_VERSION)
        _set_meta(conn, "source_files", files)
        _set_meta(conn, "schema_audit", audit)
        _set_meta(conn, "years", years)
        _set_meta(conn, "identity_policy", identity_coverage())
        summaries = {}
        for year in years:
            raw.execute("DELETE FROM raw_papers")
            raw.execute("DELETE FROM raw_replies")
            folder = source / f"iclr_{year}"
            for line, note in iter_jsonl(folder / "papers.jsonl"):
                raw.execute("INSERT INTO raw_papers VALUES (?,?,?,?,?)",
                            (year, line, note["id"], note.get("forum") or note["id"], _pack(note)))
            reply_count = 0
            for line, note in iter_jsonl(folder / "reviews.jsonl"):
                if note.get("_year", year) != year:
                    raise BurdenError(f"Year mismatch in {folder}/reviews.jsonl:{line}")
                forum = note.get("_paper_forum") or note.get("forum")
                if note.get("forum") and note.get("forum") != forum:
                    raise BurdenError(f"Conflicting forum identifiers at {year}:{line}")
                raw.execute("INSERT INTO raw_replies VALUES (?,?,?,?,?,?,?)",
                            (year, line, note["id"], forum, note_kind(note), timestamp(note), _pack(note)))
                reply_count += 1
            raw.commit()
            paper_count = 0
            seen = set()
            for line, forum, payload in raw.execute("SELECT line,forum,payload FROM raw_papers WHERE year=? ORDER BY line", (year,)):
                if forum in seen:
                    raise BurdenError(f"Duplicate paper forum in source: {forum}; explicit reconciliation required")
                seen.add(forum)
                replies = [_unpack(r[0]) for r in raw.execute(
                    "SELECT payload FROM raw_replies WHERE year=? AND forum=? ORDER BY timestamp,line", (year, forum))]
                p = normalize_paper(_unpack(payload), replies, year, source_snapshot=snapshot_id, source_line=line)
                conn.execute("INSERT INTO papers VALUES (?,?)", (p.paper_id, year))
                conn.execute("INSERT INTO paper_work VALUES (?,?)", (p.paper_id, _json(asdict(p))))
                for rank, author in enumerate(p.authors, 1):
                    if not is_profile_id(author.author_id) or author.identity_source != "openreview_id":
                        conn.execute("INSERT INTO unresolved_paper_authors VALUES (?,?,?,?)",
                                     (p.paper_id, rank, author.display_name, _unresolved_reason(p, rank)))
                        continue
                    conn.execute("INSERT OR IGNORE INTO authors VALUES (?,?,?,?)",
                                 (author.author_id, author.display_name, author.identity_source, author.identity_ambiguous))
                    conn.execute("INSERT OR IGNORE INTO author_names VALUES (?,?,?)",
                                 (author.author_id, author.display_name, author.display_name.casefold()))
                    conn.execute("INSERT INTO paper_authors VALUES (?,?,?)", (p.paper_id, author.author_id, rank))
                    conn.execute("INSERT INTO paper_author_sources VALUES (?,?,?)", (p.paper_id, author.author_id, rank))
                paper_count += 1
            orphan_count = raw.execute("SELECT COUNT(*) FROM raw_replies r WHERE r.year=? AND NOT EXISTS "
                                       "(SELECT 1 FROM raw_papers p WHERE p.year=r.year AND p.forum=r.forum)", (year,)).fetchone()[0]
            summaries[str(year)] = {"papers": paper_count, "replies": reply_count, "orphan_replies": orphan_count,
                                    "official_reviews": audit["years"][str(year)]["reply_kinds"].get("Official_Review", 0)}
            conn.commit()
            raw.execute("DELETE FROM raw_papers")
            raw.execute("DELETE FROM raw_replies")
            raw.commit()
            raw.execute("PRAGMA incremental_vacuum")
            if progress:
                progress(f"Imported {year}: {paper_count} papers, {reply_count} replies ({orphan_count} orphan replies not stored)")
        summary = {"years": summaries,
                   "papers": conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
                   "replies": sum(item["replies"] for item in summaries.values()),
                   "authors": conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0],
                   "profile_authors": conn.execute("SELECT COUNT(*) FROM authors WHERE identity_ambiguous=0").fetchone()[0],
                   "source_snapshot": snapshot_id, "source_date": source_date}
        _set_meta(conn, "import_summary", summary)
        _set_meta(conn, "import_status", "complete")
        conn.commit()
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BurdenError("SQLite integrity check failed")
        conn.close()
        raw.close()
        conn = None
        raw = None
        raw_path.unlink(missing_ok=True)
        Path(str(raw_path) + "-journal").unlink(missing_ok=True)
        staging.rename(db)
        return summary
    except Exception:
        if conn is not None:
            conn.close()
        if raw is not None:
            raw.close()
        # Only discard the private staging files created by this failed build.
        staging.unlink(missing_ok=True)
        Path(str(staging) + "-journal").unlink(missing_ok=True)
        raw_path.unlink(missing_ok=True)
        Path(str(raw_path) + "-journal").unlink(missing_ok=True)
        raise


def score_years(years):
    return [year for year in years if year >= SCORE_YEAR_MIN]


def scored_years(meta):
    calc = meta.get("calculation") or {}
    years = calc.get("years")
    if years:
        return list(years)
    return score_years(meta.get("years") or [])


def _coverage(meta, first_year, observed_years):
    rows = []
    first_year = max(first_year, SCORE_YEAR_MIN)
    for year in score_years(meta["years"]):
        if year < first_year:
            continue
        audit = meta["schema_audit"]["years"][str(year)]
        # The old dump omits some submissions and unresolved identities can hide
        # participation. Absence in such a year is NOT evidence of a zero year.
        reliable_absence = (year >= 2024 and audit["counts"].get("non_profile_ids", 0) == 0
                            and audit["counts"].get("orphan_replies", 0) == 0
                            and audit["counts"].get("papers_with_missing_authors", 0) == 0)
        rows.append(Coverage(year, year in observed_years or reliable_absence, meta["source_snapshot"],
                             "dataset_snapshot_exact_identity_only"))
    return rows


def calculate_all(db_path=DEFAULT_DATABASE, *, progress=None, replace_score_version=False):
    """Calculate paper scores, 2024+ calibrations and all local author identities."""
    conn = connect_database(db_path, writable=True)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_work'").fetchone() is None:
            raise BurdenError(
                "This database stores scores and the fields the site reads. "
                "Source papers are not retained. Run dataset-import or dataset-setup to rebuild scores.")
        meta = metadata(conn)
        previous = meta.get("calculation")
        existing_version = (previous or {}).get("score_version")
        if existing_version and existing_version != SCORE_VERSION and not replace_score_version:
            raise BurdenError(
                f"Database already has {existing_version} scores; refusing to overwrite with {SCORE_VERSION}. "
                "Pass --replace-score-version to recompute explicitly.")
        with conn:
            for table in ("author_scores", "author_years", "calibrations", "paper_scores"):
                conn.execute(f"DELETE FROM {table}")
            for year in score_years(meta["years"]):
                accepted = []
                for row in conn.execute(
                        """SELECT w.paper_id, w.normalized FROM paper_work w JOIN papers p USING(paper_id)
                           WHERE p.year=? ORDER BY w.paper_id""", (year,)):
                    payload, changed = apply_rating_adapter(json.loads(row[1]))
                    if changed:
                        conn.execute("UPDATE paper_work SET normalized=? WHERE paper_id=?",
                                     (_json(payload), row[0]))
                    p = Paper.from_dict(payload)
                    audit = score_paper(p)
                    if audit["is_accepted"] and audit["R_p"] is not None:
                        accepted.append(audit["R_p"])
                    conn.execute("INSERT INTO paper_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (p.paper_id, year, audit["R_p"], None, None, audit["is_accepted"], audit["is_desk_reject"],
                                  audit["decision_normalized"], audit["review_data_status"],
                                  _json(_compact_reviews(audit))))
                cal = calibrate_year(year, accepted, meta["source_snapshot"], rating_adapter(year)[0])
                conn.execute("INSERT INTO calibrations VALUES (?,?)", (year, _json(asdict(cal))))
                for row in conn.execute(
                        "SELECT paper_id,R,accepted FROM paper_scores WHERE year=? AND R IS NOT NULL", (year,)):
                    deficit, credit = paper_deficit_credit(row[1], cal, is_accepted=bool(row[2]))
                    conn.execute("UPDATE paper_scores SET deficit=?,credit=? WHERE paper_id=?",
                                 (deficit, credit, row[0]))
                if progress:
                    progress(f"Calibrated {year}: accepted n={len(accepted)}, T={cal.threshold_T:.4f}, H={cal.threshold_H:.4f}")
            cal_rows = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT year,payload FROM calibrations")}
            # Indexed lightweight join; do not serialize each author's repeated paper/review text.
            query = """SELECT pa.author_id,pa.rank,s.paper_id,s.year,s.deficit,s.credit,s.desk_reject,s.decision,s.review_status,s.accepted
                       FROM paper_authors pa JOIN paper_scores s USING(paper_id)
                       WHERE pa.author_id GLOB '~*' AND instr(pa.author_id,'@')=0 AND s.year>=?
                       ORDER BY pa.author_id,s.year,s.paper_id"""
            current_id, group, count, overflow = None, [], 0, 0

            def persist(author_id, records):
                nonlocal count, overflow
                by_year = defaultdict(list)
                for record in records:
                    by_year[record["year"]].append(record)
                coverage = _coverage(meta, min(by_year), set(by_year))
                rows = []
                try:
                    for cov in coverage:
                        ps = by_year[cov.year]
                        contributions = [{"rank": r["rank"], "deficit": r["deficit"] or 0.0,
                                          "credit": r["credit"] or 0.0, "is_desk_reject": bool(r["desk_reject"]),
                                          "is_accepted": bool(r["accepted"])} for r in ps]
                        components = annual_burden(contributions)
                        observed = components["A"]
                        if not cov.complete:
                            components["A"] = None
                        counts = decision_counts([{"decision_normalized": r["decision"],
                                                   "review_data_status": r["review_status"], "p_p": r["deficit"]} for r in ps])
                        rows.append({"year": cov.year, **components, "observed_base_burden": observed,
                                     "score_status": "complete" if cov.complete else "incomplete_coverage",
                                     "coverage": asdict(cov), "counts": counts,
                                     "paper_ids": [r["paper_id"] for r in ps], "calibration": cal_rows[cov.year]})
                    rows = annotate_year_rows(rows)
                    for row in rows:
                        conn.execute("INSERT INTO author_years VALUES (?,?,?,?,?)",
                                     (author_id, row["year"], row["A"], None, _json(_slim_year_payload(row))))
                    peak = peak_scored_row(rows)
                    peak_score = peak["A"] if year_participates(peak) else None
                    conn.execute("INSERT INTO author_scores VALUES (?,?,?,?,?,?)",
                                 (author_id, peak["year"], peak_score, peak_score, peak["N_bad"],
                                  "snapshot_only" if peak_score is not None else "incomplete_coverage"))
                except ScoreOverflowError:
                    conn.execute("INSERT INTO author_scores VALUES (?,?,?,?,?,?)",
                                 (author_id, max(score_years(meta["years"])), None, None, None, "score_overflow"))
                    overflow += 1
                count += 1
                if progress and count % 10000 == 0:
                    progress(f"Calculated {count} local author identities")

            for record in conn.execute(query, (SCORE_YEAR_MIN,)):
                if current_id is not None and record["author_id"] != current_id:
                    persist(current_id, group)
                    group = []
                current_id = record["author_id"]
                group.append(record)
            if current_id is not None:
                persist(current_id, group)
            summary = {"score_version": SCORE_VERSION, "dataset_adapter": DATASET_ADAPTER_VERSION,
                       "source_snapshot": meta["source_snapshot"], "authors_computed": count,
                       "overflow_authors": overflow,
                       "score_year_min": SCORE_YEAR_MIN,
                       "years": score_years(meta["years"]),
                       "papers_computed": conn.execute("SELECT COUNT(*) FROM paper_scores").fetchone()[0],
                       "author_year_rows": conn.execute("SELECT COUNT(*) FROM author_years").fetchone()[0],
                       "computed_at": datetime.now(timezone.utc).isoformat(),
                       "replaced_score_version": existing_version if existing_version and existing_version != SCORE_VERSION else None,
                       "previous_calculation": previous if existing_version and existing_version != SCORE_VERSION else None}
            _set_meta(conn, "calculation", summary)
            if existing_version and existing_version != SCORE_VERSION:
                _set_meta(conn, "previous_calculation", previous)
        conn.execute("DROP TABLE paper_work")
        conn.commit()
        conn.execute("VACUUM")
        return summary
    finally:
        conn.close()


def _ordered_quantile(ordered, fraction):
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def _survival_at(ordered, threshold):
    count = len(ordered) - bisect_left(ordered, threshold)
    share = count / len(ordered) if ordered else 0.0
    return count, share


def _percent_label(fraction):
    percent = fraction * 100
    if abs(percent - round(percent)) < 1e-9:
        return f"{int(round(percent))}%"
    return f"{percent:g}%"


def _axis_ceiling(ordered):
    """Linear score axis through the top 0.1% cutoff, so the extreme maximum does not flatten the curve."""
    if not ordered:
        return 1.0
    high = _ordered_quantile(ordered, 0.999)
    return float(max(8.0, math.ceil(high * 1.12 - 1e-9)))


def _distribution_views(ordered):
    n = len(ordered)
    if n == 0:
        return {"percentiles": [], "references": [], "ccdf": [], "bands": [], "tail": [], "x_max": 1.0}
    x_max = _axis_ceiling(ordered)

    def point(fraction):
        cutoff = _ordered_quantile(ordered, fraction)
        count, share = _survival_at(ordered, cutoff)
        return {"p": fraction, "label": _percent_label(fraction), "value": cutoff,
                "count": count, "share": share}

    percentiles = [point(fraction) for fraction in STAT_PERCENTILES]
    references = [point(fraction) for fraction in REFERENCE_FRACTIONS]
    thresholds = {0.0, x_max}
    for marker in percentiles:
        if 0 <= marker["value"] <= x_max:
            thresholds.add(marker["value"])
    for index in range(CCDF_SAMPLES + 1):
        thresholds.add(x_max * index / CCDF_SAMPLES)
    ccdf = []
    for threshold in sorted(thresholds):
        count, share = _survival_at(ordered, threshold)
        ccdf.append({"threshold": threshold, "count": count, "share": share})
    bands = []
    for start, end, label in SCORE_BANDS:
        lo = bisect_left(ordered, start)
        hi = n if end is None else bisect_left(ordered, end)
        count = max(0, hi - lo)
        bands.append({"label": label, "start": start, "end": end, "count": count, "share": count / n})
    tail = []
    for fraction, label in TAIL_FRACTIONS:
        row = point(fraction)
        row["label"] = label
        tail.append(row)
    return {"percentiles": percentiles, "references": references, "ccdf": ccdf,
            "bands": bands, "tail": tail, "x_max": x_max}


def score_distribution(metric="S", year=None, db_path=DEFAULT_DATABASE):
    """Survival curve, score bands, and tail cutoffs for one score view."""
    metric = (metric or "S").upper()
    conn = connect_database(db_path)
    try:
        meta = _require_cached_scores(conn)
        if metric == "S":
            values = [row[0] for row in conn.execute(
                "SELECT S FROM author_scores WHERE S IS NOT NULL AND author_id GLOB '~*'")]
        elif metric == "A":
            if type(year) is not int:
                raise BurdenError("Stats by A requires an integer year")
            if year not in set(scored_years(meta)):
                raise BurdenError(f"Year {year} is outside the 2024+ scoring window")
            values = [row[0] for row in conn.execute(
                """SELECT A FROM author_years
                   WHERE year=? AND A IS NOT NULL
                     AND CAST(json_extract(payload, '$.counts.submissions') AS INTEGER) > 0""",
                (year,))]
        else:
            raise BurdenError("Stats metric must be S or A")
        ordered = sorted(values)
        n = len(ordered)
        views = _distribution_views(ordered)
        body = {
            "metric": metric,
            "n": n,
            "min": ordered[0] if n else None,
            "max": ordered[-1] if n else None,
            "mean": math.fsum(ordered) / n if n else None,
            "x_max": views["x_max"],
            "axis_truncated": bool(n and ordered[-1] > views["x_max"]),
            "percentiles": views["percentiles"],
            "references": views["references"],
            "ccdf": views["ccdf"],
            "bands": views["bands"],
            "tail": views["tail"],
            "score_version": SCORE_VERSION,
            "scope": "dataset_snapshot_only",
            "data_quality": ["author_identity_coverage_limited", "dataset_snapshot_only"],
            "metadata": {"lookup_source": "local_sqlite_cached_scores",
                         "source_snapshot": meta["source_snapshot"],
                         "identity_policy": COVERAGE_NOTICE},
        }
        if metric == "A":
            body["year"] = year
        return public_output(body)
    finally:
        conn.close()


def _compact_reviews(audit):
    reviews = []
    for review in audit.get("reviews") or []:
        score, confidence = review.get("rating_numeric"), review.get("confidence_numeric")
        if score is None or confidence is None:
            continue
        reviews.append({"score": score, "confidence": confidence})
    reviews.sort(key=lambda item: (-item["score"], -item["confidence"]))
    return reviews


def _compact_paper(row):
    paper_id = row["paper_id"]
    return {
        "paper_id": paper_id,
        "year": row["year"],
        "decision": row["decision"],
        "R_p": row["R"],
        "p_p": row["deficit"],
        "g_p": row["credit"],
        "reviews": json.loads(row["reviews"]),
        "forum_url": forum_url(paper_id),
    }


def _author_papers(conn, author_id):
    rows = conn.execute(
        """SELECT s.paper_id,s.year,s.R,s.deficit,s.credit,s.decision,s.reviews
           FROM paper_authors pa JOIN paper_scores s USING(paper_id)
           WHERE pa.author_id=? AND s.year>=?""",
        (author_id, SCORE_YEAR_MIN))
    papers = [_compact_paper(row) for row in rows]
    papers.sort(key=lambda item: (item["R_p"] is None, -(item["R_p"] or 0), -item["year"], item["paper_id"]))
    return papers


def _compact_year(row):
    counts = row.get("counts") or {}
    return {
        "year": row["year"],
        "A": row.get("A"),
        "B": row.get("B"), "G": row.get("G"),
        "D": row.get("D"), "N_bad": row.get("N_bad"),
        "N_low": row.get("N_low"), "N_DR": row.get("N_DR"), "N_acc": row.get("N_acc"),
        "rho": row.get("rho"),
        "papers": counts.get("submissions", 0),
        "accepted": counts.get("accepted", 0), "rejected": counts.get("rejected", 0),
        "withdrawn": counts.get("withdrawn", 0), "desk_rejected": counts.get("desk_rejected", 0),
        "oral": counts.get("oral", 0), "spotlight": counts.get("spotlight", 0), "poster": counts.get("poster", 0),
        "decided_acceptance_rate": counts.get("decided_acceptance_rate"),
        "score_status": row.get("score_status"),
    }


def _compact_summary(years):
    keys = ("papers", "accepted", "rejected", "withdrawn", "desk_rejected", "oral", "spotlight", "poster")
    total = {key: 0 for key in keys}
    for row in years:
        for key in keys:
            total[key] += row.get(key) or 0
    decided = total["accepted"] + total["rejected"] + total["desk_rejected"]
    total["submissions"] = total["papers"]
    total["decided_papers"] = decided
    total["decided_acceptance_rate"] = total["accepted"] / decided if decided else None
    return total


def _require_cached_scores(conn):
    meta = metadata(conn)
    if meta.get("calculation", {}).get("score_version") != SCORE_VERSION:
        raise BurdenError("Local score cache is missing or incompatible; run compute-all first")
    return meta


def _author_header(author_id):
    return public_author_row(author_id)


def _has_submissions(counts):
    return (counts or {}).get("submissions", 0) > 0


def _score_cards(conn, author_ids):
    ids = list(dict.fromkeys(author_ids))
    cards = {identifier: {"S": None, "year_scores": [], "accepted": 0, "submissions": 0} for identifier in ids}
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    for row in conn.execute(f"SELECT author_id,S FROM author_scores WHERE author_id IN ({placeholders})", ids):
        cards[row["author_id"]]["S"] = row["S"]
    for row in conn.execute(
            f"""SELECT author_id,year,A,payload FROM author_years
                WHERE author_id IN ({placeholders}) ORDER BY year DESC""", ids):
        counts = (json.loads(row["payload"]) or {}).get("counts") or {}
        if _has_submissions(counts):
            cards[row["author_id"]]["year_scores"].append({"year": row["year"], "A": row["A"]})
        cards[row["author_id"]]["accepted"] += counts.get("accepted", 0)
        cards[row["author_id"]]["submissions"] += counts.get("submissions", 0)
    return {
        identifier: {
            **public_author_row(identifier),
            "S": cards[identifier]["S"],
            "year_scores": cards[identifier]["year_scores"],
            "accepted": cards[identifier]["accepted"],
            "submissions": cards[identifier]["submissions"],
        }
        for identifier in ids
    }


def _attach_score_cards(rows, conn):
    cards = _score_cards(conn, [row["canonical_profile_id"] for row in rows])
    attached = []
    for row in rows:
        card = cards[row["canonical_profile_id"]]
        attached.append({
            **row,
            "S": row.get("S", card["S"]),
            "year_scores": card["year_scores"],
            "accepted": row["accepted"] if "accepted" in row else card["accepted"],
            "submissions": row["submissions"] if "submissions" in row else card["submissions"],
        })
    return attached


def _leaderboard_k(k):
    if type(k) is not int or not 1 <= k <= LEADERBOARD_MAX_K:
        raise BurdenError(f"Leaderboard --k must be an integer within 1–{LEADERBOARD_MAX_K}")
    return k


def _ranking_payload(metric, ranking, meta, extra=None):
    body = {
        "metric": metric,
        "k": extra.pop("k") if extra and "k" in extra else len(ranking),
        "returned": len(ranking),
        "ranking": ranking,
        "score_version": SCORE_VERSION,
        "scope": "dataset_snapshot_only",
        "data_quality": ["author_identity_coverage_limited", "dataset_snapshot_only"],
        "metadata": {"lookup_source": "local_sqlite_cached_scores", "source_snapshot": meta["source_snapshot"],
                     "identity_policy": COVERAGE_NOTICE},
    }
    if extra:
        body.update(extra)
    return public_output(body)


def top_s(k=10, db_path=DEFAULT_DATABASE):
    """Ranking by peak annual score (highest A across scored years)."""
    k = _leaderboard_k(k)
    conn = connect_database(db_path)
    try:
        meta = _require_cached_scores(conn)
        rows = conn.execute(
            """SELECT author_id, year, A, S, N_bad
               FROM author_scores
               WHERE S IS NOT NULL
               ORDER BY S DESC, author_id
               LIMIT ?""",
            (k,))
        ranking = []
        for rank, row in enumerate(rows, 1):
            ranking.append({
                "rank": rank,
                **_author_header(row["author_id"]),
                "year": row["year"],
                "A": row["A"],
                "S": row["S"],
                "N_bad": row["N_bad"],
            })
        ranking = _attach_score_cards(ranking, conn)
        return _ranking_payload("S", ranking, meta, {"k": k})
    finally:
        conn.close()


def top_a(year, k=10, db_path=DEFAULT_DATABASE):
    """Annual A ranking for one conference year."""
    k = _leaderboard_k(k)
    if type(year) is not int:
        raise BurdenError("top-a requires an integer --year")
    conn = connect_database(db_path)
    try:
        meta = _require_cached_scores(conn)
        if year not in set(scored_years(meta)):
            raise BurdenError(f"Year {year} is outside the 2024+ scoring window")
        rows = conn.execute(
            """SELECT y.author_id, y.year, y.A, y.payload
               FROM author_years y
               WHERE y.year=? AND y.A IS NOT NULL
               ORDER BY y.A DESC, y.author_id
               LIMIT ?""",
            (year, k))
        ranking = []
        for rank, row in enumerate(rows, 1):
            payload = json.loads(row["payload"])
            ranking.append({
                "rank": rank,
                **_author_header(row["author_id"]),
                "year": row["year"],
                "A": row["A"],
                "N_bad": payload.get("N_bad"),
            })
        ranking = _attach_score_cards(ranking, conn)
        return _ranking_payload("A", ranking, meta, {"k": k, "year": year})
    finally:
        conn.close()


def get_author_report(author_id, db_path=DEFAULT_DATABASE):
    conn = connect_database(db_path)
    try:
        author_id = resolve_profile_id(conn, author_id)
        meta = _require_cached_scores(conn)
        row = conn.execute("SELECT author_id FROM authors WHERE author_id=?", (author_id,)).fetchone()
        if row is None:
            raise BurdenError(f"Author identity not present in local dataset: {author_id}")
        stored = [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM author_years WHERE author_id=? ORDER BY year", (author_id,))]
        score = conn.execute("SELECT year,A,S,N_bad,status FROM author_scores WHERE author_id=?", (author_id,)).fetchone()
        if not stored or score is None:
            raise BurdenError("No cached score for this author")
        papers = _author_papers(conn, author_id)
        by_year = defaultdict(list)
        for paper in papers:
            by_year[paper["year"]].append(paper)
        years = []
        for item in stored:
            compact = _compact_year(item)
            compact["paper_list"] = by_year.get(compact["year"], [])
            years.append(compact)
        current = next((item for item in years if item["year"] == score["year"]), years[-1])
        visible_years = [item for item in years if item.get("papers", 0) > 0]
        header = _author_header(author_id)
        return public_output({
            "author": header,
            "header": header,
            "display_name": display_name_from_profile_id(author_id),
            "profile_url": profile_url(author_id),
            "summary": _compact_summary(visible_years),
            "current_score": {"year": score["year"], "A": score["A"], "S": score["S"],
                              "N_bad": score["N_bad"],
                              "score_status": current["score_status"],
                              "scope": "dataset_snapshot_only"},
            "years": visible_years,
            "papers": papers,
            "data_quality": ["author_identity_coverage_limited", "dataset_snapshot_only"],
            "metadata": {"lookup_source": "local_sqlite_cached_scores", "source_snapshot": meta["source_snapshot"],
                         "identity_policy": COVERAGE_NOTICE},
        })
    finally:
        conn.close()


def search_authors(query, db_path=DEFAULT_DATABASE, *, limit=20):
    if not query.strip() or not 1 <= limit <= 100:
        raise BurdenError("Search requires text and limit within 1–100")
    conn = connect_database(db_path)
    try:
        hits = search_profiles(conn, query, limit=limit)
        ids = [row["canonical_profile_id"] for row in hits]
        if ids:
            placeholders = ",".join("?" * len(ids))
            visible = {row[0] for row in conn.execute(
                f"""SELECT DISTINCT pa.author_id
                    FROM paper_authors pa JOIN papers p USING(paper_id)
                    WHERE pa.author_id IN ({placeholders}) AND p.year>=?""",
                (*ids, SCORE_YEAR_MIN))}
            ids = [identifier for identifier in ids if identifier in visible]
        cards = _score_cards(conn, ids)
        return public_output([cards[identifier] for identifier in ids])
    finally:
        conn.close()


def get_paper_record(paper_id, db_path=DEFAULT_DATABASE):
    conn = connect_database(db_path)
    try:
        row = conn.execute(
            """SELECT paper_id,year,R,deficit,credit,decision,reviews
               FROM paper_scores WHERE paper_id=?""", (paper_id,)).fetchone()
        if row is None:
            raise BurdenError("Paper not found in calculated local database")
        author_ids = [item[0] for item in conn.execute(
            "SELECT author_id FROM paper_authors WHERE paper_id=? ORDER BY rank", (paper_id,))]
        return public_output({
            "paper_id": row["paper_id"],
            "year": row["year"],
            "decision": row["decision"],
            "R_p": row["R"],
            "p_p": row["deficit"],
            "g_p": row["credit"],
            "reviews": json.loads(row["reviews"]),
            "forum_url": forum_url(row["paper_id"]),
            "author_ids": author_ids,
            "author_identities": [paper_author_row(identifier) for identifier in author_ids],
        })
    finally:
        conn.close()


def export_lightweight(source_path, dest_path):
    """Copy a v1 database down to the fields the site reads."""
    source = Path(source_path).resolve()
    dest = Path(dest_path).resolve()
    if dest.exists():
        raise BurdenError(f"Refusing to replace existing database: {dest}")
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(dest)
    try:
        columns = {row[1] for row in src.execute("PRAGMA table_info(paper_scores)")}
        if "audit" not in columns:
            raise BurdenError("Source database is already lightweight or has an unexpected paper_scores schema")
        dst.execute("PRAGMA foreign_keys=ON")
        dst.executescript(SCHEMA)
        _set_meta(dst, "database_version", DATABASE_VERSION)
        ensure_identity_schema(dst)
        with dst:
            dst.executemany("INSERT INTO papers VALUES (?,?)",
                            src.execute("SELECT paper_id, year FROM papers ORDER BY paper_id"))
            dst.executemany("INSERT INTO authors VALUES (?,?,?,?)",
                            src.execute("SELECT author_id, display_name, identity_source, identity_ambiguous FROM authors"))
            dst.executemany("INSERT INTO author_names VALUES (?,?,?)",
                            src.execute("SELECT author_id, name, folded FROM author_names"))
            dst.executemany("INSERT INTO paper_authors VALUES (?,?,?)",
                            src.execute("SELECT paper_id, author_id, rank FROM paper_authors"))
            dst.executemany("INSERT INTO paper_author_sources VALUES (?,?,?)",
                            src.execute("SELECT paper_id, source_profile_id, rank FROM paper_author_sources"))
            dst.executemany("INSERT INTO unresolved_paper_authors VALUES (?,?,?,?)",
                            src.execute("SELECT paper_id, rank, display_name, reason FROM unresolved_paper_authors"))
            score_rows = []
            for row in src.execute(
                    """SELECT paper_id, year, R, deficit, credit, accepted, desk_reject, decision, review_status, audit
                       FROM paper_scores"""):
                reviews = _json(_compact_reviews(json.loads(row["audit"])))
                score_rows.append((row["paper_id"], row["year"], row["R"], row["deficit"], row["credit"],
                                   row["accepted"], row["desk_reject"], row["decision"], row["review_status"], reviews))
                if len(score_rows) >= 2000:
                    dst.executemany("INSERT INTO paper_scores VALUES (?,?,?,?,?,?,?,?,?,?)", score_rows)
                    score_rows.clear()
            if score_rows:
                dst.executemany("INSERT INTO paper_scores VALUES (?,?,?,?,?,?,?,?,?,?)", score_rows)
            dst.executemany("INSERT INTO calibrations VALUES (?,?)",
                            src.execute("SELECT year, payload FROM calibrations"))
            year_rows = []
            for row in src.execute("SELECT author_id, year, A, S, payload FROM author_years"):
                year_rows.append((row["author_id"], row["year"], row["A"], row["S"],
                                  _json(_slim_year_payload(json.loads(row["payload"])))))
                if len(year_rows) >= 2000:
                    dst.executemany("INSERT INTO author_years VALUES (?,?,?,?,?)", year_rows)
                    year_rows.clear()
            if year_rows:
                dst.executemany("INSERT INTO author_years VALUES (?,?,?,?,?)", year_rows)
            dst.executemany("INSERT INTO author_scores VALUES (?,?,?,?,?,?)",
                            src.execute("SELECT author_id, year, A, S, N_bad, status FROM author_scores"))
            for key, value in src.execute("SELECT key, value FROM metadata"):
                if key == "database_version":
                    continue
                dst.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, value))
            _set_meta(dst, "database_version", DATABASE_VERSION)
        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BurdenError("SQLite integrity check failed")
        dst.execute("VACUUM")
    except Exception:
        dst.close()
        src.close()
        dest.unlink(missing_ok=True)
        Path(str(dest) + "-journal").unlink(missing_ok=True)
        raise
    else:
        dst.close()
        src.close()


def database_status(db_path=DEFAULT_DATABASE):
    conn = connect_database(db_path)
    try:
        meta = metadata(conn)
        result = {key: meta.get(key) for key in ("database_version", "repository", "dataset_adapter", "source_date",
                                               "source_snapshot", "import_summary", "calculation")}
        result["source_years"] = meta.get("years")
        result["years"] = scored_years(meta)
        result["score_year_min"] = SCORE_YEAR_MIN
        result["identity"] = profile_status_conn(conn)
        result["identity_coverage"] = identity_coverage(conn)
        result["current_cached_author_years"] = conn.execute("SELECT COUNT(*) FROM author_years").fetchone()[0]
        return result
    finally:
        conn.close()
