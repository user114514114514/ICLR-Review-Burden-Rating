"""JSON helpers used by import, export, and the local query API."""
import hashlib
import json
from pathlib import Path

from .errors import BurdenError


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BurdenError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise BurdenError(f"Nonfinite JSON constant: {value}")


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (OSError, json.JSONDecodeError) as exc:
        raise BurdenError(f"Cannot read JSON {path}: {exc}") from exc


def canonical_json(value) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BurdenError(f"Cannot encode JSON: {exc}") from exc


def content_hash(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def write_json_new(path, value):
    """Write a new file; refuse to replace a different existing payload."""
    target = Path(path)
    payload = canonical_json(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        if target.read_bytes() != payload:
            raise BurdenError(f"Refusing to overwrite existing file: {target}") from exc
    return target
