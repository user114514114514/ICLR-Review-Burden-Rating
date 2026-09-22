"""Canonical Profile ID lookup and name search over the local snapshot."""
import json
from dataclasses import replace

from .errors import BurdenError
from .models import Author
from .identity_policy import public_author_row
from .name_search import load_name_index, match_rank
from .profiles import is_profile_id, public_output

IDENTITY_VERSION = "profile-identity-v1"

DDL = [
    """CREATE TABLE IF NOT EXISTS author_profiles (
        canonical_profile_id TEXT PRIMARY KEY CHECK(canonical_profile_id GLOB '~*'),
        preferred_name TEXT, current_institution TEXT NOT NULL, current_position TEXT NOT NULL,
        payload TEXT NOT NULL, fetched_at TEXT NOT NULL, snapshot_hash TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS profile_aliases (
        source_profile_id TEXT PRIMARY KEY CHECK(source_profile_id GLOB '~*'),
        canonical_profile_id TEXT NOT NULL REFERENCES author_profiles(canonical_profile_id),
        evidence_hash TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS profile_aliases_canonical ON profile_aliases(canonical_profile_id)",
    """CREATE TABLE IF NOT EXISTS profile_names (
        canonical_profile_id TEXT REFERENCES author_profiles(canonical_profile_id), name TEXT, folded TEXT,
        PRIMARY KEY(canonical_profile_id,name))""",
    "CREATE INDEX IF NOT EXISTS profile_names_folded ON profile_names(folded,canonical_profile_id)",
    """CREATE TABLE IF NOT EXISTS paper_author_sources (
        paper_id TEXT REFERENCES papers, source_profile_id TEXT CHECK(source_profile_id GLOB '~*'), rank INTEGER NOT NULL,
        PRIMARY KEY(paper_id,source_profile_id))""",
    "CREATE INDEX IF NOT EXISTS paper_author_sources_id ON paper_author_sources(source_profile_id,paper_id)",
    """CREATE TABLE IF NOT EXISTS unresolved_paper_authors (
        paper_id TEXT REFERENCES papers, rank INTEGER, display_name TEXT, reason TEXT,
        PRIMARY KEY(paper_id,rank))""",
]


def has_identity_schema(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='author_profiles'").fetchone() is not None


def ensure_identity_schema(conn):
    migrated = conn.execute("SELECT 1 FROM metadata WHERE key='identity_version'").fetchone()
    for statement in DDL:
        conn.execute(statement)
    if not migrated:
        conn.execute("""INSERT OR IGNORE INTO paper_author_sources
                        SELECT paper_id,author_id,rank FROM paper_authors WHERE author_id GLOB '~*' AND instr(author_id,'@')=0""")
    conn.execute("""INSERT OR IGNORE INTO unresolved_paper_authors
                    SELECT p.paper_id,p.rank,a.display_name,'missing_or_unaligned_profile_id'
                    FROM paper_authors p JOIN authors a USING(author_id) WHERE a.identity_source!='openreview_id'""")
    for table in ("author_years", "author_scores", "paper_authors", "author_names"):
        conn.execute(f"DELETE FROM {table} WHERE author_id NOT GLOB '~*' OR instr(author_id,'@')>0")
    conn.execute("DELETE FROM authors WHERE author_id NOT GLOB '~*' OR instr(author_id,'@')>0")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS authors_profile_id_insert BEFORE INSERT ON authors
                    WHEN NEW.author_id NOT GLOB '~*' OR instr(NEW.author_id,'@')>0 OR NEW.identity_source!='openreview_id'
                    BEGIN SELECT RAISE(ABORT,'Author identity must be an OpenReview Profile ID'); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS authors_profile_id_update BEFORE UPDATE ON authors
                    WHEN NEW.author_id NOT GLOB '~*' OR instr(NEW.author_id,'@')>0 OR NEW.identity_source!='openreview_id'
                    BEGIN SELECT RAISE(ABORT,'Author identity must be an OpenReview Profile ID'); END""")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('identity_version',?)", (json.dumps(IDENTITY_VERSION),))


def profile_status_conn(conn):
    if not has_identity_schema(conn):
        return {"identity_version": None, "message": "Identity tables are not initialized"}
    return {
        "identity_version": IDENTITY_VERSION,
        "unresolved_author_occurrences": conn.execute("SELECT COUNT(*) FROM unresolved_paper_authors").fetchone()[0],
        "profile_id_authors": conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0],
    }


def resolve_profile_id(conn, value):
    if not is_profile_id(value):
        raise BurdenError("Author lookup requires an OpenReview Profile ID")
    if has_identity_schema(conn):
        row = conn.execute("SELECT canonical_profile_id FROM profile_aliases WHERE source_profile_id=?", (value,)).fetchone()
        if row:
            return row[0]
        row = conn.execute("SELECT canonical_profile_id FROM profile_aliases WHERE lower(source_profile_id)=lower(?)",
                           (value,)).fetchone()
        if row:
            return row[0]
    row = conn.execute("SELECT author_id FROM authors WHERE author_id=?", (value,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT author_id FROM authors WHERE lower(author_id)=lower(?)", (value,)).fetchone()
    return row[0] if row else value


def load_profile(conn, profile_id):
    if not has_identity_schema(conn):
        return None
    row = conn.execute("SELECT payload FROM author_profiles WHERE canonical_profile_id=?",
                       (resolve_profile_id(conn, profile_id),)).fetchone()
    return json.loads(row[0]) if row else None


def canonicalize_paper(conn, paper):
    """Rewrite author IDs only when a stored Profile alias exists."""
    if not has_identity_schema(conn):
        return paper
    authors, resolutions = [], []
    for author in paper.authors:
        if not is_profile_id(author.author_id):
            authors.append(author)
            continue
        canonical = resolve_profile_id(conn, author.author_id)
        profile = load_profile(conn, canonical)
        authors.append(Author(canonical, profile["preferred_name"] if profile and profile["preferred_name"] else author.display_name))
        if canonical != author.author_id:
            resolutions.append({"source_profile_id": author.author_id, "canonical_profile_id": canonical})
    flags = list(paper.data_quality)
    if len({a.author_id for a in authors}) < len(authors):
        flags.append("verified_profile_alias_duplicate")
    roles = {}
    for source, evidence in paper.author_roles.items():
        canonical = resolve_profile_id(conn, source) if is_profile_id(source) else source
        roles.setdefault(canonical, []).extend(evidence)
    return replace(paper, authors=authors, author_roles=roles, data_quality=flags,
                   source_metadata=paper.source_metadata | {"profile_id_resolutions": resolutions})


def search_profiles(conn, query, *, limit=20):
    if is_profile_id(query):
        canonical = resolve_profile_id(conn, query)
        candidates = [canonical] if conn.execute("SELECT 1 FROM authors WHERE author_id=?", (canonical,)).fetchone() else []
        result = []
        for candidate in candidates:
            if conn.execute("SELECT 1 FROM paper_authors WHERE author_id=? LIMIT 1", (candidate,)).fetchone():
                result.append(public_author_row(candidate))
        return public_output(result[:limit])
    index = load_name_index(conn)
    linked = {row[0] for row in conn.execute("SELECT DISTINCT author_id FROM paper_authors")}
    scored = []
    for author_id in index.lookup(query):
        if author_id not in linked or not is_profile_id(author_id):
            continue
        extra = author_id[1:] if author_id.startswith("~") else author_id
        rank = match_rank(query, (*index.names.get(author_id, ()), extra))
        if rank is not None:
            scored.append((rank, author_id))
    scored.sort()
    result, seen = [], set()
    for _, author_id in scored:
        canonical = resolve_profile_id(conn, author_id)
        if canonical in seen or canonical not in linked:
            continue
        seen.add(canonical)
        result.append(public_author_row(canonical))
        if len(result) >= limit:
            break
    return public_output(result)
