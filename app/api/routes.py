from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.core.errors import (
    ExerciseCatalogUnavailableError,
    ExerciseNotFoundError,
    SectionNotFoundError,
)
from app.core.security import require_api_key
from app.models.exercise import (
    CHAPTER_ID_PATTERN,
    EXERCISE_ID_PATTERN,
    ChapterExerciseSummary,
    ExerciseLocator,
)
from app.models.locator import (
    ExerciseCatalogUnavailableResponse,
    ExerciseNotFoundResponse,
    HealthResponse,
    SectionLocator,
    SectionNotFoundResponse,
)
from app.services.locator_service import LocatorService
from skill_temple.action_logging import log_activity, new_activity_id

router = APIRouter()


def _activity_started(operation_id: str, payload: dict[str, object]) -> str:
    activity_id = new_activity_id("generic")
    log_activity(
        activity_id=activity_id,
        kind="generic",
        phase="started",
        payload=payload,
        legacy_action=operation_id,
        legacy_fields={**payload, "state": "started"},
    )
    return activity_id


def _activity_finished(
    *,
    activity_id: str,
    operation_id: str,
    payload: dict[str, object],
    failed: bool = False,
) -> None:
    log_activity(
        activity_id=activity_id,
        kind="generic",
        phase="failed" if failed else "completed",
        payload=payload,
        legacy_action=operation_id,
        legacy_fields={**payload, "state": "failed" if failed else "completed"},
    )


def locator_service(request: Request) -> LocatorService:
    return LocatorService(
        request.app.state.locator_repository,
        getattr(request.app.state, "exercise_repository", None),
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
    operation_id="healthCheck",
)
async def health(
    service: Annotated[LocatorService, Depends(locator_service)],
) -> HealthResponse:
    return service.health()


@router.get(
    "/gpt/section-locators/{section_id}",
    response_model=SectionLocator,
    responses={
        404: {"model": SectionNotFoundResponse, "description": "Section id does not exist."}
    },
    tags=["gpt"],
    dependencies=[Depends(require_api_key)],
    operation_id="gptGetSectionLocator",
)
async def get_section_locator(
    section_id: str,
    service: Annotated[LocatorService, Depends(locator_service)],
) -> SectionLocator:
    activity_id = _activity_started(
        "gptGetSectionLocator",
        {"operation": "get_section_locator", "section_id": section_id},
    )
    try:
        locator = service.get_section(section_id)
    except SectionNotFoundError:
        _activity_finished(
            activity_id=activity_id,
            operation_id="gptGetSectionLocator",
            payload={
                "operation": "get_section_locator",
                "section_id": section_id,
                "error_code": "SECTION_NOT_FOUND",
                "diagnostic": "Section not found",
            },
            failed=True,
        )
        raise
    _activity_finished(
        activity_id=activity_id,
        operation_id="gptGetSectionLocator",
        payload={
            "operation": "get_section_locator",
            "section_id": section_id,
            "title": locator.title,
            "printed_page_start": locator.page_range.printed_page_start,
            "printed_page_end": locator.page_range.printed_page_end,
        },
    )
    return locator


@router.get(
    "/gpt/exercise-locators/{exercise_id}",
    response_model=ExerciseLocator,
    responses={
        404: {
            "model": ExerciseNotFoundResponse,
            "description": "Exercise id does not exist.",
        },
        503: {
            "model": ExerciseCatalogUnavailableResponse,
            "description": "Exercise catalog has not been configured.",
        },
    },
    tags=["gpt"],
    dependencies=[Depends(require_api_key)],
    operation_id="gptGetExerciseLocator",
)
async def get_exercise_locator(
    exercise_id: Annotated[str, Path(pattern=EXERCISE_ID_PATTERN)],
    service: Annotated[LocatorService, Depends(locator_service)],
) -> ExerciseLocator:
    activity_id = _activity_started(
        "gptGetExerciseLocator",
        {"operation": "get_exercise_locator", "exercise_id": exercise_id},
    )
    try:
        locator = service.get_exercise(exercise_id)
    except ExerciseNotFoundError:
        _activity_finished(
            activity_id=activity_id,
            operation_id="gptGetExerciseLocator",
            payload={
                "operation": "get_exercise_locator",
                "exercise_id": exercise_id,
                "error_code": "EXERCISE_NOT_FOUND",
                "diagnostic": "Exercise not found",
            },
            failed=True,
        )
        raise
    except ExerciseCatalogUnavailableError:
        _activity_finished(
            activity_id=activity_id,
            operation_id="gptGetExerciseLocator",
            payload={
                "operation": "get_exercise_locator",
                "exercise_id": exercise_id,
                "error_code": "EXERCISE_CATALOG_UNAVAILABLE",
                "diagnostic": "Exercise catalog unavailable",
            },
            failed=True,
        )
        raise
    _activity_finished(
        activity_id=activity_id,
        operation_id="gptGetExerciseLocator",
        payload={
            "operation": "get_exercise_locator",
            "exercise_id": exercise_id,
            "chapter_id": locator.chapter_id,
            "printed_page_start": locator.problem_page_range.printed_page_start,
            "printed_page_end": locator.problem_page_range.printed_page_end,
            "reference_count": len(locator.reference_targets),
        },
    )
    return locator


@router.get(
    "/gpt/chapters/{chapter_id}/exercises",
    response_model=ChapterExerciseSummary,
    responses={
        404: {
            "model": ExerciseNotFoundResponse,
            "description": "Chapter has no exercise catalog entry.",
        },
        503: {
            "model": ExerciseCatalogUnavailableResponse,
            "description": "Exercise catalog has not been configured.",
        },
    },
    tags=["gpt"],
    dependencies=[Depends(require_api_key)],
    operation_id="gptListChapterExercises",
)
async def list_chapter_exercises(
    chapter_id: Annotated[str, Path(pattern=CHAPTER_ID_PATTERN)],
    service: Annotated[LocatorService, Depends(locator_service)],
) -> ChapterExerciseSummary:
    activity_id = _activity_started(
        "gptListChapterExercises",
        {"operation": "list_chapter_exercises", "chapter_id": chapter_id},
    )
    try:
        summary = service.list_chapter_exercises(chapter_id)
    except ExerciseNotFoundError:
        _activity_finished(
            activity_id=activity_id,
            operation_id="gptListChapterExercises",
            payload={
                "operation": "list_chapter_exercises",
                "chapter_id": chapter_id,
                "error_code": "EXERCISE_NOT_FOUND",
                "diagnostic": "Chapter exercises not found",
            },
            failed=True,
        )
        raise
    except ExerciseCatalogUnavailableError:
        _activity_finished(
            activity_id=activity_id,
            operation_id="gptListChapterExercises",
            payload={
                "operation": "list_chapter_exercises",
                "chapter_id": chapter_id,
                "error_code": "EXERCISE_CATALOG_UNAVAILABLE",
                "diagnostic": "Exercise catalog unavailable",
            },
            failed=True,
        )
        raise
    _activity_finished(
        activity_id=activity_id,
        operation_id="gptListChapterExercises",
        payload={
            "operation": "list_chapter_exercises",
            "chapter_id": chapter_id,
            "exercise_count": summary.exercise_count,
            "first_exercise": summary.first_exercise,
            "last_exercise": summary.last_exercise,
        },
    )
    return summary
