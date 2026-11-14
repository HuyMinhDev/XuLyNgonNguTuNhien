"""Parse amendment instructions of amending documents into typed operations.

An amending clause mixes instruction lines ("7. Sửa đổi, bổ sung Điều 53 như sau:",
"a) Bãi bỏ khoản 4 Điều 9; ...; các điều 54, 55, 56 và 57;") with replacement text. Only
instruction lines are parsed; quoted phrases inside them ("Thay thế cụm từ “A” bằng cụm từ
“B” tại ...") are removed first. Every other paragraph is replacement text, even when the
Gazette closes the quotation early (“Điều 33. ...” followed by an unquoted body).

Each operation has a kind and a list of targets with a scope:

- ``repeal``: "Bãi bỏ khoản 4 Điều 9" — the provision stops applying;
- ``amend``: "Sửa đổi, bổ sung Điều 53 như sau" — the provision text is replaced;
- ``supplement``: "Bổ sung Điều 8a vào sau Điều 8" — new text anchored to an existing unit;
- ``wording``: "Thay thế cụm từ ...", "Bỏ cụm từ ..." — words change, the provision stays.

A target is (article, clause, partial): ``partial`` is true when the operation touches only
part of that unit (some points, a title, or wording).
"""

import re
from dataclasses import dataclass, field

from .structure import quote_depth

ITEM = r"(?:\d+\.|[a-zđ]\d?\))"
VERB = re.compile(
    rf"^\s*(?:{ITEM}\s+)?(?P<verb>Sửa đổi,? bổ sung|Sửa đổi|Bổ sung|Bãi bỏ|Bỏ|Thay thế)\b"
    r"(?P<rest>.*)$",
    re.S,
)
SUB_ITEM = re.compile(r"^\s*[a-zđ]\d?\)\s+(?:Sửa đổi|Bổ sung|Bãi bỏ|Bỏ|Thay thế)\b")
WORDS = re.compile(r"^\s*(?:các\s+|một số\s+)?(?:cụm\s+)?từ\b")
ANNEX = re.compile(r"^\s*(?:các\s+)?(?:Mẫu|Phụ lục|mẫu|phụ lục)")
# "Sửa đổi, bổ sung một số khoản của Điều 17 như sau:" or "Bãi bỏ ... sau đây:" introduces
# sub-items and changes nothing itself.
HEADER = re.compile(r"^\s*một số\b|sau đây\s*:\s*$")

POINT_ID = r"[a-zđ]\d?"
NUMBER_ID = r"\d+[a-z]?"


def unit_list(word, ident):
    """ "các khoản 3, 4 và 5", "khoản 1 và khoản 3", "điểm a, điểm b"."""
    unit = rf"[{word[0].upper()}{word[0]}]{word[1:]}"
    return re.compile(
        rf"(?P<title>tên\s+)?(?:các\s+)?{unit}\s+(?P<ids>{ident}"
        rf"(?:\s*(?:,|và|hoặc)\s*(?:{unit}\s+)?{ident})*)(?![\wđ])"
    )


PATTERNS = (
    ("point", unit_list("điểm", POINT_ID)),
    ("clause", unit_list("khoản", NUMBER_ID)),
    ("article", unit_list("điều", NUMBER_ID)),
)
SEPARATOR = re.compile(r";")


@dataclass(frozen=True)
class Target:
    article: str
    clause: str | None = None
    partial: bool = False


@dataclass
class Operation:
    kind: str  # repeal | amend | supplement | wording
    line: str
    targets: list[Target] = field(default_factory=list)


def ids(match):
    bare = re.sub(r"[Đđ]iểm|[Kk]hoản|[Đđ]iều|\bvà\b|\bhoặc\b", " ", match["ids"])
    return re.findall(rf"{NUMBER_ID}|{POINT_ID}", bare)


def tokens(segment):
    """Unit mentions of a segment in reading order: (kind, ids, title)."""
    found = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(segment):
            found.append((match.start(), match.end(), kind, ids(match), bool(match["title"])))
    found.sort()
    result, end = [], -1
    for start, stop, kind, values, title in found:
        if start >= end:  # a list never overlaps another list
            result.append((kind, values, title))
            end = stop
    return result


def parse_targets(text, context=(None, None)):
    """Targets named in instruction text. Vietnamese names the smaller unit first ("điểm c
    khoản 1 Điều 19"), so points wait for their clause and clauses for their article; a
    segment that ends without an article uses the context of its header line."""
    context_article, context_clause = context
    targets = []
    for segment in SEPARATOR.split(text):
        points, clauses = [], []  # clauses: [(number, partial)]
        for kind, values, title in tokens(segment):
            if kind == "point":
                points.extend(values)
            elif kind == "clause":
                clauses.extend((v, i == 0 and bool(points)) for i, v in enumerate(values))
                points = []
            else:
                for i, article in enumerate(values):
                    owned = clauses if i == 0 else []
                    if owned:
                        targets.extend(Target(article, c, p) for c, p in owned)
                    else:
                        targets.append(Target(article, None, title or (i == 0 and bool(points))))
                points, clauses = [], []
        if (clauses or points) and context_article:
            if clauses:
                targets.extend(Target(context_article, c, p) for c, p in clauses)
            else:
                targets.append(Target(context_article, context_clause, True))
    return list(dict.fromkeys(targets))


def outside_quotes(text):
    """Instruction text with quoted phrases blanked out."""
    out, depth = [], 0
    for char in text:
        before = depth
        depth = quote_depth(char, depth)
        out.append(char if not before and not depth else " ")
    return "".join(out)


def instruction_lines(paragraphs):
    """(index, unquoted text) of the instruction lines of an amending clause."""
    depth = 0
    for i, paragraph in enumerate(paragraphs):
        if i == 0 or (depth == 0 and SUB_ITEM.match(paragraph)):
            yield i, outside_quotes(paragraph)
        depth = quote_depth(paragraph, depth)


def classify(verb, rest):
    if verb in {"Bãi bỏ", "Bỏ"}:
        return "wording" if WORDS.match(rest) else "repeal"
    if verb == "Thay thế":
        if ANNEX.match(rest):
            return None
        return "wording"
    if verb == "Bổ sung":
        return "supplement"
    return "amend"


def parse_clause(paragraphs):
    """Operations of one amending clause, and the paragraphs that are replacement text."""
    operations, instruction_index = [], set()
    context = (None, None)
    for index, line in instruction_lines(paragraphs):
        match = VERB.match(line)
        if not match:
            continue
        instruction_index.add(index)
        kind = classify(match["verb"], match["rest"])
        head = match["rest"].split("như sau")[0]
        if HEADER.search(match["rest"]):
            # "Sửa đổi, bổ sung một số khoản của Điều 17 như sau:" names the article that
            # following sub-items ("a) Sửa đổi, bổ sung khoản 1 như sau:") refer to.
            named = parse_targets(head)
            if named:
                context = (named[0].article, named[0].clause)
            continue
        if kind is None:
            continue
        targets = parse_targets(head if kind in {"amend", "supplement"} else match["rest"], context)
        if kind in {"wording", "supplement"}:
            targets = [Target(t.article, t.clause, True) for t in targets]
        if targets:
            operations.append(Operation(kind, line.strip(), targets))
            articles = {t.article for t in targets}
            if index == 0 and len(articles) == 1:
                # "Sửa đổi tên Điều 38 và bổ sung khoản 7 Điều 38 như sau:" followed by
                # "b) Bổ sung khoản 7 vào sau khoản 6": sub-items inherit the article.
                context = (articles.pop(), None)
    replacement = [p for i, p in enumerate(paragraphs) if i not in instruction_index]
    return operations, replacement
