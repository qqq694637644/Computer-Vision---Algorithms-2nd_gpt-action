#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
import yaml

from app.models.locator import (
    BookMetadata,
    BoundaryAnchor,
    ContentWindow,
    EvidenceRequirement,
    HeadingLocation,
    PageClassification,
    PageCoverage,
    PageRange,
    PageReference,
    query_safe_anchor,
)
from app.models.manifest import (
    BookManifest,
    BookManifestPackage,
    LearningUnitManifest,
    ManifestRetrievalStep,
    PrintedSectionManifest,
    PrintedSectionManifestShard,
)
from tools.extract_pdf_candidates import (
    BODY_END_INDEX,
    BODY_START_INDEX,
    HeadingCandidate,
    clean_text,
    extract_heading_candidates,
    extract_lines,
    extract_page_anchors,
    page_references,
    sha256_file,
)

BOOK_ID = "cvaa2e"
BOOK_TITLE = "Computer Vision: Algorithms and Applications, 2nd Edition"
BOOK_AUTHOR = "Richard Szeliski"


@dataclass(frozen=True)
class SourceBoundary:
    kind: Literal["heading", "text"]
    text: str
    pdf_page_index: int
    pdf_page_number: int
    printed_page_label: str
    bbox: tuple[float, float, float, float]


@dataclass
class SourceNode:
    title: str
    source_heading: str
    source_location: HeadingLocation
    source_level: int
    start_index: int
    end_index: int = 0
    end_before: SourceBoundary | None = None
    children: list[SourceNode] = field(default_factory=list)


def _display_title(candidate: HeadingCandidate) -> str:
    text = clean_text(candidate.text)
    text = re.sub(r"^(?:Chapter|Appendix)\s+(?:\d+|[A-Z])\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:\d+|[A-Z])(?:\.\d+)*\s+", "", text)
    return text


def _page_reference(raw: dict[str, Any]) -> PageReference:
    return PageReference.model_validate(raw)


def _heading_location(candidate: HeadingCandidate, pages: list[PageReference]) -> HeadingLocation:
    return HeadingLocation(page=pages[candidate.pdf_page_index], bbox=list(candidate.bbox))


def _heading_boundary(candidate: HeadingCandidate) -> SourceBoundary:
    return SourceBoundary(
        kind="heading",
        text=candidate.text,
        pdf_page_index=candidate.pdf_page_index,
        pdf_page_number=candidate.pdf_page_number,
        printed_page_label=candidate.printed_page_label,
        bbox=candidate.bbox,
    )


def _page_range(start: int, end: int, pages: list[PageReference]) -> PageRange:
    return PageRange(
        pdf_page_index_start=start,
        pdf_page_index_end=end,
        pdf_page_number_start=start + 1,
        pdf_page_number_end=end + 1,
        printed_page_start=pages[start].printed_page_label,
        printed_page_end=pages[end].printed_page_label,
    )


def _within_window(record: dict[str, Any], *, start_y: float | None, end_y: float | None) -> bool:
    y0 = float(record["bbox"][1])
    if start_y is not None and y0 < start_y - 0.5:
        return False
    return not (end_y is not None and y0 >= end_y - 0.5)


def _coverage_for_page(
    page_anchor: dict[str, Any], *, start_y: float | None, end_y: float | None
) -> PageCoverage:
    def ids(key: str) -> list[str]:
        return list(
            dict.fromkeys(
                record["id"]
                for record in page_anchor[key]
                if _within_window(record, start_y=start_y, end_y=end_y)
            )
        )

    return PageCoverage(
        subheadings=list(
            dict.fromkeys(
                clean_text(record["text"])
                for record in page_anchor["heading_records"]
                if _within_window(record, start_y=start_y, end_y=end_y)
            )
        ),
        figure_ids=ids("figure_records"),
        equation_ids=ids("equation_records"),
        example_ids=ids("example_records"),
        table_ids=ids("table_records"),
    )


def _distinctive_text(
    document: fitz.Document,
    page_index: int,
    *,
    start_y: float | None,
    end_y: float | None,
) -> str:
    candidates: list[tuple[int, int, str]] = []
    for line in extract_lines(document[page_index]):
        if start_y is not None and line.bbox[1] < start_y - 0.5:
            continue
        if end_y is not None and line.bbox[1] >= end_y - 0.5:
            continue
        if line.bbox[1] < 65:
            continue
        words = re.findall(r"[A-Za-z][A-Za-z'-]+", line.text)
        if len(words) < 5:
            continue
        if re.match(r"^(?:Figure|Fig\.|Table|Example|Ex\s+\d+\.\d+:)", line.text, re.I):
            continue
        phrase = clean_text(line.text)
        candidates.append((len({word.casefold() for word in words[:14]}), len(phrase), phrase))
    if not candidates:
        for line in extract_lines(document[page_index]):
            if start_y is not None and line.bbox[1] < start_y - 0.5:
                continue
            if end_y is not None and line.bbox[1] >= end_y - 0.5:
                continue
            if line.bbox[1] < 65:
                continue
            if len(line.text) >= 12:
                candidates.append((1, len(line.text), clean_text(line.text)))
    if not candidates:
        raise ValueError(f"cannot derive a text anchor for PDF page index {page_index}")
    return max(candidates)[2]


def _safe_queries(
    node: SourceNode,
    page_label: str,
    start_anchor: BoundaryAnchor | None,
    text_anchor: str,
) -> list[str]:
    primary = query_safe_anchor(start_anchor.value if start_anchor is not None else text_anchor)
    secondary = query_safe_anchor(text_anchor)
    title = query_safe_anchor(node.title)
    queries = [
        f"+({primary}) +(printed page {page_label}) --QDF=0",
        f"+({secondary}) +({title}) +(printed page {page_label}) --QDF=0",
    ]
    if queries[0] == queries[1]:
        queries[1] = f"+({secondary}) +(text) +(printed page {page_label}) --QDF=0"
    return queries


def _build_plan(
    document: fitz.Document,
    pages: list[PageReference],
    page_anchors: list[dict[str, Any]],
    node: SourceNode,
) -> list[ManifestRetrievalStep]:
    steps: list[ManifestRetrievalStep] = []
    for page_index in range(node.start_index, node.end_index + 1):
        first = page_index == node.start_index
        last = page_index == node.end_index
        start_anchor = BoundaryAnchor(kind="heading", value=node.source_heading) if first else None
        end_anchor = None
        if last and node.end_before is not None and node.end_before.pdf_page_index == page_index:
            end_anchor = BoundaryAnchor(kind=node.end_before.kind, value=node.end_before.text)

        start_y = node.source_location.bbox[1] if first else None
        end_y = node.end_before.bbox[1] if end_anchor is not None else None
        coverage = _coverage_for_page(page_anchors[page_index], start_y=start_y, end_y=end_y)
        text_anchor = _distinctive_text(
            document,
            page_index,
            start_y=start_y,
            end_y=end_y,
        )
        evidence = [
            EvidenceRequirement(
                kind="printed_page_equals",
                value=pages[page_index].printed_page_label,
                verification_mode="visual_required",
            ),
            EvidenceRequirement(
                kind="contains_text",
                value=text_anchor,
                verification_mode="text_or_visual",
            ),
        ]
        if start_anchor is not None:
            evidence.append(
                EvidenceRequirement(
                    kind="contains_heading",
                    value=start_anchor.value,
                    verification_mode="visual_required",
                )
            )
        if end_anchor is not None:
            evidence.append(
                EvidenceRequirement(
                    kind=("contains_heading" if end_anchor.kind == "heading" else "contains_text"),
                    value=end_anchor.value,
                    verification_mode="visual_required",
                )
            )

        steps.append(
            ManifestRetrievalStep(
                page=pages[page_index],
                content_window=ContentWindow(start_at=start_anchor, end_before=end_anchor),
                queries=_safe_queries(
                    node,
                    pages[page_index].printed_page_label,
                    start_anchor,
                    text_anchor,
                ),
                required_evidence=evidence,
                coverage=coverage,
            )
        )
    return steps


def _has_content_before_boundary(document: fitz.Document, boundary: SourceBoundary) -> bool:
    for line in extract_lines(document[boundary.pdf_page_index]):
        if line.bbox[1] >= boundary.bbox[1] - 0.5:
            continue
        if line.bbox[1] < 65:
            continue
        text = clean_text(line.text)
        if not text or re.fullmatch(r"(?:\d+|[ivxlcdm]+)", text, re.I):
            continue
        if len(text) >= 12:
            return True
    return False


def _range_end_before_next(
    document: fitz.Document,
    current_start: int,
    next_boundary: SourceBoundary,
) -> tuple[int, SourceBoundary | None]:
    if next_boundary.pdf_page_index == current_start:
        return next_boundary.pdf_page_index, next_boundary
    if _has_content_before_boundary(document, next_boundary):
        return next_boundary.pdf_page_index, next_boundary
    return next_boundary.pdf_page_index - 1, None


def _candidate_order(candidate: HeadingCandidate) -> tuple[int, float, float]:
    return (candidate.pdf_page_index, candidate.bbox[1], candidate.bbox[0])


def _section_parent(section_id: str) -> str | None:
    return section_id.rsplit(".", 1)[0] if "." in section_id else None


def _printed_ranges(
    document: fitz.Document,
    printed: list[HeadingCandidate],
) -> dict[str, tuple[int, SourceBoundary | None]]:
    ordered = sorted(printed, key=_candidate_order)
    result: dict[str, tuple[int, SourceBoundary | None]] = {}
    for index, current in enumerate(ordered):
        section_id = current.printed_section_id
        assert section_id is not None
        next_non_descendant = next(
            (
                candidate
                for candidate in ordered[index + 1 :]
                if not str(candidate.printed_section_id).startswith(f"{section_id}.")
            ),
            None,
        )
        if next_non_descendant is None:
            result[section_id] = (BODY_END_INDEX, None)
        else:
            result[section_id] = _range_end_before_next(
                document,
                current.pdf_page_index,
                _heading_boundary(next_non_descendant),
            )
    return result


def _learning_nodes(
    document: fitz.Document,
    candidates: list[HeadingCandidate],
    pages: list[PageReference],
    terminal_end: int,
    terminal_boundary: SourceBoundary | None,
) -> list[SourceNode]:
    roots = [
        SourceNode(
            title=_display_title(candidate),
            source_heading=candidate.text,
            source_location=_heading_location(candidate, pages),
            source_level=1,
            start_index=candidate.pdf_page_index,
        )
        for candidate in sorted(candidates, key=_candidate_order)
    ]
    for index, node in enumerate(roots):
        following = roots[index + 1] if index + 1 < len(roots) else None
        if following is not None:
            boundary = SourceBoundary(
                kind="heading",
                text=following.source_heading,
                pdf_page_index=following.start_index,
                pdf_page_number=following.start_index + 1,
                printed_page_label=following.source_location.page.printed_page_label,
                bbox=tuple(following.source_location.bbox),
            )
            node.end_index, node.end_before = _range_end_before_next(
                document,
                node.start_index,
                boundary,
            )
        else:
            node.end_index = terminal_end
            node.end_before = terminal_boundary
    return roots


def _build_unit_manifest(
    document: fitz.Document,
    pages: list[PageReference],
    page_anchors: list[dict[str, Any]],
    node: SourceNode,
) -> LearningUnitManifest:
    return LearningUnitManifest(
        title=node.title,
        source_heading=node.source_heading,
        source_location=node.source_location,
        source_level=node.source_level,
        page_range=_page_range(node.start_index, node.end_index, pages),
        outline=[],
        retrieval_plan=_build_plan(document, pages, page_anchors, node),
        children=[],
    )


def _root_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _shard_token(value: str) -> str:
    return f"{int(value):02d}" if value.isdigit() else value


def build_manifest(pdf_path: Path) -> BookManifest:
    document = fitz.open(pdf_path)
    try:
        pages = [_page_reference(item) for item in page_references(document)]
        candidates = extract_heading_candidates(document)
        printed = [candidate for candidate in candidates if candidate.numbered]
        learning = [candidate for candidate in candidates if not candidate.numbered]
        printed_by_id = {
            candidate.printed_section_id: candidate
            for candidate in printed
            if candidate.printed_section_id is not None
        }
        if len(printed_by_id) != len(printed):
            raise ValueError("printed heading ids are not unique")
        learning_by_printed: dict[str, list[HeadingCandidate]] = {}
        for candidate in learning:
            if candidate.printed_section_id is None:
                raise ValueError(f"learning heading has no printed parent: {candidate.text!r}")
            learning_by_printed.setdefault(candidate.printed_section_id, []).append(candidate)

        ranges = _printed_ranges(document, printed)
        page_anchors = extract_page_anchors(document)
        printed_sections: list[PrintedSectionManifest] = []
        ordered_printed = sorted(printed, key=_candidate_order)
        for candidate in ordered_printed:
            section_id = candidate.printed_section_id
            assert section_id is not None
            parent_id = _section_parent(section_id)
            if parent_id is not None and parent_id not in printed_by_id:
                raise ValueError(f"printed section {section_id} has missing source parent {parent_id}")
            end_index, end_before = ranges[section_id]
            direct_children = [
                item
                for item in ordered_printed
                if _section_parent(str(item.printed_section_id)) == section_id
            ]
            first_child = direct_children[0] if direct_children else None
            learning_terminal_end = end_index
            learning_terminal_boundary = end_before
            if first_child is not None:
                learning_terminal_end, learning_terminal_boundary = _range_end_before_next(
                    document,
                    candidate.pdf_page_index,
                    _heading_boundary(first_child),
                )
            learning_nodes = _learning_nodes(
                document,
                learning_by_printed.get(section_id, []),
                pages,
                learning_terminal_end,
                learning_terminal_boundary,
            )
            node = SourceNode(
                title=_display_title(candidate),
                source_heading=candidate.text,
                source_location=_heading_location(candidate, pages),
                source_level=0,
                start_index=candidate.pdf_page_index,
                end_index=end_index,
                end_before=end_before,
            )
            printed_sections.append(
                PrintedSectionManifest(
                    printed_section_id=section_id,
                    parent_printed_section_id=parent_id,
                    title=node.title,
                    source_heading=node.source_heading,
                    source_location=node.source_location,
                    page_range=_page_range(node.start_index, node.end_index, pages),
                    outline=[str(item.printed_section_id) for item in direct_children]
                    + [item.title for item in learning_nodes],
                    retrieval_plan=_build_plan(document, pages, page_anchors, node),
                    learning_units=[
                        _build_unit_manifest(document, pages, page_anchors, item)
                        for item in learning_nodes
                    ],
                )
            )

        classifications: list[PageClassification] = []
        for page in pages:
            if page.pdf_page_index < BODY_START_INDEX:
                category = "front_matter"
                reason = "cover, preface, and contents before Chapter 1"
            elif page.pdf_page_index <= BODY_END_INDEX:
                category = "body"
                reason = "Chapters 1-15 and Appendices A-C"
            else:
                category = "back_matter"
                reason = "references and index"
            classifications.append(PageClassification(page=page, category=category, reason=reason))

        return BookManifest(
            data_version="3",
            index_status="complete",
            book=BookMetadata(
                book_id=BOOK_ID,
                title=BOOK_TITLE,
                author=BOOK_AUTHOR,
                pdf_filename=pdf_path.name,
                pdf_sha256=sha256_file(pdf_path),
                page_count=document.page_count,
            ),
            pages=pages,
            page_classifications=classifications,
            printed_sections=printed_sections,
        )
    finally:
        document.close()


def write_manifest_package(manifest: BookManifest, output: Path) -> None:
    groups: dict[str, list[PrintedSectionManifest]] = {}
    for section in manifest.printed_sections:
        root = section.printed_section_id.split(".", 1)[0]
        groups.setdefault(root, []).append(section)

    output.parent.mkdir(parents=True, exist_ok=True)
    for stale in output.parent.glob("manifest.sections.*.yaml"):
        stale.unlink()

    shard_names: list[str] = []
    for root in sorted(groups, key=_root_sort_key):
        shard_name = f"manifest.sections.{_shard_token(root)}.yaml"
        shard_path = output.parent / shard_name
        shard = PrintedSectionManifestShard(data_version="3", printed_sections=groups[root])
        shard_path.write_text(
            yaml.safe_dump(
                shard.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
                width=120,
            ),
            encoding="utf-8",
        )
        shard_names.append(shard_name)

    package = BookManifestPackage(
        data_version=manifest.data_version,
        index_status=manifest.index_status,
        book=manifest.book,
        pages=manifest.pages,
        page_classifications=manifest.page_classifications,
        printed_section_shards=shard_names,
    )
    output.write_text(
        yaml.safe_dump(
            package.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the complete CVAA2E Version 3 section manifest")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    manifest = build_manifest(args.pdf)
    write_manifest_package(manifest, args.output)
    print(
        f"wrote {args.output}: pages={len(manifest.pages)}, "
        f"printed_sections={len(manifest.printed_sections)}, "
        f"learning_units={sum(len(section.learning_units) for section in manifest.printed_sections)}"
    )


if __name__ == "__main__":
    main()
