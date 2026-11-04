"""Preserve scheduling meaning across resource queries and native formats."""

from datetime import date, datetime

import pytest
import pytest_asyncio
from schedule_core.commands import dispatch
from schedule_core.core.settings import Settings
from schedule_core.db.service import DatabaseService
from schedule_core.exporting import ExportRequest, export_calendar
from schedule_core.interchange import ImportRequest, apply_import, plan_import


@pytest.mark.asyncio
async def test_recurring_timed_gap_is_rejected_before_write(database):
    with pytest.raises(ValueError, match="nonexistent"):
        await dispatch(
            database,
            "events.create",
            {
                "event": {
                    "kind": "timed",
                    "title": "Gap",
                    "time_zone": "America/Chicago",
                    "start_time": "2030-03-03T02:30:00",
                    "end_time": "2030-03-03T03:30:00",
                    "rrule": "FREQ=WEEKLY;COUNT=3",
                }
            },
        )
    assert (await dispatch(database, "schedule.query", {})).count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,recurrence",
    [
        ("timed", "rrule"),
        ("point", "rrule"),
        ("point", "repeat"),
    ],
)
async def test_imported_recurrence_gap_reports_error(database, tmp_path, kind, recurrence):
    path = tmp_path / "gap.org"
    stamp = "<2030-03-03 Sun 02:30" + ("-03:30" if kind == "timed" else "")
    stamp += " +1w>" if recurrence == "repeat" else ">"
    rule = ":RRULE: FREQ=WEEKLY;COUNT=3\n" if recurrence == "rrule" else ""
    path.write_text("* Gap\n:PROPERTIES:\n:TIMEZONE: America/Chicago\n" + rule + ":END:\n" + stamp)
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path)))
        await apply_import(session, plan)
        await session.commit()
    with pytest.raises(ValueError, match="nonexistent"):
        await dispatch(
            database,
            "schedule.query",
            {"view": "occurrences", "start": "2030-03-10T00:00:00Z", "end": "2030-03-11T00:00:00Z"},
        )
    assert (
        await dispatch(database, "schedule.query", {"view": "occurrences", "date": "2030-03-17"})
    ).count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("format_name", ["org", "ics"])
async def test_timed_snapshots_own_repeated_exports(database, tmp_path, format_name):
    from schedule_core import calendar_service as service

    source = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "timed",
                "title": "Source",
                "start_time": "2030-01-07T12:00:00Z",
                "end_time": "2030-01-07T13:00:00Z",
                "rrule": "FREQ=DAILY;COUNT=2",
            }
        },
    )
    request = ExportRequest(
        format=format_name,
        mode="occurrences",
        start="2030-01-07T00:00:00Z",
        end="2030-01-09T00:00:00Z",
    )
    path = tmp_path / ("snapshot." + format_name)
    snapshot_id = None
    for iteration in range(3):
        async with database.get_session() as session:
            exported = await export_calendar(session, request)
            assert exported.count == 2
            path.write_text(exported.content)
            plan = await plan_import(session, ImportRequest(source=str(path), format=format_name))
            assert len(plan.records) == 2
            assert all(r.action in {"create", "unchanged"} for r in plan.records)
            if snapshot_id is None:
                snapshot_id = plan.records[0].id
            assert snapshot_id != source.event_id
            if iteration:
                assert next(r for r in plan.records if r.id == snapshot_id).event.title == "Edited"
            await apply_import(session, plan)
            if iteration == 0:
                row = await service.get_event(session, snapshot_id)
                row.title = "Edited"
            await session.commit()
    async with database.get_session() as session:
        alternate_format = "ics" if format_name == "org" else "org"
        alternate = await export_calendar(
            session, request.model_copy(update={"format": alternate_format})
        )
        assert alternate.count == 2
        alternate_path = tmp_path / ("alternate." + alternate_format)
        alternate_path.write_text(alternate.content)
        alternate_plan = await plan_import(
            session, ImportRequest(source=str(alternate_path), format=alternate_format)
        )
        assert all(r.action == "unchanged" for r in alternate_plan.records)
        row = await service.get_event(session, snapshot_id)
        row.start_time = service.stored(datetime.fromisoformat("2030-01-10T12:00:00Z"))
        row.end_time = service.stored(datetime.fromisoformat("2030-01-10T13:00:00Z"))
        await session.commit()
        assert (await export_calendar(session, request)).count == 1
        row.start_time = service.stored(datetime.fromisoformat("2030-01-07T12:00:00Z"))
        row.end_time = service.stored(datetime.fromisoformat("2030-01-07T13:00:00Z"))
        row.calendar_id = "other"
        await session.commit()
        # Stored identity also suppresses regeneration when the calendar filter hides it.
        filtered = request.model_copy(update={"calendar": "primary"})
        assert (await export_calendar(session, filtered)).count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", ["exdate", "cancelled", "modified"])
async def test_floating_ics_exceptions_use_series_timezone(database, tmp_path, exception):
    base_exdate = "EXDATE:20300310T090000\n" if exception == "exdate" else ""
    override = ""
    if exception != "exdate":
        fields = (
            "STATUS:CANCELLED\n"
            if exception == "cancelled"
            else "DTSTART:20300310T110000\nDTEND:20300310T120000\nSUMMARY:Moved\n"
        )
        override = (
            "BEGIN:VEVENT\nUID:floating\nRECURRENCE-ID:20300310T090000\n" + fields + "END:VEVENT\n"
        )
    path = tmp_path / "floating.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:floating\nSUMMARY:Daily\n"
        "DTSTART:20300309T090000\nDTEND:20300309T100000\nRRULE:FREQ=DAILY;COUNT=3\n"
        + base_exdate
        + "END:VEVENT\n"
        + override
        + "END:VCALENDAR\n"
    )
    async with database.get_session() as session:
        plan = await plan_import(
            session, ImportRequest(source=str(path), format="ics", timezone="America/Chicago")
        )
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(
        database,
        "schedule.query",
        {"view": "occurrences", "start": "2030-03-09T00:00:00Z", "end": "2030-03-12T00:00:00Z"},
    )
    expected = ["2030-03-09T15:00:00+00:00", "2030-03-11T14:00:00+00:00"]
    if exception == "modified":
        expected.insert(1, "2030-03-10T16:00:00+00:00")
        assert result.events[1].occurrence.original_start.isoformat() == "2030-03-10T14:00:00+00:00"
    assert [event.start_time.isoformat() for event in result.events] == expected


@pytest.mark.asyncio
async def test_org_modified_notes_remain_data_on_roundtrip(database, tmp_path):
    created = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "timed",
                "title": "Daily",
                "start_time": "2030-02-01T12:00:00Z",
                "end_time": "2030-02-01T13:00:00Z",
                "rrule": "FREQ=DAILY;COUNT=3",
            }
        },
    )
    await dispatch(
        database,
        "events.update",
        {
            "id": str(created.event_id),
            "scope": "occurrence",
            "occurrence": "2030-02-02T12:00:00Z",
            "patch": {"notes": "SCHEDULED: <2030-02-09 Sat 14:00>"},
        },
    )
    query = {"view": "occurrences", "start": "2030-02-01T00:00:00Z", "end": "2030-02-04T00:00:00Z"}
    before = await dispatch(database, "schedule.query", query)
    async with database.get_session() as session:
        exported = await export_calendar(session, ExportRequest(format="org"))
        path = tmp_path / "modified.org"
        path.write_text(exported.content)
        plan = await plan_import(session, ImportRequest(source=str(path)))
        assert plan.records[0].action == "update"
        await apply_import(session, plan)
        await session.commit()
    after = await dispatch(database, "schedule.query", query)
    assert [(e.start_time, e.notes, e.occurrence) for e in after.events] == [
        (e.start_time, e.notes, e.occurrence) for e in before.events
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,anchor,repeat,rrule",
    [
        ("all_day", "2030-01-31", "+1m", None),
        ("all_day", "2030-01-31", "+1m", "FREQ=MONTHLY;COUNT=3"),
        ("anniversary", "2028-02-29", None, None),
    ],
)
async def test_ics_roundtrip_preserves_repeater_occurrences(
    database, tmp_path, kind, anchor, repeat, rrule
):
    await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": kind,
                "title": "Recurring",
                "start": anchor,
                "repeat": repeat,
                "rrule": rrule,
            }
        },
    )
    query = {
        "resource": "events",
        "view": "occurrences",
        "start": "2030-01-01T00:00:00Z",
        "end": "2030-05-01T00:00:00Z",
    }
    before = await dispatch(database, "schedule.query", query)
    async with database.get_session() as session:
        exported = await export_calendar(session, ExportRequest(format="ics"))
        path = tmp_path / "monthly.ics"
        path.write_text(exported.content)
        plan = await plan_import(session, ImportRequest(source=str(path), format="ics"))
        assert plan.records[0].item.rrule == rrule
        await apply_import(session, plan)
        await session.commit()
    after = await dispatch(database, "schedule.query", query)
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["error", "skip"])
@pytest.mark.parametrize("deadline_repeat", ["+1m", "", "+1w"])
async def test_org_rejects_incompatible_task_repeaters(database, tmp_path, policy, deadline_repeat):
    path = tmp_path / "tasks.org"
    path.write_text(
        "* TODO Different repeats\nSCHEDULED: <2030-01-01 Tue +1w> "
        f"DEADLINE: <2030-01-02 Wed {deadline_repeat}>\n".replace(" >", ">")
    )
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path), on_unsupported=policy))
        assert len(plan.records) == 1
        if deadline_repeat == "+1w":
            assert plan.records[0].action == "create"
            assert plan.records[0].item.repeat == "+1w"
            return
        assert plan.records[0].action == policy
        assert "repeaters" in plan.records[0].reason


@pytest_asyncio.fixture
async def database(tmp_path):
    db = DatabaseService.from_settings(
        Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path}/calendar.db",
        )
    )
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_org_notes_cannot_change_event_kind_on_reimport(database, tmp_path):
    notes = "SCHEDULED: <2030-01-09 Wed 14:00>"
    original = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "timed",
                "title": "Appointment",
                "notes": notes,
                "start_time": "2030-01-07T12:00:00Z",
                "end_time": "2030-01-07T13:00:00Z",
            }
        },
    )
    async with database.get_session() as session:
        exported = await export_calendar(session, ExportRequest(format="org"))
        path = tmp_path / "calendar.org"
        path.write_text(exported.content)
        plan = await plan_import(session, ImportRequest(source=str(path)))
        assert plan.records[0].event is not None
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(database, "schedule.query", {"id": str(original.event_id)})
    assert result.tasks == []
    assert result.events[0].start_time == original.event.start_time
    assert result.events[0].notes == notes


@pytest.mark.asyncio
@pytest.mark.parametrize("placement", ["heading", "body"])
async def test_org_task_active_timestamp_remains_on_agenda(database, tmp_path, placement):
    stamp = "<2030-01-07 Mon 14:00>"
    text = (
        f"* TODO Call Alex {stamp}\n" if placement == "heading" else f"* TODO Call Alex\n{stamp}\n"
    )
    path = tmp_path / "tasks.org"
    path.write_text(text)
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path)))
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(
        database,
        "schedule.query",
        {
            "resource": "tasks",
            "view": "occurrences",
            "date": "2030-01-07",
        },
    )
    assert len(result.tasks) == 1
    assert result.tasks[0].start == datetime.fromisoformat("2030-01-07T14:00:00Z")


@pytest.mark.asyncio
async def test_ics_task_duration_sets_deadline(database, tmp_path):
    path = tmp_path / "tasks.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VTODO\nUID:duration-task\n"
        "SUMMARY:Due in an hour\nDTSTART:20300107T120000Z\nDURATION:PT1H\n"
        "END:VTODO\nEND:VCALENDAR\n"
    )
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path), format="ics"))
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(database, "schedule.query", {"resource": "tasks"})
    assert result.tasks[0].deadline == datetime.fromisoformat("2030-01-07T13:00:00Z")


@pytest.mark.asyncio
async def test_participant_query_excludes_unrelated_tasks(database):
    await dispatch(database, "tasks.create", {"title": "Unrelated task"})
    event = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "timed",
                "title": "With Alex",
                "participant_identifiers": ["alex@example.com"],
                "start_time": "2030-01-07T12:00:00Z",
                "end_time": "2030-01-07T13:00:00Z",
            }
        },
    )
    result = await dispatch(database, "schedule.query", {"participant": "alex@example.com"})
    assert result.tasks == []
    assert [e.id for e in result.events] == [event.event_id]


@pytest.mark.asyncio
async def test_midnight_task_inputs_preserve_instant_and_date_only_values(database):
    created = await dispatch(
        database,
        "tasks.create",
        {
            "title": "Midnight UTC",
            "start": "2030-01-07T00:00:00Z",
            "deadline": "2030-01-08",
            "time_zone": "America/Chicago",
        },
    )
    assert created.task.start == datetime.fromisoformat("2030-01-07T00:00:00Z")
    assert type(created.task.deadline) is date
    updated = await dispatch(
        database,
        "tasks.update",
        {
            "id": str(created.task_id),
            "patch": {"start": "2030-01-08T00:00:00Z"},
        },
    )
    result = await dispatch(database, "schedule.query", {"id": str(created.task_id)})
    assert (
        result.tasks[0].start
        == updated.task.start
        == datetime.fromisoformat("2030-01-08T00:00:00Z")
    )
    assert result.tasks[0].deadline == date(2030, 1, 8)


@pytest.mark.asyncio
async def test_ambiguous_occurrence_delete_leaves_series_intact(database):
    created = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "timed",
                "title": "Daily",
                "start_time": "2030-01-07T09:00:00Z",
                "end_time": "2030-01-07T10:00:00Z",
                "rrule": "FREQ=DAILY;COUNT=3",
            }
        },
    )
    with pytest.raises(ValueError, match="scope"):
        await dispatch(
            database,
            "events.delete",
            {
                "id": created.event_id,
                "occurrence": "2030-01-08T09:00:00Z",
            },
        )
    result = await dispatch(database, "schedule.query", {"id": created.event_id})
    assert result.count == 1
    await dispatch(
        database,
        "events.delete",
        {
            "id": created.event_id,
            "scope": "occurrence",
            "occurrence": "2030-01-08T09:00:00Z",
        },
    )
    assert (
        await dispatch(database, "schedule.query", {"view": "occurrences", "date": "2030-01-08"})
    ).count == 0
    assert (
        await dispatch(database, "schedule.query", {"view": "occurrences", "date": "2030-01-09"})
    ).count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,action", [("error", "error"), ("skip", "skip")])
async def test_ics_future_range_override_is_explicitly_unsupported(
    database, tmp_path, policy, action
):
    path = tmp_path / "future.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:series\nSUMMARY:Original\n"
        "DTSTART:20300107T090000Z\nDTEND:20300107T100000Z\nRRULE:FREQ=DAILY;COUNT=3\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:series\nRECURRENCE-ID;RANGE=THISANDFUTURE:20300108T090000Z\n"
        "SUMMARY:Moved\nDTSTART:20300108T110000Z\nDTEND:20300108T120000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    async with database.get_session() as session:
        plan = await plan_import(
            session, ImportRequest(source=str(path), format="ics", on_unsupported=policy)
        )
        assert [r.action for r in plan.records] == [action]
        assert "RANGE" in plan.records[0].reason


@pytest.mark.asyncio
async def test_dst_gap_completion_is_atomic(database):
    created = await dispatch(
        database,
        "tasks.create",
        {
            "title": "02:30 daily",
            "start": "2030-03-09T02:30:00-06:00",
            "time_zone": "America/Chicago",
            "repeat": "+1d",
        },
    )
    with pytest.raises(ValueError, match="nonexistent"):
        await dispatch(
            database,
            "tasks.complete",
            {
                "id": created.task_id,
                "completed_at": "2030-03-09T03:00:00-06:00",
            },
        )
    current = (await dispatch(database, "schedule.query", {"id": created.task_id})).tasks[0]
    assert current.start == created.task.start
    assert current.completed_at is None
    assert current.state == "TODO"


@pytest.mark.asyncio
async def test_generated_ics_preserves_calendar_and_description(database, tmp_path):
    event = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "timed",
                "title": "Work",
                "start_time": "2030-01-07T09:00:00Z",
                "end_time": "2030-01-07T10:00:00Z",
                "calendar_id": "work",
                "description": "Detailed description",
                "notes": "Separate notes",
            }
        },
    )
    task = await dispatch(database, "tasks.create", {"title": "Work task", "calendar_id": "work"})
    async with database.get_session() as session:
        exported = await export_calendar(session, ExportRequest(format="ics"))
        path = tmp_path / "roundtrip.ics"
        path.write_text(exported.content)
        plan = await plan_import(session, ImportRequest(source=str(path), format="ics"))
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(database, "schedule.query", {"id": event.event_id})
    assert result.events[0].calendar_id == "work"
    assert result.events[0].description == "Detailed description"
    assert result.events[0].notes == "Separate notes"
    assert (await dispatch(database, "schedule.query", {"id": task.task_id})).tasks[
        0
    ].calendar_id == "work"


@pytest.mark.asyncio
async def test_generated_org_preserves_yearly_all_day_range(database, tmp_path):
    original = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "all_day",
                "title": "Holiday",
                "start": "2030-02-01",
                "end": "2030-02-04",
                "repeat": "+1y",
            }
        },
    )
    async with database.get_session() as session:
        exported = await export_calendar(session, ExportRequest(format="org"))
        path = tmp_path / "holiday.org"
        path.write_text(exported.content)
        plan = await plan_import(session, ImportRequest(source=str(path)))
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(database, "schedule.query", {"id": original.event_id})
    assert result.events[0].kind == "all_day"
    assert result.events[0].end == date(2030, 2, 4)
    assert "<2030-02-03" in exported.content


@pytest.mark.asyncio
async def test_native_org_date_range_includes_last_day(database, tmp_path):
    path = tmp_path / "conference.org"
    path.write_text("* Conference\n<2030-04-01 Mon>--<2030-04-03 Wed>\n")
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path)))
        await apply_import(session, plan)
        await session.commit()
    assert (
        await dispatch(database, "schedule.query", {"view": "occurrences", "date": "2030-04-03"})
    ).count == 1
    assert (
        await dispatch(database, "schedule.query", {"view": "occurrences", "date": "2030-04-04"})
    ).count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "<2030-01-07 Mon>\n<2030-01-09 Wed>",
        "<2030-01-07 Mon 10:00-11:00>--<2030-01-09 Wed 10:00-11:00>",
        "SCHEDULED: <2030-01-07 Mon>\n<2030-01-09 Wed>",
    ],
)
@pytest.mark.parametrize("policy,action", [("error", "error"), ("skip", "skip")])
async def test_unrepresentable_org_timestamps_are_diagnosed(
    database, tmp_path, body, policy, action
):
    path = tmp_path / "multiple.org"
    path.write_text("* Commitment\n" + body + "\n")
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path), on_unsupported=policy))
        assert [record.action for record in plan.records] == [action]
        assert plan.records[0].reason


@pytest.mark.asyncio
async def test_org_heading_timestamp_edit_updates_existing_identity(database, tmp_path):
    path = tmp_path / "heading.org"
    ids = []
    for day in (7, 8):
        path.write_text(f"* Appointment <2030-01-{day:02} Mon 09:00-10:00>\n")
        async with database.get_session() as session:
            plan = await plan_import(session, ImportRequest(source=str(path)))
            ids.append(plan.records[0].id)
            await apply_import(session, plan)
            await session.commit()
    assert ids[0] == ids[1]
    result = await dispatch(database, "schedule.query", {})
    assert result.count == 1
    assert result.events[0].start_time.day == 8
    path.write_text(
        "* Appointment <2030-01-08 Tue 09:00-10:00>\n* Appointment <2030-01-09 Wed 09:00-10:00>\n"
    )
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path)))
        assert any(r.action == "error" and "identity" in r.reason.lower() for r in plan.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,action", [("error", "error"), ("skip", "skip")])
async def test_duplicate_ics_override_identity_is_diagnosed(database, tmp_path, policy, action):
    path = tmp_path / "duplicates.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:series\n"
        "DTSTART:20300107T090000Z\nDTEND:20300107T100000Z\nRRULE:FREQ=DAILY;COUNT=3\nSUMMARY:Base\nEND:VEVENT\n"
        + "".join(
            f"BEGIN:VEVENT\nUID:series\nRECURRENCE-ID:20300108T090000Z\nDTSTART:20300108T{hour}0000Z\nDTEND:20300108T{hour}3000Z\nSUMMARY:Override\nEND:VEVENT\n"
            for hour in ("11", "12")
        )
        + "END:VCALENDAR\n"
    )
    async with database.get_session() as session:
        plan = await plan_import(
            session, ImportRequest(source=str(path), format="ics", on_unsupported=policy)
        )
        assert [r.action for r in plan.records] == [action]
        assert "duplicate" in plan.records[0].reason.lower()


@pytest.mark.asyncio
async def test_due_only_ics_preserves_named_zone_for_repeat(database, tmp_path):
    path = tmp_path / "due.ics"
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VTODO\nUID:due-only\nSUMMARY:Daily due\nDUE;TZID=America/Chicago:20300309T090000\nX-ORG-REPEAT:+1d\nEND:VTODO\nEND:VCALENDAR\n"
    )
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path), format="ics"))
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(
        database,
        "tasks.complete",
        {
            "id": plan.records[0].id,
            "completed_at": "2030-03-09T10:00:00-06:00",
        },
    )
    assert result.task.time_zone == "America/Chicago"
    assert result.task.deadline == datetime.fromisoformat("2030-03-10T09:00:00-05:00")


@pytest.mark.asyncio
async def test_date_only_ics_roundtrip_preserves_explicit_zone(database, tmp_path):
    original = await dispatch(
        database,
        "events.create",
        {
            "event": {
                "kind": "all_day",
                "title": "Chicago date",
                "start": "2030-04-01",
                "time_zone": "America/Chicago",
            }
        },
    )
    async with database.get_session() as session:
        exported = await export_calendar(session, ExportRequest(format="ics"))
        path = tmp_path / "date.ics"
        path.write_text(exported.content)
        plan = await plan_import(
            session, ImportRequest(source=str(path), format="ics", timezone="UTC")
        )
        await apply_import(session, plan)
        await session.commit()
    result = await dispatch(database, "schedule.query", {"id": original.event_id})
    assert result.events[0].time_zone == "America/Chicago"


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_count", [1, 2])
async def test_timestamp_identity_normalization_handles_existing_registrations(
    database, tmp_path, registered_count
):
    from schedule_core.items import SourceIdentity

    path = tmp_path / "registered.org"
    identities = []
    for day in range(7, 7 + registered_count):
        created = await dispatch(
            database,
            "events.create",
            {
                "event": {
                    "kind": "timed",
                    "title": "Appointment",
                    "start_time": f"2030-01-{day:02}T09:00:00Z",
                    "end_time": f"2030-01-{day:02}T10:00:00Z",
                }
            },
        )
        identities.append(created.event_id)
        async with database.get_session() as session:
            session.add(
                SourceIdentity(
                    source_key=f"myorg:{path}:Appointment <2030-01-{day:02} Mon 09:00-10:00>",
                    record_id=created.event_id,
                    source_path=str(path),
                )
            )
            await session.commit()
    path.write_text("* Appointment <2030-01-10 Thu 09:00-10:00>\n")
    async with database.get_session() as session:
        plan = await plan_import(session, ImportRequest(source=str(path)))
        if registered_count == 1:
            assert plan.records[0].id == identities[0]
            assert plan.records[0].action == "update"
        else:
            assert plan.records[0].action == "error"
            assert "Ambiguous" in plan.records[0].reason
