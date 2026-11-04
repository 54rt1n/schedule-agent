"""Release contracts for recurrence and loss-aware source imports."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from schedule_core import calendar_service as service
from schedule_core.contracts import EventCreateRequest
from schedule_core.exporting import ExportRequest, export_calendar
from schedule_core.interchange import ImportRequest, apply_import, plan_import
from schedule_core.items import ItemRequest, complete, item_agenda, item_get, item_write
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


def calendar(path, components):
    path.write_text("BEGIN:VCALENDAR\nVERSION:2.0\n" + components + "END:VCALENDAR\n")
    return ImportRequest(source=str(path), format="ics")


def event(uid="fixture", extra="", title="Appointment"):
    return (
        "BEGIN:VEVENT\n" + (f"UID:{uid}\n" if uid else "") + f"SUMMARY:{title}\n"
        "DTSTART:20300107T090000Z\nDTEND:20300107T100000Z\n" + extra + "END:VEVENT\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["task", "point"])
async def test_named_zone_survives_persistence_and_dst(db, kind):
    item = await item_write(
        db,
        ItemRequest(
            kind=kind,
            title="9AM daily",
            start=datetime.fromisoformat("2030-03-09T09:00:00-06:00"),
            time_zone="America/Chicago",
            repeat="+1d",
        ),
    )
    await db.commit()
    db.expire_all()
    if kind == "task":
        item = await complete(db, item.id, datetime.fromisoformat("2030-03-09T10:00:00-06:00"))
        next_time = item.start
    else:
        item = await item_get(db, item.id)
        rows = item_agenda(
            item, datetime(2030, 3, 10, tzinfo=UTC), datetime(2030, 3, 11, tzinfo=UTC)
        )
        next_time = datetime.fromisoformat(rows[0]["occurrence_start"])
    assert next_time.astimezone(ZoneInfo("America/Chicago")).hour == 9
    assert next_time.astimezone(UTC).hour == 14


@pytest.mark.asyncio
async def test_org_description_remains_data(db, tmp_path):
    description = "Reference <2035-02-03 Sat> and %%(org-anniversary 2000 1 1) Example"
    original = await service.create_event(
        db,
        EventCreateRequest(
            title="Meeting",
            start_time=datetime(2030, 1, 7, 9, tzinfo=UTC),
            end_time=datetime(2030, 1, 7, 10, tzinfo=UTC),
            description=description,
        ),
    )
    path = tmp_path / "calendar.org"
    path.write_text((await export_calendar(db, ExportRequest(format="org"))).content)
    plan = await plan_import(db, ImportRequest(source=str(path)))
    assert len(plan.records) == 1
    assert plan.records[0].action == "unchanged"
    assert plan.records[0].event.description == description
    assert plan.records[0].event.start_time == original.start_time


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("components", "reason", "count"),
    [
        (event(extra="STATUS:CANCELLED\n"), "cancelled", 1),
        (event(extra="RDATE:20300109T090000Z\n"), "rdate", 1),
        (event(extra="RECURRENCE-ID:20300107T090000Z\n"), "base", 1),
        (event(title="First") + event(title="Second"), "duplicate", 2),
        (event(uid="", title="First") + event(uid="", title="Second"), "uid", 2),
    ],
)
async def test_ics_unsupported_inputs_have_diagnostics(db, tmp_path, components, reason, count):
    request = calendar(tmp_path / "calendar.ics", components)
    plan = await plan_import(db, request)
    assert len(plan.records) == count
    assert all(r.action == "error" and reason in r.reason.lower() for r in plan.records)
    with pytest.raises(ValueError, match="Import has errors"):
        await apply_import(db, plan)
    skipped = await plan_import(db, request.model_copy(update={"on_unsupported": "skip"}))
    assert len(skipped.records) == count
    assert all(r.action == "skip" for r in skipped.records)
    await apply_import(db, skipped)
    assert await service.list_events(db, definitions=True) == []


@pytest.mark.asyncio
async def test_reject_policy_checks_effective_imported_series(db, tmp_path):
    await service.create_event(
        db,
        EventCreateRequest(
            title="Busy",
            start_time=datetime(2030, 1, 8, 12, tzinfo=UTC),
            end_time=datetime(2030, 1, 8, 13, tzinfo=UTC),
        ),
    )
    await db.commit()
    moved = (
        event(extra="RECURRENCE-ID:20300108T090000Z\n", title="Moved")
        .replace("DTSTART:20300107T090000Z", "DTSTART:20300108T120000Z")
        .replace("DTEND:20300107T100000Z", "DTEND:20300108T130000Z")
    )
    request = calendar(tmp_path / "calendar.ics", event(extra="RRULE:FREQ=DAILY;COUNT=2\n") + moved)
    plan = await plan_import(db, request.model_copy(update={"conflicts": "reject"}))
    with pytest.raises(service.ApplicationError, match="conflict"):
        await apply_import(db, plan)
    await db.rollback()
    assert [e.title for e in await service.list_events(db, definitions=True)] == ["Busy"]
    # Preserve explicitly allows the same imported overlap.
    plan = await plan_import(db, request)
    await apply_import(db, plan)
    rows = await service.list_events(
        db, datetime(2030, 1, 8, 12, tzinfo=UTC), datetime(2030, 1, 8, 13, tzinfo=UTC)
    )
    assert {e.title for e in rows} == {"Busy", "Moved"}


@pytest.mark.asyncio
async def test_reject_policy_allows_booking_in_cancelled_import_occurrence(db, tmp_path):
    await service.create_event(
        db,
        EventCreateRequest(
            title="Busy",
            start_time=datetime(2030, 1, 8, 9, tzinfo=UTC),
            end_time=datetime(2030, 1, 8, 10, tzinfo=UTC),
        ),
    )
    cancelled = (
        "BEGIN:VEVENT\nUID:fixture\nRECURRENCE-ID:20300108T090000Z\nSTATUS:CANCELLED\nEND:VEVENT\n"
    )
    request = calendar(
        tmp_path / "calendar.ics", event(extra="RRULE:FREQ=DAILY;COUNT=2\n") + cancelled
    )
    plan = await plan_import(db, request.model_copy(update={"conflicts": "reject"}))
    await apply_import(db, plan)
    rows = await service.list_events(
        db, datetime(2030, 1, 8, tzinfo=UTC), datetime(2030, 1, 9, tzinfo=UTC)
    )
    assert [e.title for e in rows] == ["Busy"]
