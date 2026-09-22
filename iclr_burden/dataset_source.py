"""Pinned release download, safe extraction and streaming JSONL schema audit."""
import hashlib
import json
import shutil
import tarfile
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

from .errors import BurdenError
from .normalization import parse_numeric, unwrap
from .storage import read_json, write_json_new

REPOSITORY = "https://github.com/qhjqhj00/iclr-openreview-reviews"
RELEASE = "v1.0"
ARCHIVE_URL = REPOSITORY + "/releases/download/v1.0/iclr.tar.bz2"
ARCHIVE_SHA256 = "350071daf25ebf1aef854ee1d5dc3a81834e1c468917c82a4f78af5548de34fc"
SOURCE_DATE = "2026-05-08T00:00:00Z"
YEARS = tuple(range(2020, 2027))


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_release(destination, *, progress=None):
    """Pinned asset only; never contact OpenReview or execute upstream code."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if file_sha256(path) != ARCHIVE_SHA256:
            raise BurdenError("Existing dataset archive fails pinned SHA-256 verification")
        return path
    partial = path.with_suffix(path.suffix + ".part")
    request = Request(ARCHIVE_URL, headers={"User-Agent": "iclr-review-burden/1.0"})
    # Retry by rerunning; a failed/partial download is never treated as a dataset.
    with urlopen(request, timeout=60) as response, partial.open("wb") as output:
        count = 0
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            output.write(chunk)
            count += len(chunk)
            if progress and count % (32 * 1024 * 1024) == 0:
                progress(f"Downloaded {count // (1024 * 1024)} MiB")
    if file_sha256(partial) != ARCHIVE_SHA256:
        raise BurdenError("Downloaded dataset fails pinned SHA-256 verification")
    partial.replace(path)
    return path


def extract_release(archive, destination, *, expected_sha256=ARCHIVE_SHA256, progress=None):
    """Extract JSONL regular files only; reject traversal, links and duplicates."""
    digest = file_sha256(archive)
    if expected_sha256 and digest != expected_sha256:
        raise BurdenError("Dataset archive SHA-256 mismatch")
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "extraction.json"
    if marker.exists():
        manifest = read_json(marker)
        if manifest["archive_sha256"] != digest:
            raise BurdenError("Extraction destination already contains a different release")
        if all((root / entry["path"]).is_file() and file_sha256(root / entry["path"]) == entry["sha256"]
               for entry in manifest["files"]):
            return manifest
        raise BurdenError("Existing extraction is incomplete or modified; use a new directory")
    files, seen = [], set()
    with tarfile.open(archive, "r|bz2") as bundle:
        for member in bundle:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise BurdenError(f"Unsafe archive member: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise BurdenError(f"Unsupported archive member: {member.name}")
            if name.name not in {"papers.jsonl", "reviews.jsonl"}:
                continue
            year_dir = name.parent.name
            if year_dir not in {f"iclr_{year}" for year in YEARS}:
                raise BurdenError(f"Unexpected dataset directory: {member.name}")
            relative = Path(year_dir) / name.name
            if str(relative) in seen:
                raise BurdenError(f"Duplicate dataset archive member: {member.name}")
            seen.add(str(relative))
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.parent.is_symlink():
                raise BurdenError("Extraction target cannot be a symbolic link")
            stream = bundle.extractfile(member)
            if stream is None:
                raise BurdenError(f"Unreadable archive member: {member.name}")
            with stream, target.open("xb") as output:
                shutil.copyfileobj(stream, output, 1024 * 1024)
            files.append({"path": str(relative), "size": target.stat().st_size, "sha256": file_sha256(target)})
            if progress:
                progress(f"Extracted {relative}")
    if seen != {f"iclr_{y}/{name}.jsonl" for y in YEARS for name in ("papers", "reviews")}:
        raise BurdenError("Archive does not contain all 14 expected year files")
    manifest = {"repository": REPOSITORY, "release": RELEASE, "source_date": SOURCE_DATE,
                "archive_sha256": digest, "files": sorted(files, key=lambda item: item["path"])}
    write_json_new(marker, manifest)
    return manifest


def iter_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BurdenError(f"Invalid JSON at {path}:{line_no}") from exc
            if not isinstance(record, dict) or not record.get("id"):
                raise BurdenError(f"Expected an OpenReview note with ID at {path}:{line_no}")
            yield line_no, record


def content_fields(note):
    return {k: unwrap(v) for k, v in (note.get("content") or {}).items()}


def note_kind(note):
    invitations = note.get("invitations") or [note.get("invitation", "")]
    names = {i.rsplit("/", 1)[-1] for i in invitations}
    known = names & {"Official_Review", "Meta_Review", "Decision", "Official_Comment"}
    helper = note.get("_kind")
    if helper and helper not in known:
        raise BurdenError(f"Reply kind disagrees with invitation for {note['id']}")
    return helper or next(iter(sorted(known)), "unknown")


def inspect_dataset(root, years=YEARS, *, progress=None):
    """Examine every record's schema; retain distributions, not review text."""
    result = {"repository": REPOSITORY, "years": {}}
    for year in years:
        folder = Path(root) / f"iclr_{year}"
        stats = {"api_version": 1 if year <= 2023 else 2}
        paper_fields, review_fields, kinds, rating_values, confidence_values = (Counter() for _ in range(5))
        paper_ids, author_shapes, decisions = set(), Counter(), Counter()
        rating_texts, confidence_texts = Counter(), Counter()
        counts = Counter()
        for _, note in iter_jsonl(folder / "papers.jsonl"):
            counts["papers"] += 1
            paper_ids.add(note.get("forum") or note["id"])
            content = content_fields(note)
            paper_fields.update(content.keys())
            authors, ids = content.get("authors") or [], content.get("authorids") or []
            counts["papers_with_missing_authors"] += not bool(authors)
            author_shapes["length_mismatch" if len(authors) != len(ids) else "aligned"] += 1
            counts["profile_ids"] += sum(isinstance(a, str) and a.startswith("~") for a in ids)
            counts["non_profile_ids"] += sum(not (isinstance(a, str) and a.startswith("~")) for a in ids)
            for key in ("venue", "venueid", "decision"):
                if content.get(key):
                    decisions[f"paper.{key}: {content[key]}"] += 1
        for _, note in iter_jsonl(folder / "reviews.jsonl"):
            counts["replies"] += 1
            kind = note_kind(note)
            kinds[kind] += 1
            content = content_fields(note)
            if (note.get("_paper_forum") or note.get("forum")) not in paper_ids:
                counts["orphan_replies"] += 1
            if kind == "Official_Review":
                review_fields.update(content.keys())
                for key in ("rating", "recommendation", "confidence", "experience_assessment"):
                    if key in content:
                        counts[f"field:{key}"] += 1
                rating = content.get("rating", content.get("recommendation"))
                confidence = content.get("confidence")
                rating_values[str(parse_numeric(rating))] += 1
                confidence_values[str(parse_numeric(confidence))] += 1
                rating_texts[str(rating)] += 1
                confidence_texts[str(confidence)] += 1
                counts["reviews_with_timestamp"] += any(note.get(k) for k in ("tmdate", "mdate", "tcdate", "cdate"))
                counts["reviews_with_embedded_revisions"] += bool((note.get("details") or {}).get("revisions"))
            elif kind == "Decision":
                decisions[f"decision: {content.get('decision')}"] += 1
        stats.update(counts=counts, paper_fields=paper_fields, review_fields=review_fields,
                     reply_kinds=kinds, author_shapes=author_shapes, decisions=decisions,
                     ratings=rating_values, confidences=confidence_values,
                     rating_labels=rating_texts, confidence_labels=confidence_texts)
        result["years"][str(year)] = stats
        if progress:
            progress(f"Inspected {year}: {counts['papers']} papers, {counts['replies']} replies")
    return result
