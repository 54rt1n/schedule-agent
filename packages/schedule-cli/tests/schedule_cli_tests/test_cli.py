"""Behavior contracts through real command processes and temporary SQLite state."""

import json
import subprocess
import sys


def cli(data, *args, ok=True):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "schedule_cli",
            "--data-dir",
            str(data),
            "--json",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == (0 if ok else 1), (result.stdout, result.stderr)
    assert payload["success"] is ok
    return payload


def test_event_flags_for_timed_point_and_all_day(tmp_path):
    cli(tmp_path, "init", "--timezone", "America/Chicago")
    event = cli(
        tmp_path,
        "events",
        "create",
        "--title",
        "Planning",
        "--start",
        "2030-01-07T10:00",
        "--end",
        "2030-01-07T11:00",
        "--location",
        "Office",
        "--participant",
        "alex@example.com",
    )
    uid = event["event_id"]
    assert event["event"]["start_time"] == "2030-01-07T16:00:00+00:00"
    updated = cli(
        tmp_path,
        "events",
        "update",
        uid,
        "--start",
        "2030-01-07T12:00",
        "--end",
        "2030-01-07T13:00",
        "--clear",
        "location",
        "--clear",
        "participants",
    )
    assert updated["event"]["location"] is None
    assert updated["event"]["participant_identifiers"] == []
    assert updated["event"]["title"] == "Planning"
    participants = cli(
        tmp_path,
        "events",
        "participants",
        "set",
        uid,
        "--participant",
        "alex@example.com",
        "--participant",
        "pat@example.com",
    )
    assert participants["identifiers"] == ["alex@example.com", "pat@example.com"]
    conflicts = cli(
        tmp_path,
        "conflicts",
        "check",
        "--title",
        "Overlap",
        "--start",
        "2030-01-07T12:00",
        "--end",
        "2030-01-07T13:00",
    )
    assert conflicts["count"] == 1
    for kind, start in [
        ("point", "2030-01-08T09:00"),
        ("all_day", "2030-01-09"),
        ("anniversary", "2000-01-10"),
    ]:
        created = cli(
            tmp_path, "events", "create", "--kind", kind, "--title", kind, "--start", start
        )
        changed = cli(
            tmp_path,
            "events",
            "update",
            created["event_id"],
            "--start",
            start,
            "--notes",
            "Updated",
        )
        assert changed["event"]["kind"] == kind
        assert changed["event"]["notes"] == "Updated"


def test_task_contact_and_calendar_flags(tmp_path):
    cli(tmp_path, "init")
    cal = cli(tmp_path, "calendars", "create", "--id", "work", "--name", "Work")
    assert cal["calendar"]["id"] == "work"
    cli(tmp_path, "calendars", "update", "work", "--name", "Work calendar")
    task = cli(
        tmp_path,
        "tasks",
        "create",
        "--title",
        "Proposal",
        "--scheduled",
        "2030-01-07",
        "--deadline",
        "2030-01-08",
        "--calendar",
        "work",
        "--repeat",
        "+1w",
    )
    updated = cli(
        tmp_path, "tasks", "update", task["task_id"], "--state", "BLOCKED", "--clear", "deadline"
    )
    assert updated["task"]["deadline"] is None
    assert updated["task"]["start"] == "2030-01-07"
    contact = cli(
        tmp_path,
        "contacts",
        "create",
        "--name",
        "Alex",
        "--email",
        "alex@example.com",
        "--phone",
        "123",
    )
    updated = cli(
        tmp_path,
        "contacts",
        "update",
        contact["contact_id"],
        "--clear",
        "email",
        "--notes",
        "Met at work",
    )
    assert updated["contact"]["email"] is None
    assert updated["contact"]["phone"] == "123"


def test_cli_rejects_ambiguous_flag_payloads(tmp_path):
    cli(tmp_path, "init")
    for arguments in [
        ["tasks", "create", "--title", "Flag", "--data", '{"title":"JSON"}'],
        ["tasks", "create"],
        [
            "contacts",
            "update",
            "00000000-0000-0000-0000-000000000001",
            "--email",
            "alex@example.com",
            "--clear",
            "email",
        ],
    ]:
        assert cli(tmp_path, *arguments, ok=False)["problem"]["code"] == "validation"


def test_events_conflict_recurrence_and_delete(tmp_path):
    data = tmp_path / "calendar"
    cli(data, "init", "--timezone", "America/Chicago")
    event = {
        "title": "Meeting",
        "start_time": "2030-01-07T10:00:00-06:00",
        "end_time": "2030-01-07T11:00:00-06:00",
    }
    created = cli(data, "events", "create", "--data", json.dumps(event))
    conflict = cli(
        data,
        "events",
        "create",
        "--data",
        json.dumps({**event, "title": "Overlap"}),
        ok=False,
    )
    assert conflict["details"]["conflicts"]
    eid = created["event_id"]
    cli(data, "events", "update", eid, "--data", '{"title":"Renamed"}')
    query = cli(
        data,
        "events",
        "list",
        "--start",
        "2030-01-07T00:00:00Z",
        "--end",
        "2030-01-08T00:00:00Z",
    )
    assert len(query["events"]) == 1
    assert query["events"][0]["title"] == "Renamed"
    assert query["events"][0]["start_time"].endswith("+00:00")
    cli(data, "events", "delete", eid)
    repeat = {**event, "title": "Daily", "rrule": "FREQ=DAILY;COUNT=3"}
    cli(data, "events", "create", "--data", json.dumps(repeat))
    query = cli(
        data,
        "events",
        "list",
        "--start",
        "2030-01-07T00:00:00Z",
        "--end",
        "2030-01-11T00:00:00Z",
    )
    assert len(query["events"]) == 3


def test_contacts_persist_and_isolate(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    cli(a, "init")
    cli(b, "init")
    created = cli(a, "contacts", "create", "--data", '{"name":"Alex","email":"alex@example.com"}')
    cid = created["contact_id"]
    cli(a, "contacts", "update", cid, "--data", '{"phone":"555-0101"}')
    assert (
        cli(a, "contacts", "list", "--email", "example.com")["contacts"][0]["phone"] == "555-0101"
    )
    assert not cli(a, "contacts", "list", "--email", "absent.example")["contacts"]
    assert not cli(b, "contacts", "list")["contacts"]
    cli(a, "contacts", "delete", cid)
    assert not cli(a, "contacts", "list")["contacts"]


def test_org_round_trip_and_preview(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    cli(a, "init")
    cli(b, "init")
    event = {
        "title": "Night meeting",
        "start_time": "2030-01-07T23:00:00-06:00",
        "end_time": "2030-01-08T01:00:00-06:00",
        "time_zone": "America/Chicago",
        "notes": "Bring notes -- and questions",
        "rrule": "FREQ=WEEKLY;COUNT=3",
        "participant_identifiers": ["alex@example.com"],
    }
    original = cli(a, "events", "create", "--data", json.dumps(event))
    export = tmp_path / "calendar.org"
    cli(a, "export", "--output", str(export))
    assert ">--<" in export.read_text()
    preview = cli(b, "import", str(export))
    assert not preview["applied"]
    assert not cli(
        b,
        "events",
        "list",
        "--start",
        "2030-01-01T00:00:00Z",
        "--end",
        "2030-02-01T00:00:00Z",
    )["events"]
    cli(b, "import", str(export), "--apply")
    second = cli(b, "import", str(export), "--apply")
    assert second["created"] == []
    assert second["unchanged"] == [original["event_id"]]
    assert cli(b, "export")["content"] == export.read_text()
    ics = cli(b, "export", "--format", "ics")["content"]
    assert "BEGIN:VCALENDAR" in ics and "RRULE:FREQ=WEEKLY;COUNT=3" in ics


def test_invalid_import_is_atomic_and_missing_state_fails(tmp_path):
    data = tmp_path / "calendar"
    cli(data, "status", ok=False)
    assert not data.exists()
    cli(data, "init")
    source = tmp_path / "input.org"
    source.write_text("* Good\n<2030-01-07 Mon 10:00-11:00 +1w>\n* Unsupported\n<2030-99-99 Tue>\n")
    cli(data, "import", str(source), "--apply", ok=False)
    assert not cli(
        data,
        "events",
        "list",
        "--start",
        "2030-01-01T00:00:00Z",
        "--end",
        "2030-02-01T00:00:00Z",
    )["events"]


def test_import_open_ended_weekly_series(tmp_path):
    data = tmp_path / "calendar"
    cli(data, "init", "--timezone", "America/Chicago")
    source = tmp_path / "weekly.org"
    source.write_text("* GPD weekly meeting\n<2026-11-10 Tue 14:00-15:00 +1w>\n")
    cli(data, "import", str(source), "--apply")
    result = cli(
        data,
        "events",
        "list",
        "--start",
        "2026-11-02T00:00:00-06:00",
        "--end",
        "2026-11-18T00:00:00-06:00",
    )
    assert [event["start_time"] for event in result["events"]] == [
        "2026-11-10T20:00:00+00:00",
        "2026-11-17T20:00:00+00:00",
    ]
