"""Order-independent, spacing-tolerant author name matching. Search only; never merges IDs."""
import bisect
import re
from collections import defaultdict
from itertools import permutations

_SPLIT = re.compile(r"[^a-z0-9]+")
_TRAILING_DIGITS = re.compile(r"\d+$")
_MAX_PERM_TOKENS = 4


def normalize_name(value):
    return " ".join(_SPLIT.split((value or "").casefold())).strip()


def name_tokens(value):
    tokens = []
    for part in normalize_name(value).split():
        stripped = _TRAILING_DIGITS.sub("", part)
        if stripped:
            tokens.append(stripped)
    return tokens


def compact_forms(tokens):
    tokens = [token for token in tokens if token]
    if not tokens:
        return set()
    forms = {"".join(tokens), "".join(reversed(tokens))}
    if 2 < len(tokens) <= _MAX_PERM_TOKENS:
        forms.update("".join(perm) for perm in permutations(tokens))
    return forms


def _compact_hit(qcompact, tokens):
    joined = "".join(tokens)
    reversed_join = "".join(reversed(tokens))
    if qcompact == joined or qcompact == reversed_join:
        return 0
    if joined.startswith(qcompact) or reversed_join.startswith(qcompact):
        return 1
    if 2 < len(tokens) <= _MAX_PERM_TOKENS:
        forms = compact_forms(tokens)
        if qcompact in forms:
            return 0
        if any(form.startswith(qcompact) for form in forms):
            return 1
    return None


def _tokens_cover(query_tokens, name_tokens):
    unused = list(name_tokens)
    for query in sorted(query_tokens, key=len, reverse=True):
        for index, name in enumerate(unused):
            if name.startswith(query):
                unused.pop(index)
                break
        else:
            return False
    return True


def match_rank(query, names):
    """Lower is better. None means no match."""
    qnorm = normalize_name(query)
    qtokens = name_tokens(query)
    qcompact = "".join(qtokens)
    if not qcompact:
        return None
    best = None
    for raw in names:
        ntokens = name_tokens(raw)
        if not ntokens:
            continue
        nnorm = normalize_name(raw)
        compact = _compact_hit(qcompact, ntokens)
        if qnorm == nnorm or compact == 0:
            return 0
        if nnorm.startswith(qnorm) or compact == 1:
            score = 1
        elif _tokens_cover(qtokens, ntokens):
            score = 2 if all(token in ntokens for token in qtokens) else 3
        else:
            continue
        best = score if best is None else min(best, score)
    return best


class NameIndex:
    def __init__(self, names_by_author):
        self.names = names_by_author
        token_authors = defaultdict(set)
        for author_id, names in names_by_author.items():
            extra = author_id[1:] if author_id.startswith("~") else author_id
            for value in (*names, extra):
                for token in name_tokens(value):
                    token_authors[token].add(author_id)
        self.tokens = sorted(token_authors)
        self.token_authors = token_authors

    def prefix_authors(self, prefix):
        if not prefix:
            return set()
        authors = set()
        index = bisect.bisect_left(self.tokens, prefix)
        while index < len(self.tokens) and self.tokens[index].startswith(prefix):
            authors.update(self.token_authors[self.tokens[index]])
            index += 1
        return authors

    def lookup(self, query):
        qtokens = name_tokens(query)
        qcompact = "".join(qtokens)
        if not qcompact:
            return set()
        if len(qtokens) >= 2:
            authors = self.prefix_authors(qtokens[0])
            for token in qtokens[1:]:
                authors &= self.prefix_authors(token)
            authors |= self._split_authors(qcompact)
            return authors
        return self.prefix_authors(qcompact) | self._split_authors(qcompact)

    def _split_authors(self, qcompact):
        authors = set()
        length = len(qcompact)
        if length < 4:
            return authors
        for split in range(2, length - 1):
            authors |= self.prefix_authors(qcompact[:split]) & self.prefix_authors(qcompact[split:])
        if length >= 6:
            for first in range(2, length - 3):
                for second in range(first + 2, length - 1):
                    authors |= (self.prefix_authors(qcompact[:first])
                                & self.prefix_authors(qcompact[first:second])
                                & self.prefix_authors(qcompact[second:]))
        return authors


_INDEX = None
_INDEX_KEY = None


def load_name_index(conn):
    global _INDEX, _INDEX_KEY
    row = conn.execute("PRAGMA database_list").fetchone()
    count = conn.execute("SELECT COUNT(*) FROM author_names").fetchone()[0]
    key = (row[2] if row is not None else "", count)
    if _INDEX is not None and _INDEX_KEY == key:
        return _INDEX
    names_by_author = defaultdict(list)
    for author_id, folded in conn.execute("SELECT author_id, folded FROM author_names"):
        if folded:
            names_by_author[author_id].append(folded)
    for author_id, display in conn.execute("SELECT author_id, display_name FROM authors"):
        if display:
            names_by_author[author_id].append(display)
    _INDEX = NameIndex(names_by_author)
    _INDEX_KEY = key
    return _INDEX
