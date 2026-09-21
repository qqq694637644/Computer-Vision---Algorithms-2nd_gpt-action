#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

BOOK_ID = "cvaa2e"
BODY_START_INDEX = 22
BODY_END_INDEX = 781
HEADING_FONT = "NimbusSanL-Bold"
HEADING_COLOR = 10598
ANCHOR_LABEL_FONT = "NimbusRomNo9L-Medi"
COLUMN_SPLIT_RATIO = 0.90

NUMBERED_HEADING_RE = re.compile(
    r"^(?P<id>(?:\d+|[A-Z])(?:\.\d+)*)\s+(?P<title>.+)$"
)
CHAPTER_TOC_RE = re.compile(r"^Chapter\s+(?P<id>\d+)\s+(?P<title>.+)$", re.IGNORECASE)
APPENDIX_TOC_RE = re.compile(r"^Appendix\s+(?P<id>[A-Z])\s+(?P<title>.+)$", re.IGNORECASE)
FIGURE_RE = re.compile(r"\b(?:Figure|Fig\.)\s+(\d+(?:\.\d+)+)\b", re.IGNORECASE)
EQUATION_RE = re.compile(r"\((\d+(?:\.\d+)+)\)")
EXAMPLE_RE = re.compile(r"\bExample\s+(\d+(?:\.\d+)+)\b", re.IGNORECASE)
TABLE_RE = re.compile(r"\bTable\s+(\d+(?:\.\d+)+)\b", re.IGNORECASE)
EXERCISE_RE = re.compile(r"^Ex\s+(?P<id>\d+\.\d+):", re.IGNORECASE)
PROBLEMS_RE = re.compile(r"^Exercises$", re.IGNORECASE)
TERMINAL_BOUNDARY_RE = re.compile(r"^(?:References|Index)$", re.IGNORECASE)


@dataclass(frozen=True)
class TextLine:
    text: str
    pdf_page_index: int
    pdf_page_number: int
    printed_page_label: str
    bbox: tuple[float, float, float, float]
    font_names: tuple[str, ...]
    max_font_size: float
    colors: tuple[int, ...]


@dataclass(frozen=True)
class HeadingCandidate:
    text: str
    printed_section_id: str | None
    pdf_page_index: int
    pdf_page_number: int
    printed_page_label: str
    bbox: tuple[float, float, float, float]
    numbered: bool
    style_signature: str


@dataclass(frozen=True)
class ExerciseCandidate:
    exercise_id: str
    chapter_id: str
    exercise_number: int
    starred: bool
    source_order: int
    pdf_page_index: int
    pdf_page_number: int
    printed_page_label: str
    bbox: tuple[float, float, float, float]
    column: int


def clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    safe = normalized.encode("utf-8", errors="ignore").decode("utf-8")
    safe = "".join(character for character in safe if character >= " " or character in "\t\n")
    return re.sub(r"\s+", " ", safe).strip()


def normalize_page_label(value: str) -> str:
    label = clean_text(value)
    if label == "<FEFF00430031>":
        return "Cover"
    if label.startswith("<FEFF>"):
        label = label[len("<FEFF>") :]
    return label


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def page_references(doc: fitz.Document) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for index in range(doc.page_count):
        label = normalize_page_label(doc[index].get_label())
        if not label:
            raise ValueError(f"PDF page {index} has no normalized page label")
        pages.append(
            {
                "pdf_page_index": index,
                "pdf_page_number": index + 1,
                "printed_page_label": label,
            }
        )
    labels = [page["printed_page_label"] for page in pages]
    if len(labels) != len(set(labels)):
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        raise ValueError(f"normalized PDF page labels are not unique: {duplicates[:10]}")
    return pages


def extract_lines(page: fitz.Page) -> list[TextLine]:
    lines: list[TextLine] = []
    page_index = page.number
    page_label = normalize_page_label(page.get_label())
    for block in page.get_text("dict").get("blocks", []):
        for raw_line in block.get("lines", []):
            spans = [span for span in raw_line.get("spans", []) if span.get("text", "").strip()]
            if not spans:
                continue
            pieces: list[str] = []
            previous_x1: float | None = None
            for span in spans:
                span_text = str(span["text"])
                x0 = float(span["bbox"][0])
                if previous_x1 is not None and x0 - previous_x1 > 1.0:
                    pieces.append(" ")
                pieces.append(span_text)
                previous_x1 = float(span["bbox"][2])
            text = clean_text("".join(pieces))
            if not text:
                continue
            lines.append(
                TextLine(
                    text=text,
                    pdf_page_index=page_index,
                    pdf_page_number=page_index + 1,
                    printed_page_label=page_label,
                    bbox=tuple(round(float(value), 3) for value in raw_line["bbox"]),
                    font_names=tuple(sorted({span["font"] for span in spans})),
                    max_font_size=round(max(float(span["size"]) for span in spans), 3),
                    colors=tuple(sorted({int(span["color"]) for span in spans})),
                )
            )
    return lines


def _column_for_x(page_width: float, x0: float) -> int:
    return 0 if x0 < page_width * COLUMN_SPLIT_RATIO else 1


def _visual_line_compatible(
    group_bbox: tuple[float, float, float, float],
    line_bbox: tuple[float, float, float, float],
) -> bool:
    group_center = (group_bbox[1] + group_bbox[3]) / 2
    line_center = (line_bbox[1] + line_bbox[3]) / 2
    overlap = min(group_bbox[3], line_bbox[3]) - max(group_bbox[1], line_bbox[1])
    return overlap >= -1.5 and abs(group_center - line_center) <= 5.0


def merge_visual_lines(page_width: float, lines: list[TextLine]) -> list[TextLine]:
    merged: list[TextLine] = []
    for column in (0, 1):
        column_lines = sorted(
            (line for line in lines if _column_for_x(page_width, float(line.bbox[0])) == column),
            key=lambda item: (float(item.bbox[1]), float(item.bbox[0])),
        )
        groups: list[list[TextLine]] = []
        group_bboxes: list[tuple[float, float, float, float]] = []
        for line in column_lines:
            matching_index = next(
                (
                    index
                    for index in range(len(groups) - 1, -1, -1)
                    if _visual_line_compatible(group_bboxes[index], line.bbox)
                ),
                None,
            )
            if matching_index is None:
                groups.append([line])
                group_bboxes.append(line.bbox)
                continue
            groups[matching_index].append(line)
            bbox = group_bboxes[matching_index]
            group_bboxes[matching_index] = (
                min(bbox[0], line.bbox[0]),
                min(bbox[1], line.bbox[1]),
                max(bbox[2], line.bbox[2]),
                max(bbox[3], line.bbox[3]),
            )

        for group, bbox in zip(groups, group_bboxes, strict=True):
            ordered = sorted(group, key=lambda item: float(item.bbox[0]))
            first = ordered[0]
            merged.append(
                TextLine(
                    text=clean_text(" ".join(item.text for item in ordered)),
                    pdf_page_index=first.pdf_page_index,
                    pdf_page_number=first.pdf_page_number,
                    printed_page_label=first.printed_page_label,
                    bbox=tuple(round(float(value), 3) for value in bbox),
                    font_names=tuple(sorted({font for item in ordered for font in item.font_names})),
                    max_font_size=max(item.max_font_size for item in ordered),
                    colors=tuple(sorted({color for item in ordered for color in item.colors})),
                )
            )
    return sorted(
        merged,
        key=lambda item: (_column_for_x(page_width, item.bbox[0]), item.bbox[1], item.bbox[0]),
    )


def _is_blue_bold(line: TextLine, size: float, tolerance: float = 0.08) -> bool:
    return (
        HEADING_FONT in line.font_names
        and HEADING_COLOR in line.colors
        and abs(line.max_font_size - size) <= tolerance
    )


def is_heading_line(line: TextLine) -> bool:
    """Return whether a raw line is an unnumbered learning-unit heading."""
    if not (_is_blue_bold(line, 10.617, 0.08) or _is_blue_bold(line, 12.740, 0.08)):
        return False
    if NUMBERED_HEADING_RE.match(line.text):
        return False
    if re.match(r"^(?:Figure|Fig\.|Table|Example|Ex\s+\d+\.\d+:)", line.text, re.IGNORECASE):
        return False
    return True


def merge_wrapped_heading_lines(lines: list[TextLine]) -> list[TextLine]:
    # Szeliski's unnumbered subheads are short, single raw lines. Keeping their exact
    # bbox avoids merging the following body sentence into the heading.
    return [line for line in lines if is_heading_line(line)]


def _chapter_candidates(doc: fitz.Document) -> list[HeadingCandidate]:
    candidates: list[HeadingCandidate] = []
    for level, raw_title, pdf_page_number in doc.get_toc(simple=True):
        if level != 1:
            continue
        title = clean_text(raw_title)
        match = CHAPTER_TOC_RE.match(title) or APPENDIX_TOC_RE.match(title)
        if match is None:
            continue
        section_id = match.group("id")
        page_index = int(pdf_page_number) - 1
        page = doc[page_index]
        raw_lines = extract_lines(page)
        prefix_pattern = re.compile(
            rf"^(?:Chapter\s+{re.escape(section_id)}|Appendix\s+{re.escape(section_id)})$",
            re.IGNORECASE,
        )
        prefix_lines = [line for line in raw_lines if prefix_pattern.match(line.text)]
        title_lines = [
            line
            for line in raw_lines
            if "NimbusRomNo9L-Medi" in line.font_names
            and 25.5 <= line.max_font_size <= 27.5
            and line.bbox[1] < 220
        ]
        if not prefix_lines or not title_lines:
            raise ValueError(f"cannot locate root heading layout for {title!r}")
        relevant = [prefix_lines[0], *title_lines]
        bbox = (
            min(line.bbox[0] for line in relevant),
            min(line.bbox[1] for line in relevant),
            max(line.bbox[2] for line in relevant),
            max(line.bbox[3] for line in relevant),
        )
        candidates.append(
            HeadingCandidate(
                text=title,
                printed_section_id=section_id,
                pdf_page_index=page_index,
                pdf_page_number=page_index + 1,
                printed_page_label=normalize_page_label(page.get_label()),
                bbox=tuple(round(float(value), 3) for value in bbox),
                numbered=True,
                style_signature="root-outline+NimbusRomNo9L-Medi-26.4",
            )
        )
    return sorted(candidates, key=lambda item: (item.pdf_page_index, item.bbox[1], item.bbox[0]))


def extract_chapter_candidates(doc: fitz.Document) -> list[HeadingCandidate]:
    return _chapter_candidates(doc)


def _numbered_subsection_candidates(doc: fitz.Document) -> list[HeadingCandidate]:
    candidates: list[HeadingCandidate] = []
    for page_index in range(BODY_START_INDEX, min(BODY_END_INDEX, doc.page_count - 1) + 1):
        page = doc[page_index]
        for line in merge_visual_lines(float(page.rect.width), extract_lines(page)):
            if not (
                _is_blue_bold(line, 15.290, 0.08)
                or _is_blue_bold(line, 12.740, 0.08)
            ):
                continue
            match = NUMBERED_HEADING_RE.match(line.text)
            if match is None or "." not in match.group("id"):
                continue
            section_id = match.group("id")
            candidates.append(
                HeadingCandidate(
                    text=line.text,
                    printed_section_id=section_id,
                    pdf_page_index=page_index,
                    pdf_page_number=page_index + 1,
                    printed_page_label=normalize_page_label(page.get_label()),
                    bbox=line.bbox,
                    numbered=True,
                    style_signature=(
                        f"fonts={','.join(line.font_names)};size={line.max_font_size};"
                        f"colors={','.join(str(color) for color in line.colors)}"
                    ),
                )
            )
    ids = [item.printed_section_id for item in candidates]
    if len(ids) != len(set(ids)):
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        raise ValueError(f"duplicate visible printed section headings: {duplicates}")
    return candidates


def _candidate_order(candidate: HeadingCandidate) -> tuple[int, float, float]:
    return (candidate.pdf_page_index, candidate.bbox[1], candidate.bbox[0])


def _learning_candidates(
    doc: fitz.Document,
    printed: list[HeadingCandidate],
) -> list[HeadingCandidate]:
    printed_sorted = sorted(printed, key=_candidate_order)
    candidates: list[HeadingCandidate] = []
    for page_index in range(BODY_START_INDEX, min(BODY_END_INDEX, doc.page_count - 1) + 1):
        page = doc[page_index]
        for line in extract_lines(page):
            if not is_heading_line(line):
                continue
            overlaps_printed_heading = any(
                item.pdf_page_index == page_index
                and min(item.bbox[3], line.bbox[3]) - max(item.bbox[1], line.bbox[1]) > 0
                and min(item.bbox[2], line.bbox[2]) - max(item.bbox[0], line.bbox[0]) > -2
                for item in printed_sorted
            )
            if overlaps_printed_heading:
                continue
            order = (page_index, line.bbox[1], line.bbox[0])
            active = [item for item in printed_sorted if _candidate_order(item) < order]
            if not active:
                continue
            printed_section_id = active[-1].printed_section_id
            if printed_section_id is None:
                continue
            candidates.append(
                HeadingCandidate(
                    text=line.text,
                    printed_section_id=printed_section_id,
                    pdf_page_index=page_index,
                    pdf_page_number=page_index + 1,
                    printed_page_label=normalize_page_label(page.get_label()),
                    bbox=line.bbox,
                    numbered=False,
                    style_signature=(
                        f"fonts={','.join(line.font_names)};size={line.max_font_size};"
                        f"colors={','.join(str(color) for color in line.colors)}"
                    ),
                )
            )
    return candidates


def extract_heading_candidates(doc: fitz.Document) -> list[HeadingCandidate]:
    printed = [*_chapter_candidates(doc), *_numbered_subsection_candidates(doc)]
    printed = sorted(printed, key=_candidate_order)
    learning = _learning_candidates(doc, printed)
    return sorted([*printed, *learning], key=_candidate_order)


def chapter_end_indices(
    doc: fitz.Document,
    chapters: list[HeadingCandidate],
) -> dict[str, int]:
    ordered = sorted(chapters, key=_candidate_order)
    result: dict[str, int] = {}
    for index, chapter in enumerate(ordered):
        section_id = chapter.printed_section_id
        if section_id is None:
            continue
        if index + 1 < len(ordered):
            result[section_id] = ordered[index + 1].pdf_page_index - 1
        else:
            result[section_id] = BODY_END_INDEX
    return result


def _as_text_line(candidate: HeadingCandidate) -> TextLine:
    return TextLine(
        text=candidate.text,
        pdf_page_index=candidate.pdf_page_index,
        pdf_page_number=candidate.pdf_page_number,
        printed_page_label=candidate.printed_page_label,
        bbox=candidate.bbox,
        font_names=(HEADING_FONT,),
        max_font_size=15.29,
        colors=(HEADING_COLOR,),
    )


def extract_problem_headings(doc: fitz.Document) -> dict[str, TextLine]:
    headings: dict[str, TextLine] = {}
    for candidate in _numbered_subsection_candidates(doc):
        match = NUMBERED_HEADING_RE.match(candidate.text)
        if match is None or match.group("title").casefold() != "exercises":
            continue
        chapter_id = match.group("id").split(".", 1)[0]
        if not chapter_id.isdigit():
            continue
        headings[chapter_id] = _as_text_line(candidate)
    return headings


def _reading_key(line: TextLine, page_width: float) -> tuple[int, float, float]:
    return (_column_for_x(page_width, line.bbox[0]), line.bbox[1], line.bbox[0])


def extract_exercise_candidates(doc: fitz.Document) -> list[ExerciseCandidate]:
    chapters = [item for item in extract_chapter_candidates(doc) if item.printed_section_id and item.printed_section_id.isdigit()]
    chapter_ends = chapter_end_indices(doc, extract_chapter_candidates(doc))
    exercise_headings = extract_problem_headings(doc)
    candidates: list[ExerciseCandidate] = []
    for chapter in chapters:
        chapter_id = chapter.printed_section_id
        assert chapter_id is not None
        exercises_heading = exercise_headings.get(chapter_id)
        if exercises_heading is None:
            continue
        chapter_candidates: list[tuple[TextLine, int]] = []
        for page_index in range(exercises_heading.pdf_page_index, chapter_ends[chapter_id] + 1):
            page = doc[page_index]
            for line in sorted(extract_lines(page), key=lambda item: _reading_key(item, page.rect.width)):
                if page_index == exercises_heading.pdf_page_index and _reading_key(
                    line, page.rect.width
                ) <= _reading_key(exercises_heading, page.rect.width):
                    continue
                match = EXERCISE_RE.match(line.text)
                if match is None:
                    continue
                exercise_id = match.group("id")
                prefix, number = exercise_id.split(".", 1)
                if prefix != chapter_id or not number.isdigit():
                    continue
                if "NimbusRomNo9L-Medi" not in line.font_names:
                    continue
                chapter_candidates.append((line, int(number)))

        seen: set[str] = set()
        for source_order, (line, number) in enumerate(chapter_candidates, start=1):
            exercise_id = f"{chapter_id}.{number}"
            if exercise_id in seen:
                raise ValueError(f"duplicate exercise candidate: {exercise_id}")
            seen.add(exercise_id)
            candidates.append(
                ExerciseCandidate(
                    exercise_id=exercise_id,
                    chapter_id=chapter_id,
                    exercise_number=number,
                    starred=False,
                    source_order=source_order,
                    pdf_page_index=line.pdf_page_index,
                    pdf_page_number=line.pdf_page_number,
                    printed_page_label=line.printed_page_label,
                    bbox=line.bbox,
                    column=_column_for_x(float(doc[line.pdf_page_index].rect.width), line.bbox[0]),
                )
            )
    return candidates


def _records(
    pattern: re.Pattern[str],
    source_lines: list[TextLine],
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[float, float, float, float]]] = set()
    for line in source_lines:
        for match in pattern.finditer(line.text):
            key = (match.group(1), line.bbox)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "id": match.group(1),
                    "bbox": list(line.bbox),
                    "font_names": list(line.font_names),
                    "max_font_size": line.max_font_size,
                    "colors": list(line.colors),
                }
            )
    return found


def extract_page_anchors(doc: fitz.Document) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    headings_by_page: dict[int, list[HeadingCandidate]] = {}
    for candidate in extract_heading_candidates(doc):
        headings_by_page.setdefault(candidate.pdf_page_index, []).append(candidate)
    exercises_by_page: dict[int, list[ExerciseCandidate]] = {}
    for candidate in extract_exercise_candidates(doc):
        exercises_by_page.setdefault(candidate.pdf_page_index, []).append(candidate)

    for page_index in range(doc.page_count):
        page = doc[page_index]
        lines = extract_lines(page)
        visual_lines = merge_visual_lines(float(page.rect.width), lines)
        text = clean_text("\n".join(line.text for line in visual_lines))
        figure_records = _records(FIGURE_RE, lines)
        equation_records = _records(EQUATION_RE, lines)
        example_records = _records(EXAMPLE_RE, lines)
        table_records = _records(TABLE_RE, lines)
        heading_records = [
            {"text": candidate.text, "bbox": list(candidate.bbox)}
            for candidate in headings_by_page.get(page_index, [])
        ]
        text_records = [{"text": line.text, "bbox": list(line.bbox)} for line in visual_lines]
        exercise_records = [
            {
                "id": item.exercise_id,
                "bbox": list(item.bbox),
                "starred": item.starred,
                "column": item.column,
            }
            for item in exercises_by_page.get(page_index, [])
        ]
        running_header = clean_text(
            page.get_textbox(fitz.Rect(0, 0, page.rect.width, min(70, page.rect.height)))
        )
        anchors.append(
            {
                "pdf_page_index": page_index,
                "printed_page_label": normalize_page_label(page.get_label()),
                "page_width": float(page.rect.width),
                "heading_records": heading_records,
                "text_records": text_records,
                "figure_records": figure_records,
                "equation_records": equation_records,
                "example_records": example_records,
                "table_records": table_records,
                "exercise_records": exercise_records,
                "figure_ids": list(dict.fromkeys(item["id"] for item in figure_records)),
                "equation_ids": list(dict.fromkeys(item["id"] for item in equation_records)),
                "example_ids": list(dict.fromkeys(item["id"] for item in example_records)),
                "table_ids": list(dict.fromkeys(item["id"] for item in table_records)),
                "normalized_text": text,
                "running_header": running_header,
            }
        )
    return anchors


def extract_outline(doc: fitz.Document) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for level, title, pdf_page_number in doc.get_toc(simple=True):
        cleaned = clean_text(title)
        if not cleaned:
            continue
        page_index = int(pdf_page_number) - 1
        outline.append(
            {
                "level": int(level),
                "title": cleaned,
                "pdf_page_number": int(pdf_page_number),
                "pdf_page_index": page_index,
                "printed_page_label": normalize_page_label(doc[page_index].get_label()),
            }
        )
    return outline


def extract_candidates(pdf_path: Path) -> dict[str, Any]:
    document = fitz.open(pdf_path)
    try:
        return {
            "source": {
                "pdf_filename": pdf_path.name,
                "pdf_sha256": sha256_file(pdf_path),
                "page_count": document.page_count,
                "metadata": document.metadata,
            },
            "pages": page_references(document),
            "outline": extract_outline(document),
            "heading_candidates": [asdict(candidate) for candidate in extract_heading_candidates(document)],
            "exercise_candidates": [asdict(candidate) for candidate in extract_exercise_candidates(document)],
            "page_anchors": extract_page_anchors(document),
        }
    finally:
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract catalog candidates from Computer Vision: Algorithms and Applications, 2nd Edition"
    )
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    payload = extract_candidates(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "page_count": payload["source"]["page_count"],
                "heading_candidates": len(payload["heading_candidates"]),
                "exercise_candidates": len(payload["exercise_candidates"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
