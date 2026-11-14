"""Point-in-time validity of legal provisions (the "Temp" in TempLaw-KAG).

Build time: every amendment event found by the instruction parser is dated with the
effective date of the amending document and stored on the affected node
(``metadata["changes"]``); every node also carries ``valid_from``, the effective date of
its own document.

Query time:

- :class:`Timeline` answers "what is the status of this provision on day D?" — in force,
  amended in part, replaced, repealed, or not yet in force — and names the amending clauses;
- :func:`resolve` re-orders a ranked list so that, on day D, provisions in force come first,
  a replaced or repealed provision is substituted by the clause that replaced or repealed it,
  and outdated or future provisions move to the end;
- :func:`date_filter` is the graph-free baseline: it only knows each document's effective
  date, so it cannot tell that a provision was repealed later;
- :func:`extract_time` recognises and normalises Vietnamese time expressions in a question.

Effective dates are document-level. Exceptions stated inside a document (khoản 2 Điều 16
Luật 146/2025/QH15 postpones one planning provision to 01/01/2027) are not modelled.
"""

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

# Fixed "today" for reproducible evaluation; the app passes the real date.
REFERENCE_DATE = date(2026, 11, 12)

STATES = ("in_force", "amended", "replaced", "repealed", "not_yet")
STALE = {"replaced", "repealed", "not_yet"}


def annotate(nodes, events, effective):
    """Return nodes with ``valid_from`` and dated ``changes`` in their metadata.

    ``events`` are :class:`links.Event` objects; ``effective`` maps a document ID to its
    ISO effective date.
    """
    from dataclasses import replace

    by_unit = {}
    for event in events:
        source_document = event.source.split("::")[0]
        by_unit.setdefault(event.unit, []).append(
            {
                "date": effective[source_document],
                "kind": event.kind,
                "scope": event.scope,
                "by": event.source,
            }
        )
    result = []
    for node in nodes:
        meta = dict(node.metadata)
        if node.document_id in effective:
            meta["valid_from"] = effective[node.document_id]
        changes = sorted(by_unit.get(node.id, []), key=lambda c: (c["date"], c["by"]))
        if changes:
            meta["changes"] = changes
        result.append(replace(node, metadata=meta))
    return result


@dataclass
class Status:
    state: str
    since: str | None = None
    by: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)

    @property
    def stale(self):
        return self.state in STALE


class Timeline:
    """Validity status of graph nodes on a given day."""

    def __init__(self, graph):
        self.nodes = {n.id: n for n in graph.nodes() if not n.placeholder}
        dates = [n.metadata.get("valid_from") for n in self.nodes.values()]
        self.earliest = min((d for d in dates if d), default=None)

    def parent(self, key):
        return key.split("::clause:")[0] if "::clause:" in key else None

    def changes(self, key):
        """Own changes plus whole-article changes, which also end the article's clauses."""
        node = self.nodes.get(key)
        own = list(node.metadata.get("changes", [])) if node else []
        parent = self.nodes.get(self.parent(key)) if self.parent(key) else None
        if parent:
            own += [c for c in parent.metadata.get("changes", []) if c["scope"] == "whole"]
        return sorted(own, key=lambda c: (c["date"], c["by"]))

    def status(self, key, day):
        node = self.nodes.get(key)
        if node is None:
            return Status("in_force")
        day = day.isoformat() if isinstance(day, date) else day
        start = node.metadata.get("valid_from")
        if start and start > day:
            return Status("not_yet", start)
        applied = [c for c in self.changes(key) if c["date"] <= day]
        whole = [c for c in applied if c["scope"] == "whole"]
        if whole:
            last = whole[-1]
            same_day = [c["by"] for c in whole if c["date"] == last["date"]]
            state = "repealed" if last["kind"] == "repeal" else "replaced"
            return Status(state, last["date"], list(dict.fromkeys(same_day)), applied)
        if applied:
            by = [c["by"] for c in applied]
            return Status("amended", applied[-1]["date"], list(dict.fromkeys(by)), applied)
        return Status("in_force", start)

    def in_corpus_period(self, day):
        return self.earliest is None or day.isoformat() >= self.earliest


def resolve(ranked, timeline, day):
    """Re-rank ``ranked`` unit IDs for the law in force on ``day``.

    A replaced or repealed unit is substituted, at its rank, by the clauses that replaced or
    repealed it; an amended unit is followed by its amending clauses; units not in force on
    that day keep their relative order after every unit in force.
    """
    current, stale, seen = [], [], set()

    def add(key):
        if key not in seen:
            seen.add(key)
            current.append(key)

    for key in ranked:
        status = timeline.status(key, day)
        if status.state in {"replaced", "repealed"}:
            for update in status.by:
                if not timeline.status(update, day).stale:
                    add(update)
            stale.append(key)
        elif status.state == "not_yet":
            stale.append(key)
        else:
            add(key)
            for update in status.by:
                if not timeline.status(update, day).stale:
                    add(update)
    return current + [k for k in dict.fromkeys(stale) if k not in seen]


def date_filter(ranked, timeline, day):
    """Graph-free baseline: demote units whose own document is not yet in force."""
    day = day.isoformat()
    future = [
        k
        for k in ranked
        if (timeline.nodes.get(k) and timeline.nodes[k].metadata.get("valid_from", "") > day)
    ]
    return [k for k in ranked if k not in future] + future


# ---------------------------------------------------------------------------------------
# Time expressions in questions


@dataclass
class TimeExpression:
    day: date
    text: str
    rule: str


NOW = re.compile(
    r"\b(hiện nay|hiện hành|hiện tại|bây giờ|mới nhất|đang có hiệu lực|còn hiệu lực|"
    r"hiện đang|ở thời điểm này|lúc này)\b"
)
FULL_DATE = re.compile(
    r"(?:ngày\s+)?(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4})"
    r"|ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})"
)
MONTH = re.compile(r"tháng\s+(\d{1,2})\s*(?:/|-|năm)\s*(\d{4})")
YEAR = re.compile(r"(?P<part>đầu|giữa|cuối)?\s*năm\s+(?P<year>\d{4})")
BEFORE = re.compile(r"\b(trước khi|trước ngày|trước thời điểm)\b")
AFTER = re.compile(r"\b(sau khi|kể từ khi|từ khi|kể từ ngày|từ ngày|sau ngày)\b")
AMENDED = re.compile(r"(?:được\s+)?sửa đổi(?:,?\s*bổ sung)?(?:\s+năm\s+(\d{4}))?")
ORIGINAL = re.compile(
    r"\b(ban đầu|bản gốc|chưa (?:được )?sửa đổi|khi\b[^,.;?]{0,60}?\bmới (?:ban hành|có hiệu lực))"
)
# "Luật Tài nguyên nước năm 2023", "Nghị định ... năm 2024": a year that names a document.
NAMED_YEAR = re.compile(r"(?:luật|nghị định|bộ luật|thông tư|quyết định)[^,.;?]{0,40}?năm\s+\d{4}$")


def fold(text):
    return unicodedata.normalize("NFC", text).casefold()


def cap(day, today):
    return min(day, today)


def safe_date(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


class TimeExtractor:
    """Rule-based recognition and normalisation of Vietnamese time expressions.

    Normalisation conventions: a year or month means its last day (the law as it stood at
    the end of the period, capped at today); "đầu năm" is 1 January and "giữa năm" 30 June;
    "trước khi X có hiệu lực" is the day before X's effective date; "sau khi X có hiệu lực" or
    "sau khi ... được sửa đổi" is the effective date of X (or of the latest amending document).
    """

    def __init__(self, documents, today=REFERENCE_DATE):
        # documents: {id: {"effective_date": iso, "names": [folded names], "amended_by": [ids]}}
        self.documents = documents
        self.today = today

    def amendment_date(self, key=None):
        """Entry into force of the amendments of ``key`` (of any document if None)."""
        keys = (
            self.documents[key].get("amended_by", [])
            if key
            else [k for d in self.documents.values() for k in d.get("amended_by", [])]
        )
        dates = [self.effective(k) for k in keys if k in self.documents]
        return max(dates) if dates else None

    def named_document(self, text):
        """Document named in text: by number ("146/2025") or by a registered name."""
        for key, info in self.documents.items():
            number = key.split("/")[0] + "/" + key.split("/")[1]
            if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text):
                return key
        for key, info in self.documents.items():
            if any(name in text for name in info.get("names", [])):
                return key
        return None

    def effective(self, key):
        return date.fromisoformat(self.documents[key]["effective_date"])

    def extract(self, question):
        text = fold(question)
        # 1. Explicit dates, possibly anchored by "trước ngày" / "từ ngày".
        for match in FULL_DATE.finditer(text):
            parts = match.groups()
            d, m, y = parts[:3] if parts[0] else parts[3:]
            day = safe_date(y, m, d)
            if day:
                if re.search(r"\btrước\s*(?:ngày\s*)?$", text[: match.start()]):
                    return TimeExpression(day - timedelta(days=1), match.group(), "before_date")
                return TimeExpression(day, match.group(), "date")
        # 2. Relative to a document's entry into force or to its amendment.
        for anchor, rule in ((BEFORE, "before"), (AFTER, "after")):
            match = anchor.search(text)
            if not match:
                continue
            tail = text[match.end() : match.end() + 90].split("?")[0]
            key = self.named_document(tail)
            if AMENDED.search(tail):
                # "trước khi Luật Tài nguyên nước được sửa đổi": the amendment's start date.
                day = self.amendment_date(key) if key else self.amendment_date()
            else:
                day = None
            if day is None and key:
                day = self.effective(key)
            if day is None:
                continue
            if rule == "before":
                day -= timedelta(days=1)
            phrase = re.split(r"[,;]", match.group() + tail)[0].strip()
            return TimeExpression(day, phrase, rule)
        # 3. "Theo quy định sửa đổi năm 2026" → the law after that amendment, i.e. today.
        for match in AMENDED.finditer(text):
            if match.group(1):
                return TimeExpression(self.today, match.group(), "after_amendment")
        # 4. "ban đầu", "khi mới có hiệu lực" → the named document's effective date.
        match = ORIGINAL.search(text)
        if match:
            key = self.named_document(text)
            if key:
                return TimeExpression(self.effective(key), match.group(), "original")
        # 5. Month or year periods, unless the year names a document.
        match = MONTH.search(text)
        if match:
            month, year = int(match[1]), int(match[2])
            if 1 <= month <= 12:
                last = calendar.monthrange(year, month)[1]
                return TimeExpression(
                    cap(date(year, month, last), self.today), match.group(), "month"
                )
        for match in YEAR.finditer(text):
            if NAMED_YEAR.search(text[: match.end()]):
                continue
            year = int(match["year"])
            part = match["part"]
            day = {
                "đầu": date(year, 1, 1),
                "giữa": date(year, 6, 30),
            }.get(part, date(year, 12, 31))
            return TimeExpression(cap(day, self.today), match.group().strip(), "year")
        # 6. "hiện nay", "hiện hành" → today.
        match = NOW.search(text)
        if match:
            return TimeExpression(self.today, match.group(), "now")
        return None


def extractor_from_graph(document_nodes, unit_nodes, today=REFERENCE_DATE):
    """Build a :class:`TimeExtractor` from graph nodes: document names and effective dates,
    and which documents amend which (read from the dated changes on provisions)."""
    amended_by = {}
    for node in unit_nodes:
        for change in node.metadata.get("changes", []):
            amended_by.setdefault(node.document_id, set()).add(change["by"].split("::")[0])
    info = {}
    for node in document_nodes:
        meta = node.metadata
        names = [fold(a) for a in meta.get("aliases", [])] + [fold(meta.get("short_name", ""))]
        info[node.id] = {
            "effective_date": meta.get("effective_date"),
            "names": [n for n in names if n],
            "amended_by": sorted(amended_by.get(node.id, ())),
        }
    return TimeExtractor({k: v for k, v in info.items() if v["effective_date"]}, today)
