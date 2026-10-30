"""Text extraction from Official Gazette (Công báo) PDFs.

PDF text arrives as visual lines. Gazette paragraphs indent their first line, so line
geometry rebuilds paragraphs; headers, page numbers, and signature stamps are dropped.
"""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

# The page number may share the header's visual line.
GAZETTE_HEADER = re.compile(
    r"^(?:\d{1,4} )?CÔNG BÁO/Số [\d +]+/Ngày \d{1,2}-\d{1,2}-\d{4}(?: \d{1,4})?$"
)
PAGE_NUMBER = re.compile(r"^\d{1,4}$")
SIGNATURE = re.compile(
    r"^(?:Người ký: CỔNG THÔNG TIN|Ký bởi: CÔNG BÁO|Email: thongtinchinhphu|"
    r"Cơ quan: VĂN PHÒNG CHÍNH PHỦ|Thời gian ký: \d|Ngày ký: \d)"
)
BANNER = "VĂN BẢN QUY PHẠM PHÁP LUẬT"
CONTINUED = re.compile(r"^\(Tiếp theo Công báo số [^)]*\)$")
POINT = re.compile(r"^[a-zđ]\d?\)\s")
# Pages begin and end with furniture; page numbers elsewhere are content.
FURNITURE_ZONE = 3
INDENT = 8.0
# Legacy Vietnamese encodings substitute the Icelandic eth for the letter đ.
LOOKALIKES = str.maketrans({"Ð": "Đ", "ð": "đ"})


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    bold: bool
    page: int
    leading_spaces: int = 0


def clean(text):
    """NFC-normalize Vietnamese text and drop invisible or broken characters."""
    text = unicodedata.normalize("NFC", text).translate(LOOKALIKES)
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if char in "\t    ":
            kept.append(" ")
        elif char == "�" or category in {"Cc", "Cf", "Co", "Cs"}:
            continue
        else:
            kept.append(char)
    return re.sub(r" {2,}", " ", "".join(kept)).strip()


def page_lines(page, number):
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            raw = "".join(s["text"] for s in line["spans"])
            text = clean(raw)
            if not text:
                continue
            bold = all(s["flags"] & 16 or "Bold" in s["font"] for s in spans)
            lines.append(
                Line(
                    text,
                    spans[0]["bbox"][0],
                    line["bbox"][1],
                    line["bbox"][2],
                    bold,
                    number,
                    len(spans[0]["text"]) - len(spans[0]["text"].lstrip()),
                )
            )
    # A visual line is sometimes split into several pieces at the same height.
    merged = []
    for line in lines:
        last = merged[-1] if merged else None
        if last and abs(line.y0 - last.y0) < 2 and line.x0 >= last.x1 - 1:
            last.text = f"{last.text} {line.text}"
            last.x1, last.bold = line.x1, last.bold and line.bold
        else:
            merged.append(line)
    return merged


def drop_furniture(lines):
    kept = []
    for i, line in enumerate(lines):
        edge = i < FURNITURE_ZONE or i >= len(lines) - FURNITURE_ZONE
        if GAZETTE_HEADER.match(line.text) or SIGNATURE.match(line.text) or line.text == BANNER:
            continue
        if edge and PAGE_NUMBER.match(line.text):
            continue
        kept.append(line)
    return kept


def pdf_lines(path, continuation=False):
    """Content lines of one PDF; continuation parts lose their repeated title banner."""
    import pymupdf

    with pymupdf.open(path) as document:
        lines = [x for i, page in enumerate(document) for x in drop_furniture(page_lines(page, i))]
    if continuation:
        for i, line in enumerate(lines[:20]):
            if CONTINUED.match(line.text):
                return lines[i + 1 :]
        raise ValueError(f"{path}: continuation banner not found")
    return lines


def page_margins(lines):
    """Per-page left margin of wrapped lines.

    A margin is a start position shared by full-width lines that has a paragraph indent
    15-45 pt to its right. Appendices and body text use different layouts, so margins are
    estimated per page; a page without that pattern (a list of one-line items) inherits
    the previous page's margin.
    """
    starts = defaultdict(Counter)
    for line in lines:
        if len(line.text) >= 40:
            starts[line.page][round(line.x0)] += 1
    margins, last = {}, None
    for page in sorted({line.page for line in lines}):
        shared = [x for x, n in starts[page].items() if n >= 2]
        paired = [x for x in shared if any(15 <= y - x <= 45 for y in shared)]
        last = float(min(paired)) if paired else last
        margins[page] = last
    first = next((m for m in margins.values() if m is not None), 0.0)
    return {page: first if m is None else m for page, m in margins.items()}


def paragraphs(lines):
    """Join wrapped lines; an indented (or space-led) line starts a new paragraph."""
    margins = page_margins(lines)
    result = []
    for line in lines:
        starts = line.x0 > margins[line.page] + INDENT or line.leading_spaces >= 2
        # Legal paragraphs open with a capital, number, or point marker such as "a)" or "đ)";
        # a lowercase opening reveals a wrapped line where the layout shifts mid-page.
        if starts and line.text[0].islower() and not POINT.match(line.text):
            starts = False
        if not result or starts:
            result.append(line.text)
        else:
            result[-1] = f"{result[-1]} {line.text}"
    return [re.sub(r" {2,}", " ", p).strip() for p in result if p.strip()]


def extract_paragraphs(paths):
    """Paragraphs of a document published across one or more Gazette issues."""
    result = []
    for i, path in enumerate(paths):
        result.extend(paragraphs(pdf_lines(path, continuation=i > 0)))
    return result
