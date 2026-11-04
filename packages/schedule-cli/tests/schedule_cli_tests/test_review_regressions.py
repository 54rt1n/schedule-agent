"""Regression contracts for recurrence and loss-aware calendar interchange."""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from schedule_core import calendar_service as service
from schedule_core.contracts import EventCreateRequest, EventPatchRequest
from schedule_core.exporting import ExportRequest, export_calendar
from schedule_core.interchange import ImportRequest, apply_import, parse_ics_file, plan_import
from schedule_core.items import (
    ItemRequest,
    SourceRecord,
    complete,
    item_agenda,
    item_get,
    item_list,
    item_write,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel


def at(day, hour=10):
    return datetime(2030, 1, day, hour, tzinfo=UTC)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def event(db, **changes):
    values = dict(title="Daily", start_time=at(2), end_time=at(2, 11), rrule="FREQ=DAILY;COUNT=5")
    return await service.create_event(db, EventCreateRequest(**(values | changes)))


async def fresh(db):
    await db.commit()
    db.expire_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("split", [2, 4])
async def test_split_preserves_cancellations_and_moves(db, split):
    base = await event(db)
    await service.delete_event(db, base.id, scope="occurrence", occurrence=at(5))
    await service.update_event(
        db,
        base.id,
        EventPatchRequest(title="Moved", start_time=at(8), end_time=at(8, 11)),
        scope="occurrence",
        occurrence=at(6),
    )
    await fresh(db)
    # Filling a cancelled slot must not make an otherwise valid split conflict.
    await event(db, title="Other", start_time=at(5), end_time=at(5, 11), rrule=None)
    await fresh(db)
    replacement = await service.update_event(
        db, base.id, EventPatchRequest(title="Later"), scope="future", occurrence=at(split)
    )
    await fresh(db)
    rows = await service.list_events(db, at(2, 0), at(10, 0))
    assert [(r.start_time.day, r.title) for r in rows] == [
        (2, "Later" if split == 2 else "Daily"),
        (3, "Later" if split == 2 else "Daily"),
        (4, "Later"),
        (5, "Other"),
        (8, "Moved"),
    ]
    assert next(r for r in rows if r.title == "Moved").occurrence.series_id == replacement.id


@pytest.mark.asyncio
async def test_split_rejects_ambiguous_exception_remapping(db):
    base = await event(db)
    await service.delete_event(db, base.id, scope="occurrence", occurrence=at(5))
    await fresh(db)
    with pytest.raises(ValueError, match="exception"):
        await service.update_event(
            db, base.id, EventPatchRequest(rrule="FREQ=WEEKLY"), scope="future", occurrence=at(4)
        )
    assert (await service.get_event(db, base.id)).rrule == "FREQ=DAILY;COUNT=5"


@pytest.mark.asyncio
async def test_occurrence_filters_and_unsupported_description(db):
    base = await event(db)
    with pytest.raises(ValueError, match="[Dd]escription|description"):
        await service.update_event(
            db,
            base.id,
            EventPatchRequest(description="Not persisted"),
            scope="occurrence",
            occurrence=at(4),
        )
    await service.update_event(
        db,
        base.id,
        EventPatchRequest(title="Special", location="Room B"),
        scope="occurrence",
        occurrence=at(4),
    )
    await fresh(db)
    rows = await service.list_events(db, at(2, 0), at(8, 0), title="Special", location="Room B")
    assert [r.start_time.day for r in rows] == [4]
    rows = await service.list_events(db, at(2, 0), at(8, 0), title="Daily")
    assert [r.start_time.day for r in rows] == [2, 3, 5, 6]


@pytest.mark.asyncio
async def test_restore_checks_conflicts_before_removing_exception(db):
    base = await event(db)
    await service.delete_event(db, base.id, scope="occurrence", occurrence=at(5))
    await fresh(db)
    other = await event(db, title="Other", start_time=at(5), end_time=at(5, 11), rrule=None)
    await fresh(db)
    with pytest.raises(service.ApplicationError, match="conflict"):
        await service.restore_occurrence(db, base.id, at(5))
    assert len((await service.get_event(db, base.id)).excluded_dates) == 1
    await service.delete_event(db, other.id)
    await fresh(db)
    await service.restore_occurrence(db, base.id, at(5))
    await fresh(db)
    assert len(await service.list_events(db, at(5), at(5, 11))) == 1


@pytest.mark.asyncio
async def test_import_kind_changes_keep_one_identity(db, tmp_path):
    path = tmp_path / "calendar.org"
    uid = "e8449d2a-6061-4805-9ff8-84920f321a40"
    for index, stamp in enumerate(["10:00", "10:00-11:00", "10:00"]):
        path.write_text(f"* Meeting\n:PROPERTIES:\n:ID: {uid}\n:END:\n<2030-01-02 Wed {stamp}>\n")
        plan = await plan_import(db, ImportRequest(source=str(path)))
        assert plan.records[0].action == ("create" if index == 0 else "update")
        await apply_import(db, plan)
        await fresh(db)
        events = await service.list_events(db, definitions=True)
        items = await item_list(db)
        assert len(events) + len(items) == 1
        assert (events + items)[0].id == UUID(uid)


def test_ics_duration_import(tmp_path):
    path = tmp_path / "calendar.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:duration\nDTSTART:20300102T100000Z\nDURATION:PT1H\nSUMMARY:Meeting\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    record = list(parse_ics_file(path, "UTC"))[0][3]
    assert isinstance(record, EventCreateRequest)
    assert record.end_time == at(2, 11)


@pytest.mark.asyncio
async def test_flattened_org_reimports_with_unique_stable_ids(db, tmp_path):
    await event(db)
    request = ExportRequest(format="org", mode="occurrences", start=at(2, 0), end=at(8, 0))
    output = await export_calendar(db, request)
    assert output.content == (await export_calendar(db, request)).content
    path = tmp_path / "export.org"
    path.write_text(output.content)
    plan = await plan_import(db, ImportRequest(source=str(path)))
    assert len(plan.records) == 5
    assert all(r.action == "create" for r in plan.records)
    assert len({r.id for r in plan.records}) == 5


@pytest.mark.asyncio
async def test_deadline_and_completion_survive_ordinary_edits(db):
    item = await item_write(
        db,
        ItemRequest(kind="task", title="Submit", start=date(2030, 1, 2), deadline=date(2030, 1, 6)),
    )
    assert len(item_agenda(item, at(6, 0), at(7, 0))) == 1
    done = await complete(db, item.id, at=at(6))
    await item_write(
        db,
        ItemRequest.model_validate(
            done.model_dump(exclude={"id", "completed_at"}) | {"title": "Submitted"}
        ),
        item.id,
    )
    assert (await item_get(db, item.id)).completed_at == at(6)
    assert not item_agenda(await item_get(db, item.id), at(6, 0), at(7, 0))


@pytest.mark.asyncio
async def test_source_reconcile_independent_of_working_directory(db, tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "calendar.org").write_text("* Meeting\n<2030-01-02 Wed 10:00-11:00>\n")
    monkeypatch.chdir(source)
    plan = await plan_import(db, ImportRequest(source="calendar.org"))
    # A saved plan must also work after changing directories before applying.
    monkeypatch.chdir(tmp_path)
    await apply_import(db, plan)
    await fresh(db)
    from sqlalchemy import select

    row = (await db.execute(select(SourceRecord))).scalar_one()
    assert row.path == str(source / "calendar.org")
    request = ImportRequest.model_validate_json(row.options)
    assert (await plan_import(db, request)).records[0].action == "unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["org", "ics", "json"])
async def test_occurrence_exports_flatten_items(db, format):
    await item_write(
        db,
        ItemRequest(
            kind="all_day",
            title="Daily",
            start=date(2030, 1, 2),
            end=date(2030, 1, 3),
            rrule="FREQ=DAILY",
        ),
    )
    await item_write(db, ItemRequest(kind="anniversary", title="Birthday", start=date(2000, 1, 4)))
    output = await export_calendar(
        db, ExportRequest(format=format, mode="occurrences", start=at(3, 0), end=at(5, 0))
    )
    assert output.count == 3
    assert "FREQ=" not in output.content and "+1y" not in output.content
    if format == "ics":
        from icalendar import Calendar

        entries = Calendar.from_ical(output.content).walk("VEVENT")
        assert sorted(c.decoded("DTSTART") for c in entries) == [
            date(2030, 1, 3),
            date(2030, 1, 4),
            date(2030, 1, 4),
        ]
        assert len({str(c["UID"]) for c in entries}) == 3


@pytest.mark.asyncio
async def test_since_keeps_recurring_ics_items(db, tmp_path):
    path = tmp_path / "calendar.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:annual\nDTSTART;VALUE=DATE:20000104\nRRULE:FREQ=YEARLY\nSUMMARY:Annual\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    plan = await plan_import(
        db, ImportRequest(source=str(path), format="ics", since=date(2030, 1, 1))
    )
    assert plan.records[0].action == "create"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", [True, False])
async def test_org_item_notes_roundtrip_without_extra_headings(db, tmp_path, scheduled):
    notes = "Notes\n* TODO Follow up\n<2030-01-08 Tue>\n:PROPERTIES:\n:END:"
    await item_write(
        db,
        ItemRequest(
            kind="task",
            title="Original",
            notes=notes,
            start=date(2030, 1, 2) if scheduled else None,
        ),
    )
    output = await export_calendar(db, ExportRequest(format="org"))
    path = tmp_path / "export.org"
    path.write_text(output.content)
    plan = await plan_import(db, ImportRequest(source=str(path)))
    assert len(plan.records) == 1
    assert plan.records[0].action == "unchanged"
    assert plan.records[0].item.notes == notes


def test_cli_failed_future_split_rolls_back(tmp_path):
    import json
    import subprocess
    import sys

    def cli(*args, ok=True):
        run = subprocess.run(
            [sys.executable, "-m", "schedule_cli", "--data-dir", str(tmp_path), "--json", *args],
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert (run.returncode == 0) == ok, (run.stdout, run.stderr)
        return json.loads(run.stdout)

    cli("init")
    base = cli(
        "events",
        "create",
        "--data",
        json.dumps(
            dict(
                title="Series",
                start_time=at(2).isoformat(),
                end_time=at(2, 11).isoformat(),
                rrule="FREQ=DAILY;COUNT=5",
            )
        ),
    )["event_id"]
    cli(
        "events",
        "create",
        "--data",
        json.dumps(
            dict(
                title="Other",
                start_time=at(4, 12).isoformat(),
                end_time=at(4, 13).isoformat(),
            )
        ),
    )
    result = cli(
        "events",
        "update",
        base,
        "--scope",
        "future",
        "--occurrence",
        at(4).isoformat(),
        "--data",
        json.dumps(dict(start_time=at(4, 12).isoformat(), end_time=at(4, 13).isoformat())),
        ok=False,
    )
    assert result["problem"]["code"] == "conflict"
    rows = cli("events", "list", "--definitions")["events"]
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == base)["rrule"] == "FREQ=DAILY;COUNT=5"


@pytest.mark.asyncio
@pytest.mark.parametrize("format_name", ["org", "ics"])
async def test_repeating_task_snapshot_does_not_replace_source(db, tmp_path, format_name):
    task = await item_write(
        db, ItemRequest(kind="task", title="Weekly", start=date(2030, 1, 2), repeat=".+1w")
    )
    request = ExportRequest(format=format_name, mode="occurrences", start=at(2, 0), end=at(3, 0))
    output = await export_calendar(db, request)
    path = tmp_path / ("snapshot." + format_name)
    path.write_text(output.content)
    plan = await plan_import(db, ImportRequest(source=str(path), format=format_name))
    assert plan.records[0].action == "create"
    assert plan.records[0].id != task.id
    assert plan.records[0].item.repeat is None
    # Exporting the same source action again must retain its snapshot identity.
    second = await export_calendar(db, request)
    assert str(plan.records[0].id) in second.content
    await apply_import(db, plan)
    assert (await item_get(db, task.id)).repeat == ".+1w"


@pytest.mark.parametrize(
    ("stamp", "duration", "hours"),
    [
        ("20300310T013000", "PT2H", 2),
        ("20301103T003000", "PT2H", 2),
        ("20300309T120000", "P1D", 23),
        ("20300309T120000", "PT24H", 24),
        ("20300309T120000", "P1DT2H", 25),
    ],
)
def test_ics_duration_preserves_nominal_days_and_elapsed_hours(tmp_path, stamp, duration, hours):
    path = tmp_path / "duration.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:duration\n"
        f"DTSTART;TZID=America/Chicago:{stamp}\nDURATION:{duration}\n"
        "SUMMARY:DST\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    record = next(parse_ics_file(path, "UTC"))[3]
    assert isinstance(record, EventCreateRequest), record
    assert (record.end_time - record.start_time).total_seconds() == hours * 3600


def test_task_agenda_deduplicates_equivalent_offsets():
    from schedule_core.items import ItemDetail
    from uuid import uuid4

    item = ItemDetail(
        id=uuid4(),
        kind="task",
        title="Same instant",
        start="2030-01-02T10:00:00-06:00",
        deadline="2030-01-02T16:00:00Z",
    )
    rows = item_agenda(item, at(2, 0), at(3, 0))
    assert len(rows) == 1
    assert datetime.fromisoformat(rows[0]["occurrence_start"]) == at(2, 16)


def test_ics_duration_keeps_component_and_alarm_boundaries(tmp_path):
    path = tmp_path / "mixed.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\n"
        "BEGIN:VTODO\nUID:task\nSUMMARY:Task\nEND:VTODO\n"
        "BEGIN:VEVENT\nUID:timed\nSUMMARY:Timed\n"
        "DTSTART;TZID=America/Chicago:20300310T013000\nDURATION:PT\n 2H\n"
        "BEGIN:VALARM\nACTION:DISPLAY\nTRIGGER:-PT15M\nDURATION:PT15M\n"
        "REPEAT:2\nDESCRIPTION:Alarm\nEND:VALARM\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:day\nSUMMARY:Day\nDTSTART;VALUE=DATE:20300310\n"
        "DURATION:P1D\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    records = {record[1]: record[3] for record in parse_ics_file(path, "UTC")}
    assert records["Task"].kind == "task"
    assert (records["Timed"].end_time - records["Timed"].start_time).total_seconds() == 7200
    assert records["Day"].end == date(2030, 3, 11)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["error", "skip"])
async def test_undated_task_bad_notes_follow_import_policy(db, tmp_path, policy):
    path = tmp_path / "tasks.org"
    path.write_text(
        "* TODO Bad\n:PROPERTIES:\n:NOTES_JSON: not json\n:END:\n"
        "* TODO Good\nSCHEDULED: <2030-01-02 Wed>\n"
    )
    plan = await plan_import(db, ImportRequest(source=str(path), on_unsupported=policy))
    assert [record.action for record in plan.records] == [policy, "create"]
    assert plan.records[0].reason
    if policy == "skip":
        await apply_import(db, plan)
        assert [item.title for item in await item_list(db)] == ["Good"]
    else:
        with pytest.raises(ValueError, match="Import has errors"):
            await apply_import(db, plan)
        assert await item_list(db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("format_name", ["org", "ics"])
@pytest.mark.parametrize("kind", ["task", "all_day"])
async def test_snapshot_roundtrips_keep_one_identity_and_stored_edits(
    db, tmp_path, format_name, kind
):
    source = await item_write(
        db,
        ItemRequest(
            kind=kind,
            title="Source",
            start=date(2030, 1, 2),
            repeat=".+1w" if kind == "task" else "+1w",
        ),
    )
    request = ExportRequest(format=format_name, mode="occurrences", start=at(2, 0), end=at(3, 0))
    path = tmp_path / ("snapshot." + format_name)
    snapshot_id = None
    for iteration in range(3):
        output = await export_calendar(db, request)
        assert output.count == 1
        path.write_text(output.content)
        plan = await plan_import(db, ImportRequest(source=str(path), format=format_name))
        assert len(plan.records) == 1
        record = plan.records[0]
        assert record.action == ("create" if iteration == 0 else "unchanged")
        if snapshot_id is None:
            snapshot_id = record.id
        assert record.id == snapshot_id
        assert record.id != source.id
        assert record.item.title == ("Source" if iteration == 0 else "Edited snapshot")
        await apply_import(db, plan)
        if iteration == 0:
            snapshot = await item_get(db, snapshot_id)
            await item_write(
                db,
                ItemRequest.model_validate(
                    snapshot.model_dump(exclude={"id", "completed_at"})
                    | {"title": "Edited snapshot"}
                ),
                snapshot_id,
            )
        await fresh(db)
    assert len(await item_list(db)) == 2
    assert (await item_get(db, source.id)).repeat == source.repeat
    if kind == "task":
        await complete(db, snapshot_id, at=at(2))
        assert (await export_calendar(db, request)).count == 0
        await complete(db, snapshot_id, reopen=True)
        assert (await export_calendar(db, request)).count == 1
    # A moved snapshot owns its original generated identity even outside this export range.
    snapshot = await item_get(db, snapshot_id)
    await item_write(
        db,
        ItemRequest.model_validate(
            snapshot.model_dump(exclude={"id", "completed_at"}) | {"start": date(2030, 1, 4)}
        ),
        snapshot_id,
    )
    assert (await export_calendar(db, request)).count == 0
