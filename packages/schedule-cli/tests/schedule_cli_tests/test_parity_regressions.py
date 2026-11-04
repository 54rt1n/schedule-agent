"""Behavioral regressions for the package/parity review."""

import json
import subprocess
import sys
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from schedule_core import calendar_service as service
from schedule_core.contracts import EventCreateRequest, EventPatchRequest
from schedule_core.exporting import ExportRequest, export_calendar
from schedule_core.interchange import ImportRequest, apply_import, plan_import
from schedule_core.items import ItemRequest, item_agenda, item_get, item_list, item_write
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def at(day, hour=9):
    return datetime(2030, 1, day, hour, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["point", "task"])
async def test_org_roundtrip_keeps_item_instants(db, tmp_path, kind):
    item = await item_write(
        db,
        ItemRequest(
            kind=kind,
            title="Local appointment",
            start=at(7, 15),
            deadline=at(8, 15) if kind == "task" else None,
            time_zone="America/Chicago",
        ),
    )
    path = tmp_path / "export.org"
    path.write_text((await export_calendar(db, ExportRequest(format="org"))).content)
    plan = await plan_import(db, ImportRequest(source=str(path)))
    assert plan.records[0].item.start == item.start
    assert plan.records[0].item.deadline == item.deadline
    await apply_import(db, plan)
    assert (await item_get(db, item.id)).start == item.start


@pytest.mark.asyncio
@pytest.mark.parametrize(("policy", "action"), [("error", "error"), ("skip", "skip")])
async def test_non_timed_exclusions_are_not_silently_lost(db, tmp_path, policy, action):
    path = tmp_path / "excluded.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:excluded\n"
        "SUMMARY:All day\nDTSTART;VALUE=DATE:20300107\n"
        "RRULE:FREQ=DAILY;COUNT=3\nEXDATE;VALUE=DATE:20300108\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )
    plan = await plan_import(
        db,
        ImportRequest(
            source=str(path),
            format="ics",
            on_unsupported=policy,
        ),
    )
    assert plan.records[0].action == action
    assert "exception" in plan.records[0].reason.lower()
    if policy == "error":
        with pytest.raises(ValueError, match="Import has errors"):
            await apply_import(db, plan)
    else:
        await apply_import(db, plan)
    assert await item_list(db) == []


@pytest.mark.asyncio
async def test_org_roundtrip_keeps_imported_task_rule(db, tmp_path):
    item = await item_write(
        db,
        ItemRequest(
            kind="task",
            title="Recurring",
            start=date(2030, 1, 7),
            rrule="FREQ=DAILY;COUNT=3",
        ),
    )
    path = tmp_path / "export.org"
    path.write_text((await export_calendar(db, ExportRequest(format="org"))).content)
    plan = await plan_import(db, ImportRequest(source=str(path)))
    assert plan.records[0].item.rrule == item.rrule
    await apply_import(db, plan)
    assert len(item_agenda(await item_get(db, item.id), at(7, 0), at(10, 0))) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", ["+1d", "++1d", ".+1d"])
async def test_ics_roundtrip_keeps_completion_repeater_semantics(db, tmp_path, repeat):
    item = await item_write(
        db,
        ItemRequest(
            kind="task",
            title="Daily action",
            start=date(2030, 1, 7),
            repeat=repeat,
        ),
    )
    path = tmp_path / "export.ics"
    path.write_text((await export_calendar(db, ExportRequest(format="ics"))).content)
    plan = await plan_import(db, ImportRequest(source=str(path), format="ics"))
    assert plan.records[0].action == "unchanged", plan.records[0]
    await apply_import(db, plan)
    restored = await item_get(db, item.id)
    assert restored.repeat == repeat
    assert restored.rrule is None
    assert len(item_agenda(restored, at(7, 0), at(10, 0))) == 1


@pytest.mark.asyncio
async def test_series_edits_respect_cancelled_and_moved_instances(db):
    series = await service.create_event(
        db,
        EventCreateRequest(
            title="Daily",
            start_time=at(7),
            end_time=at(7, 10),
            rrule="FREQ=DAILY;COUNT=3",
        ),
    )
    await service.delete_event(db, series.id, scope="occurrence", occurrence=at(8))
    await service.update_event(
        db,
        series.id,
        EventPatchRequest(
            start_time=at(10),
            end_time=at(10, 10),
        ),
        scope="occurrence",
        occurrence=at(9),
    )
    for day in [8, 9]:
        await service.create_event(
            db,
            EventCreateRequest(
                title="Freed slot",
                start_time=at(day),
                end_time=at(day, 10),
            ),
        )
    updated = await service.update_event(db, series.id, EventPatchRequest(notes="Edited"))
    assert updated.notes == "Edited"
    # A real conflict on a surviving occurrence must still reject the series edit.
    await service.create_event(
        db,
        EventCreateRequest(
            title="Later booking",
            start_time=at(7, 10),
            end_time=at(7, 11),
        ),
    )
    with pytest.raises(service.ApplicationError, match="conflict"):
        await service.update_event(db, series.id, EventPatchRequest(end_time=at(7, 11)))


def test_cli_slots_filter_calendar(tmp_path):
    def cli(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "schedule_cli",
                "--data-dir",
                str(tmp_path),
                "--json",
                *args,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout
        return json.loads(result.stdout)

    cli("init")
    cli(
        "events",
        "create",
        "--data",
        json.dumps(
            {
                "title": "Personal",
                "calendar_id": "personal",
                "start_time": at(7).isoformat(),
                "end_time": at(7, 10).isoformat(),
            }
        ),
    )
    args = [
        "availability",
        "slots",
        "--start",
        at(7).isoformat(),
        "--end",
        at(7, 10).isoformat(),
        "--duration",
        "1h",
    ]
    assert cli(*args)["count"] == 0
    assert cli(*args, "--calendar", "work")["count"] == 1
