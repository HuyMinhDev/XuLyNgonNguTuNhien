"""Rule-based legal links between provisions.

References (REFERS_TO) are read from provision text: "khoản 2 Điều 9 của Luật này",
"Điều 43 của Luật Tài nguyên nước", "khoản 1 Điều 8 Nghị định số 54/2024/NĐ-CP".
Amending articles ("Sửa đổi, bổ sung một số điều của ...") yield validity edges from each
affected provision to the amending clause, following ViHERMES direction (affected → update):
REPEALS when a whole provision is repealed, SUPPLEMENTS for new text anchored to it, and
AMENDS otherwise. Each edge is also recorded as a dated event with its kind and scope
(whole or partial), which the temporal layer uses to decide what is in force on a date.
"""

import re
from dataclasses import dataclass

from ..ingestion import DOCUMENT_CODE
from ..models import Edge
from .instructions import parse_clause

CLAUSE_LIST = r"(?P<clauses>\d+(?:\s*(?:,|và|hoặc)\s*(?:khoản\s+)?\d+)*)"
# Provision references capitalize "Điều"; lowercase "các điều 81, 82" lists are not parsed.
MENTION = re.compile(
    rf"(?:(?:các\s+)?[Kk]hoản\s+{CLAUSE_LIST}\s+(?:của\s+)?)?Điều\s+(?P<article>\d+[a-z]?)\b"
)
THIS_ARTICLE = re.compile(rf"(?:các\s+)?[Kk]hoản\s+{CLAUSE_LIST}\s+(?:của\s+)?Điều\s+này")
# Text allowed between mentions that share one document qualifier.
CHAIN_GAP = re.compile(
    r"^\s*(?:,|;|và|hoặc)?\s*(?:(?:các\s+)?điểm\s+[a-zđ](?:\s*(?:,|và|hoặc)\s*(?:điểm\s+)?[a-zđ])*\s+)?$"
)
DOCUMENT_KIND = r"(?:Bộ luật|Luật|Nghị định|Thông tư|Nghị quyết|Quyết định|Pháp lệnh)"
QUALIFIER = re.compile(
    r"\s*(?:của\s+|tại\s+)?(?:"
    rf"(?P<this>{DOCUMENT_KIND}\s+này)"
    rf"|{DOCUMENT_KIND}(?:(?!{DOCUMENT_KIND})[^;:.“”\n]){{0,100}}?\s+số\s+(?P<code>{DOCUMENT_CODE})"
    rf"|(?P<named>{DOCUMENT_KIND}\s[^;:.“”\n]{{0,80}})"
    r")"
)
# "Luật Tài nguyên nước năm 2012" or "... ngày 21 tháng 6 năm 2012" names an earlier law.
DATED = re.compile(r"\s+(?:ngày\s+\d{1,2}\s+tháng\s+\d{1,2}\s+)?năm\s+(\d{4})")
AMENDING_TITLE = re.compile(r"^Sửa đổi,? bổ sung một số điều của")
VERB = re.compile(r"^\s*(?:\d+\.\s+)?(Sửa đổi|Bổ sung|Bãi bỏ|Thay thế|Sửa)")
RELATION = {"repeal": "REPEALS", "supplement": "SUPPLEMENTS"}


@dataclass(frozen=True)
class Event:
    """A change to ``unit`` made by the amending clause ``source``."""

    unit: str
    source: str
    kind: str  # repeal | amend | supplement | wording
    scope: str  # whole | partial

    @property
    def relation(self):
        if self.kind == "supplement":
            return "SUPPLEMENTS"
        return "REPEALS" if self.kind == "repeal" and self.scope == "whole" else "AMENDS"


def split_quotes(text):
    """Separate instruction text from quoted replacement text."""
    outside, inside, depth = [], [], 0
    for char in text:
        if char == "“" or (char == '"' and not depth):
            depth += 1
            inside.append(" ")
        elif char == "”" or (char == '"' and depth):
            depth = max(0, depth - 1)
            inside.append(" ")
        elif depth:
            inside.append(char)
        else:
            outside.append(char)
    return "".join(outside), "".join(inside)


def clause_numbers(raw):
    return re.findall(r"\d+", raw or "")


class Linker:
    def __init__(self, documents, unit_ids):
        self.documents = {d.id: d for d in documents}
        self.aliases = sorted(
            ((alias, d.id) for d in documents for alias in d.aliases), key=lambda x: -len(x[0])
        )
        self.units = set(unit_ids)
        self.events = []

    def named_document(self, text):
        """Resolve a document named by number or registered alias at the start of text."""
        # A number belongs to this name only if no other document is named before it.
        following = re.search(DOCUMENT_KIND, text[1:])
        own = text[: following.start() + 1] if following else text
        code = re.search(DOCUMENT_CODE, own)
        for alias, key in self.aliases:
            if text.startswith(alias):
                rest = text[len(alias) :]
                if code and code.start() <= len(alias) + 4:
                    return code.group().upper()
                dated = DATED.match(rest)
                if dated and dated[1] != self.documents[key].issued_date[:4]:
                    return None
                return key
        return code.group().upper() if code else None

    def qualify(self, text, position, default):
        match = QUALIFIER.match(text, position)
        if not match:
            return default
        if match["this"]:
            return default
        if match["code"]:
            return match["code"].upper()
        named = match["named"]
        return self.named_document(named) if named else default

    def mentions(self, text, default):
        """(document, article, [clauses]) for every article mention, qualifier applied per chain."""
        found = list(MENTION.finditer(text))
        result, chain = [], []
        for i, match in enumerate(found):
            chain.append(match)
            following = found[i + 1] if i + 1 < len(found) else None
            if following and CHAIN_GAP.match(text[match.end() : following.start()]):
                continue
            document = self.qualify(text, match.end(), default)
            if document:
                for item in chain:
                    result.append(
                        (document, item["article"].lower(), clause_numbers(item["clauses"]))
                    )
            chain = []
        return result

    def resolve(self, document, article, clauses):
        """Most specific known target; unknown provisions remain explicit placeholders."""
        base = f"{document}::article:{article}"
        known = [f"{base}::clause:{c}" for c in clauses if f"{base}::clause:{c}" in self.units]
        return known or [base]

    def references(self, source, text, default, article=None):
        targets = []
        for document, number, clauses in self.mentions(text, default):
            targets.extend(self.resolve(document, number, clauses))
        if article:
            for match in THIS_ARTICLE.finditer(text):
                targets.extend(self.resolve(default, article, clause_numbers(match["clauses"])))
        # A provision citing itself or its own article adds no information.
        return [
            Edge(source, t, "REFERS_TO")
            for t in dict.fromkeys(targets)
            if t != source and not source.startswith(t + "::")
        ]

    def amendments(self, source, operations, target_document):
        """Dated events for the provisions named by parsed amendment operations."""
        events = []
        for operation in operations:
            for target in operation.targets:
                base = f"{target_document}::article:{target.article}"
                if base not in self.units and operation.kind == "supplement":
                    # A supplemented article (e.g. 8a) is new; anchor it to its base article.
                    base = f"{target_document}::article:{target.article.rstrip('abcdefghijklmnopqrstuvwxyz')}"
                if base not in self.units or base == source:
                    continue
                whole = operation.kind in {"repeal", "amend"} and not target.partial
                clause = f"{base}::clause:{target.clause}" if target.clause else None
                if clause in self.units:
                    events.append(
                        Event(clause, source, operation.kind, "whole" if whole else "partial")
                    )
                    events.append(Event(base, source, operation.kind, "partial"))
                elif clause is None:
                    events.append(
                        Event(base, source, operation.kind, "whole" if whole else "partial")
                    )
                else:
                    # A new clause ("bổ sung khoản 7") changes its article only in part.
                    events.append(Event(base, source, operation.kind, "partial"))

        # One amending clause may touch a unit twice; the widest change wins.
        def rank(event):
            return (event.scope == "whole", event.kind != "wording")

        strongest = {}
        for event in events:
            key = (event.unit, event.source)
            if key not in strongest or rank(event) > rank(strongest[key]):
                strongest[key] = event
        return list(strongest.values())


def document_links(document, preamble, linker):
    """Document-level REFERS_TO edges from the legal bases ("Căn cứ ...") in the preamble."""
    edges = []
    for line in preamble:
        if not line.startswith("Căn cứ"):
            continue
        for match in re.finditer(DOCUMENT_KIND + r"\s", line):
            target = linker.named_document(line[match.start() :].split(";")[0])
            if target and target != document.id and target in linker.documents:
                edges.append(Edge(document.id, target, "REFERS_TO"))
    return list(dict.fromkeys(edges))


def amended_document(title, linker):
    """Target of an amending article such as "Sửa đổi, bổ sung một số điều của Luật ..."."""
    if not AMENDING_TITLE.match(title):
        return None
    match = re.search(DOCUMENT_KIND + r"\s", title)
    return linker.named_document(title[match.start() :]) if match else None


def instruction_target(text, current, linker):
    """Another document amended or repealed by a single clause ("3. Bãi bỏ ... của Nghị định số X:")."""
    if not VERB.match(text):
        return None
    head = text.split(":")[0]
    match = re.search(DOCUMENT_KIND + r"\s", head)
    target = linker.named_document(head[match.start() :]) if match else None
    return target if target != current else None


def amendment_links(source, paragraphs, target, linker):
    operations, replacement = parse_clause(paragraphs)
    events = linker.amendments(source, operations, target)
    linker.events.extend(events)
    instruction, quoted = split_quotes("\n".join(p for p in paragraphs if p not in replacement))
    # Out-of-corpus targets cannot carry validity edges; keep them as explicit references.
    found = [Edge(e.unit, e.source, e.relation) for e in events]
    return [
        *(found or linker.references(source, instruction, target)),
        *(
            edge
            for edge in linker.references(source, "\n".join([quoted, *replacement]), target)
            # The heading of replacement text ("“Điều 50. ...") names the amended article.
            if edge.target not in {e.unit for e in events}
        ),
    ]


def article_links(document, articles, linker):
    """REFERS_TO, AMENDS, SUPPLEMENTS and REPEALS edges for the provisions of one document."""
    edges = []
    for article in articles:
        aid = f"{document.id}::article:{article.number}"
        target = amended_document(article.title, linker)
        if target:
            edges.append(Edge(document.id, target, "REFERS_TO"))
            for clause in article.clauses:
                cid = f"{aid}::clause:{clause.number}"
                edges.extend(amendment_links(cid, clause.paragraphs, target, linker))
            continue
        if not article.clauses:
            edges.extend(linker.references(aid, article.text, document.id, article.number))
            continue
        intro = "\n".join([article.heading, *article.intro])
        edges.extend(linker.references(aid, intro, document.id, article.number))
        for clause in article.clauses:
            cid = f"{aid}::clause:{clause.number}"
            target = instruction_target(clause.text, document.id, linker)
            if target:
                edges.extend(amendment_links(cid, clause.paragraphs, target, linker))
            else:
                edges.extend(linker.references(cid, clause.text, document.id, article.number))
    return list(dict.fromkeys(edges))
