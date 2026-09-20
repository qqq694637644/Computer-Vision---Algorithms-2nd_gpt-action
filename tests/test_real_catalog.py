import json
import unicodedata
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.locator import query_parentheses_balanced
from app.repositories.exercise_repository import ExerciseRepository
from app.repositories.locator_repository import LocatorRepository

REAL_INDEX = Path("catalog/cvaa2e/compiled_locator_index.json")
REAL_EXERCISE_INDEX = Path("catalog/cvaa2e/compiled_exercise_index.json")
REAL_SECTION_REPORT = Path("catalog/cvaa2e/validation_report.json")
REAL_EXERCISE_REPORT = Path("catalog/cvaa2e/exercise_validation_report.json")


def test_real_catalog_starts_and_serves_cvaa2e_sections_and_exercises() -> None:
    settings = Settings(
        locator_index_path=REAL_INDEX,
        exercise_index_path=REAL_EXERCISE_INDEX,
        require_api_key=False,
    )

    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        learning = client.get("/gpt/section-locators/2.1.6")
        printed = client.get("/gpt/section-locators/3.1.5")
        appendix = client.get("/gpt/section-locators/A")
        exercise = client.get("/gpt/exercise-locators/10.9")
        chapter_exercises = client.get("/gpt/chapters/13/exercises")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "data_version": "3",
        "book_id": "cvaa2e",
        "section_count": 593,
        "page_count": 938,
        "exercise_catalog_status": "ready",
        "exercise_count": 172,
    }

    assert learning.status_code == 200
    learning_payload = learning.json()
    assert learning_payload["title"] == "2D points."
    assert learning_payload["section_kind"] == "learning_unit"
    assert learning_payload["printed_section_id"] == "2.1"
    assert learning_payload["page_range"]["printed_page_start"] == "29"
    assert learning_payload["page_range"]["printed_page_end"] == "30"

    assert printed.status_code == 200
    assert printed.json()["title"] == "Application: Tonal adjustment"
    assert printed.json()["printed_section_id"] == "3.1.5"

    assert appendix.status_code == 200
    assert appendix.json()["title"] == "Linear algebra and numerical techniques"
    assert appendix.json()["printed_section_id"] == "A"

    assert exercise.status_code == 200
    exercise_payload = exercise.json()
    assert exercise_payload["exercise_id"] == "10.9"
    assert exercise_payload["problem_page_range"]["printed_page_start"] == "535"
    assert exercise_payload["problem_page_range"]["printed_page_end"] == "536"

    assert chapter_exercises.status_code == 200
    chapter_payload = chapter_exercises.json()
    assert chapter_payload["exercise_count"] == 8
    assert chapter_payload["first_exercise"] == "13.1"
    assert chapter_payload["last_exercise"] == "13.8"


def test_real_catalog_preserves_mutual_exercise_references_without_recursive_fetch_loops() -> None:
    index = ExerciseRepository.load(REAL_EXERCISE_INDEX).index

    exercise_10_9 = index.exercises["10.9"]
    exercise_13_2 = index.exercises["13.2"]

    assert [(target.kind, target.target_id) for target in exercise_10_9.reference_targets] == [
        ("exercise", "13.2")
    ]
    assert [(target.kind, target.target_id) for target in exercise_13_2.reference_targets] == [
        ("exercise", "10.9")
    ]
    assert [step.page.printed_page_label for step in exercise_10_9.reference_retrieval_plan] == [
        "673"
    ]
    assert [step.page.printed_page_label for step in exercise_13_2.reference_retrieval_plan] == [
        "535",
        "536",
    ]


def test_real_catalog_reference_targets_resolve_against_committed_indexes() -> None:
    sections = LocatorRepository.load(REAL_INDEX).index
    exercises = ExerciseRepository.load(REAL_EXERCISE_INDEX).index

    for exercise in exercises.exercises.values():
        for target in exercise.reference_targets:
            if target.kind == "section":
                assert target.target_id in sections.sections
            elif target.kind == "exercise":
                assert target.target_id in exercises.exercises
            else:
                assert target.retrieval_plan


def test_real_catalog_reports_source_pdf_passed_and_file_search_not_tested() -> None:
    section_report = json.loads(REAL_SECTION_REPORT.read_text(encoding="utf-8"))
    exercise_report = json.loads(REAL_EXERCISE_REPORT.read_text(encoding="utf-8"))

    assert section_report["book_id"] == "cvaa2e"
    assert section_report["page_count"] == 938
    assert section_report["section_count"] == 593
    assert section_report["printed_section_count"] == 351
    assert section_report["learning_unit_count"] == 242
    assert section_report["source_pdf_verification_status"] == "passed"
    assert section_report["file_search_retrieval_status"] == "not_tested"
    assert section_report["structural_validation_status"] == "passed"

    assert exercise_report["book_id"] == "cvaa2e"
    assert exercise_report["exercise_count"] == 172
    assert exercise_report["exercise_chapter_count"] == 13
    assert exercise_report["exercise_reference_count"] == 217
    assert exercise_report["cross_page_exercise_count"] == 26
    assert exercise_report["source_pdf_verification_status"] == "passed"
    assert exercise_report["file_search_retrieval_status"] == "not_tested"
    assert exercise_report["structural_validation_status"] == "passed"
    assert all(not chapter["missing_numbers"] for chapter in exercise_report["chapter_reports"].values())


def test_real_catalog_has_only_balanced_nfkc_queries() -> None:
    sections = LocatorRepository.load(REAL_INDEX).index
    exercises = ExerciseRepository.load(REAL_EXERCISE_INDEX).index

    section_queries = [
        query
        for locator in sections.sections.values()
        for step in locator.retrieval_plan
        for query in step.queries
    ]
    exercise_queries = [
        query
        for locator in exercises.exercises.values()
        for plan in [locator.problem_retrieval_plan, locator.reference_retrieval_plan]
        for step in plan
        for query in step.queries
    ]

    all_queries = [*section_queries, *exercise_queries]
    assert all_queries
    assert all(query_parentheses_balanced(query) for query in all_queries)
    assert all(unicodedata.normalize("NFKC", query) == query for query in all_queries)
