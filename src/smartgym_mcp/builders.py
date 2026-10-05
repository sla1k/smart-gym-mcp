"""Single-field tool inputs → a DesiredRoutine (API-client spec §6).

Each single-field tool is a thin wrapper over apply_routine: it lists only the
section(s) it touches and leaves the others as None (kept).
"""

from __future__ import annotations

from collections.abc import Mapping

from .diff import DesiredExercise, DesiredRoutine, DiffError
from .model import Routine, Section
from .models import SetSpec


def _desired(lists: Mapping[Section, list[DesiredExercise]]) -> DesiredRoutine:
    return DesiredRoutine(
        warmup=lists.get("warmup"), main=lists.get("main"), cooldown=lists.get("cooldown")
    )


def _ids(routine: Routine, section: Section) -> list[DesiredExercise]:
    return [DesiredExercise(exercise_id=e.identifier) for e in routine.section(section)]


def _section_of(routine: Routine, exercise_id: int) -> Section:
    for e in routine.active_exercises():
        if e.identifier == exercise_id:
            return e.section
    raise DiffError(f"Exercise {exercise_id} is not an active exercise of {routine.name!r}.")


def _insert(
    items: list[DesiredExercise], item: DesiredExercise, position: int | None
) -> list[DesiredExercise]:
    out = list(items)
    if position is None or position >= len(out):
        out.append(item)
    else:
        out.insert(max(position, 0), item)
    return out


def add_exercise(
    routine: Routine,
    exercise: str,
    *,
    section: Section,
    position: int | None,
    rest_seconds: int | None,
    note: str | None,
    sets: list[SetSpec] | None,
) -> DesiredRoutine:
    new = DesiredExercise(exercise=exercise, rest_seconds=rest_seconds, note=note, sets=sets)
    return _desired({section: _insert(_ids(routine, section), new, position)})


def move_exercise(
    routine: Routine, exercise_id: int, *, section: Section, position: int | None
) -> DesiredRoutine:
    source = _section_of(routine, exercise_id)
    target = [d for d in _ids(routine, section) if d.exercise_id != exercise_id]
    lists = {section: _insert(target, DesiredExercise(exercise_id=exercise_id), position)}
    if source != section:
        lists[source] = [d for d in _ids(routine, source) if d.exercise_id != exercise_id]
    return _desired(lists)


def remove_exercise(routine: Routine, exercise_id: int) -> DesiredRoutine:
    section = _section_of(routine, exercise_id)
    kept = [d for d in _ids(routine, section) if d.exercise_id != exercise_id]
    return _desired({section: kept})


def update_exercise(
    routine: Routine,
    exercise_id: int,
    *,
    rest_seconds: int | None,
    note: str | None,
    sets: list[SetSpec] | None,
) -> DesiredRoutine:
    if rest_seconds is None and note is None and sets is None:
        raise DiffError("Pass at least one of rest_seconds, note, sets.")
    section = _section_of(routine, exercise_id)
    items = [
        DesiredExercise(
            exercise_id=exercise_id, rest_seconds=rest_seconds, note=note, sets=sets
        )
        if d.exercise_id == exercise_id
        else d
        for d in _ids(routine, section)
    ]
    return _desired({section: items})


def reorder(
    routine: Routine,
    *,
    warmup: list[int] | None,
    main: list[int] | None,
    cooldown: list[int] | None,
) -> DesiredRoutine:
    given: dict[Section, list[int] | None] = {
        "warmup": warmup,
        "main": main,
        "cooldown": cooldown,
    }
    if all(v is None for v in given.values()):
        raise DiffError("Pass the new order for at least one section.")
    problems: list[str] = []
    lists: dict[Section, list[DesiredExercise]] = {}
    for section, ids in given.items():
        if ids is None:
            continue
        members = sorted(e.identifier for e in routine.section(section))
        if sorted(ids) != members:
            problems.append(
                f"{section}: must list exactly its current exercises {members} (got {ids}); "
                "use smartgym_move_exercise to change sections."
            )
        lists[section] = [DesiredExercise(exercise_id=i) for i in ids]
    if problems:
        raise DiffError("Rejected — nothing was changed:\n- " + "\n- ".join(problems))
    return _desired(lists)


def update_routine(
    *, name: str | None, days: str | None, goal: str | None, note: str | None
) -> DesiredRoutine:
    if all(v is None for v in (name, days, goal, note)):
        raise DiffError("Pass at least one of name, days, goal, note.")
    return DesiredRoutine(name=name, days=days, goal=goal, note=note)


__all__ = [
    "add_exercise",
    "move_exercise",
    "remove_exercise",
    "reorder",
    "update_exercise",
    "update_routine",
]
