#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import fitz

try:
    from tools.extract_pdf_candidates import (
        extract_exercise_candidates,
        extract_heading_candidates,
        page_references,
        sha256_file,
    )
except ModuleNotFoundError:  # Direct execution: python tools/verify_cvaa2e_source.py
    from extract_pdf_candidates import (
        extract_exercise_candidates,
        extract_heading_candidates,
        page_references,
        sha256_file,
    )

EXPECTED_SHA256 = "f3887dd48aa497ada733d730f69e884fe2fda3d988c4bb17fc47c4a492b7c4cf"
EXPECTED_PAGE_COUNT = 938
EXPECTED_LABELS = {
    0: "Cover",
    1: "i",
    21: "xxi",
    22: "1",
    101: "80",
    287: "266",
    741: "720",
    756: "735",
    768: "747",
    781: "760",
    937: "916",
}
EXPECTED_ROOTS = [
    ("1", "1", "Chapter 1 Introduction"),
    ("2", "27", "Chapter 2 Image formation"),
    ("3", "84", "Chapter 3 Image processing"),
    ("4", "151", "Chapter 4 Model fitting and optimization"),
    ("5", "185", "Chapter 5 Deep Learning"),
    ("6", "270", "Chapter 6 Recognition"),
    ("7", "329", "Chapter 7 Feature detection and matching"),
    ("8", "396", "Chapter 8 Image alignment and stitching"),
    ("9", "437", "Chapter 9 Motion estimation"),
    ("10", "477", "Chapter 10 Computational photography"),
    ("11", "537", "Chapter 11 Structure from motion and SLAM"),
    ("12", "589", "Chapter 12 Depth estimation"),
    ("13", "633", "Chapter 13 3D reconstruction"),
    ("14", "675", "Chapter 14 Image-based rendering"),
    ("15", "717", "Chapter 15 Conclusion"),
    ("A", "720", "Appendix A Linear algebra and numerical techniques"),
    ("B", "735", "Appendix B Bayesian modeling and inference"),
    ("C", "747", "Appendix C Supplementary material"),
]
EXPECTED_NUMBERED_HEADING_COUNT = 351
EXPECTED_LEARNING_HEADING_COUNT = 242
EXPECTED_EXERCISE_COUNTS = {
    "2": 9,
    "3": 29,
    "4": 4,
    "5": 19,
    "6": 13,
    "7": 15,
    "8": 14,
    "9": 10,
    "10": 12,
    "11": 16,
    "12": 11,
    "13": 8,
    "14": 12,
}


def verify(pdf_path: Path) -> dict[str, object]:
    actual_sha = sha256_file(pdf_path)
    if actual_sha != EXPECTED_SHA256:
        raise ValueError(f"unexpected PDF SHA-256: {actual_sha}")

    document = fitz.open(pdf_path)
    try:
        if document.page_count != EXPECTED_PAGE_COUNT:
            raise ValueError(f"unexpected PDF page count: {document.page_count}")

        pages = page_references(document)
        for page_index, expected_label in EXPECTED_LABELS.items():
            actual_label = pages[page_index]["printed_page_label"]
            if actual_label != expected_label:
                raise ValueError(
                    f"page label mismatch at {page_index}: {actual_label!r} != {expected_label!r}"
                )

        headings = extract_heading_candidates(document)
        numbered = [item for item in headings if item.numbered]
        learning = [item for item in headings if not item.numbered]
        if len(numbered) != EXPECTED_NUMBERED_HEADING_COUNT:
            raise ValueError(f"unexpected numbered heading count: {len(numbered)}")
        if len(learning) != EXPECTED_LEARNING_HEADING_COUNT:
            raise ValueError(f"unexpected learning heading count: {len(learning)}")

        roots = [
            (item.printed_section_id, item.printed_page_label, item.text)
            for item in numbered
            if item.printed_section_id is not None and "." not in item.printed_section_id
        ]
        if roots != EXPECTED_ROOTS:
            raise ValueError(f"unexpected root heading sequence: {roots}")

        exercises = extract_exercise_candidates(document)
        counts = Counter(item.chapter_id for item in exercises)
        if dict(counts) != EXPECTED_EXERCISE_COUNTS:
            raise ValueError(f"unexpected exercise counts by chapter: {dict(counts)}")
        for chapter_id, expected_count in EXPECTED_EXERCISE_COUNTS.items():
            numbers = [
                item.exercise_number for item in exercises if item.chapter_id == chapter_id
            ]
            if numbers != list(range(1, expected_count + 1)):
                raise ValueError(
                    f"exercise numbering is not contiguous in chapter {chapter_id}: {numbers}"
                )
    finally:
        document.close()

    return {
        "book_id": "cvaa2e",
        "pdf_sha256": actual_sha,
        "page_count": EXPECTED_PAGE_COUNT,
        "verified_page_labels": len(EXPECTED_LABELS),
        "root_heading_count": len(EXPECTED_ROOTS),
        "numbered_heading_count": EXPECTED_NUMBERED_HEADING_COUNT,
        "learning_heading_count": EXPECTED_LEARNING_HEADING_COUNT,
        "exercise_count": sum(EXPECTED_EXERCISE_COUNTS.values()),
        "exercise_chapter_count": len(EXPECTED_EXERCISE_COUNTS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the exact Computer Vision: Algorithms and Applications, 2nd Edition source PDF"
    )
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.pdf), ensure_ascii=False))


if __name__ == "__main__":
    main()
