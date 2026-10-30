"""Segment cleaned paragraphs into the legal hierarchy: preamble, articles, clauses, closing.

Amending documents quote replacement text between “ and ”. Quoted paragraphs belong to the
amending clause, so headings inside quotations never open articles or clauses.
"""

import re
from dataclasses import dataclass, field

CHAPTER = re.compile(r"^Chương\s+([IVXLC]+)\b\.?\s*(.*)$")
SECTION = re.compile(r"^Mục\s+(\d+)\b\.?\s*(.*)$")
ARTICLE = re.compile(r"^Điều\s+(\d+)([a-z]?)\s*\.\s*(.*)$")
CLAUSE = re.compile(r"^(\d+)\.\s+\S")
# Signature, passage statement, or appendix heading ends the normative body.
CLOSING = re.compile(
    r"^(?:TM\.|KT\.|(?:PHÓ )?CHỦ TỊCH QUỐC HỘI|Nơi nhận|Luật này (?:đã )?được Quốc hội"
    r"|(?:PHỤ LỤC|Phụ lục)(?:\s+[IVXLC\d]+)?\s*$)"
)
RULE = re.compile(r"^[_\-–=.\s]+$")
OPEN, CLOSE = "“", "”"
# An amendment instruction never occurs inside quoted replacement text.
INSTRUCTION = re.compile(
    r"^\d+\.\s+(?:Sửa đổi|Bổ sung|Bãi bỏ|Thay thế)[^“”\"]*(?:như sau|sau đây)\s*:$"
)


def quote_depth(text, depth):
    """Track nested quotations; a straight quote closes an open quotation, else opens one."""
    for char in text:
        if char == OPEN:
            depth += 1
        elif char == CLOSE:
            depth = max(0, depth - 1)
        elif char == '"':
            depth = depth - 1 if depth else 1
    return depth


@dataclass
class Clause:
    number: str
    paragraphs: list[str]

    @property
    def text(self):
        return "\n".join(self.paragraphs)


@dataclass
class Article:
    number: str
    title: str
    chapter: str = ""
    section: str = ""
    intro: list[str] = field(default_factory=list)
    clauses: list[Clause] = field(default_factory=list)

    @property
    def heading(self):
        return f"Điều {self.number}. {self.title}".strip()

    @property
    def text(self):
        return "\n".join([self.heading, *self.intro, *(c.text for c in self.clauses)])


@dataclass
class Segmentation:
    preamble: list[str]
    articles: list[Article]
    closing: list[str]
    warnings: list[str]


def number_key(number):
    match = re.fullmatch(r"(\d+)([a-z]?)", number)
    return int(match[1]), match[2]


def is_title(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def segment(paragraphs):
    preamble, articles, closing, warnings = [], [], [], []
    chapter = section = ""
    pending = None
    article = clause = None
    depth = 0
    for text in paragraphs:
        if RULE.match(text):
            continue
        if depth and INSTRUCTION.match(text):
            warnings.append(f"quote_resync:{text[:60]}")
            depth = 0
        quoted = depth > 0 or text.startswith(OPEN)
        depth = quote_depth(text, depth)
        if closing:
            closing.append(text)
            continue
        if not quoted:
            if articles and CLOSING.match(text):
                closing.append(text)
                continue
            if match := CHAPTER.match(text):
                chapter, section, pending = f"Chương {match[1]}", "", "chapter"
                chapter += f". {match[2]}" if match[2] else ""
                article = clause = None
                continue
            if match := SECTION.match(text):
                section, pending = f"Mục {match[1]}", "section"
                section += f". {match[2]}" if match[2] else ""
                article = clause = None
                continue
            if pending and is_title(text):
                if pending == "chapter":
                    chapter += (". " if "." not in chapter else " ") + text
                else:
                    section += (". " if "." not in section else " ") + text
                continue
            pending = None
            if match := ARTICLE.match(text):
                number = match[1] + match[2]
                previous = number_key(articles[-1].number) if articles else (0, "")
                current = number_key(number)
                # Articles are numbered consecutively (10, 10a, 11, ...); any other heading
                # is quoted replacement text whose opening quote mark was lost.
                if current > previous and current[0] <= previous[0] + 1:
                    article = Article(number, match[3].strip(), chapter, section)
                    articles.append(article)
                    clause = None
                    continue
                warnings.append(f"unexpected_article_heading:{number}")
            if article and (match := CLAUSE.match(text)):
                expected = int(article.clauses[-1].number) + 1 if article.clauses else 1
                if int(match[1]) == expected:
                    clause = Clause(match[1], [text])
                    article.clauses.append(clause)
                    continue
        pending = None
        if clause:
            clause.paragraphs.append(text)
        elif article:
            article.intro.append(text)
        elif not articles:
            preamble.append(text)
        else:
            warnings.append(f"orphan_paragraph:{text[:60]}")
    if depth:
        warnings.append("unbalanced_quotes")
    return Segmentation(preamble, articles, closing, warnings)
