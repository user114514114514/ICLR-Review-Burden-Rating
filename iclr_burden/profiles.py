"""Profile ID checks and redaction of email fields in public output."""
import re

EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}")


def is_profile_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"~[^\s/@]+", value))


def public_output(value):
    """Strip email keys and addresses from values returned to callers."""
    if isinstance(value, dict):
        return {k: public_output(v) for k, v in value.items()
                if not isinstance(k, str) or ("email" not in k.casefold() and not EMAIL_RE.search(k))}
    if isinstance(value, (list, tuple)):
        return [public_output(v) for v in value]
    if isinstance(value, str):
        return EMAIL_RE.sub("[redacted]", value)
    return value
