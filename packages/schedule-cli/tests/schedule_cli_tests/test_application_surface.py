"""Behavioral contracts for the full command application, using real child processes."""

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
        timeout=25,
    )
    body = json.loads(result.stdout)
    assert (result.returncode == 0) == ok, (result.stdout, result.stderr)
    return body


def create(
    data, title="A", start="2030-01-07T10:00:00Z", end="2030-01-07T11:00:00Z", **extra
):
    return cli(
        data,
        "events",
        "create",
        "--data",
        json.dumps(dict(title=title, start_time=start, end_time=end, **extra)),
    )


def test_strict_validation_patch_and_move_conflicts(tmp_path):
    cli(tmp_path, "init")
    a = create(tmp_path, location="Room")["event_id"]
    b = create(tmp_path, "B", "2030-01-07T12:00:00Z", "2030-01-07T13:00:00Z")[
        "event_id"
    ]
    for fields in [
        {"titel": "Typo"},
        {"title": None},
        {"start_time": "2030-01-07T11:00:00Z", "end_time": "2030-01-07T10:00:00Z"},
        {"rrule": "garbage"},
    ]:
        reply = cli(
            tmp_path, "events", "update", a, "--data", json.dumps(fields), ok=False
        )
        assert reply["problem"]["code"] == "validation"
    conflict = cli(
        tmp_path,
        "events",
        "update",
        b,
        "--data",
        '{"start_time":"2030-01-07T10:15:00Z","end_time":"2030-01-07T10:45:00Z"}',
        ok=False,
    )
    assert conflict["problem"]["code"] == "conflict"
    cli(tmp_path, "events", "update", a, "--data", '{"location":null}')
    assert cli(tmp_path, "events", "get", a)["event"]["location"] is None
    for extra in [{"rrule": "garbage"}, {"end_time": "2030-01-07T09:00:00Z"}]:
        values = dict(
            title="Invalid",
            start_time="2030-01-07T10:00:00Z",
            end_time="2030-01-07T11:00:00Z",
        )
        values.update(extra)
        cli(tmp_path, "events", "create", "--data", json.dumps(values), ok=False)


def test_occurrence_identity_move_restore_and_future(tmp_path):
    cli(tmp_path, "init")
    eid = create(tmp_path, rrule="FREQ=WEEKLY;COUNT=4")["event_id"]
    cli(
        tmp_path,
        "events",
        "update",
        eid,
        "--scope",
        "occurrence",
        "--occurrence",
        "2030-01-14T10:00:00Z",
        "--data",
        '{"title":"Moved","start_time":"2030-01-15T12:00:00Z","end_time":"2030-01-15T13:00:00Z"}',
    )
    result = cli(
        tmp_path,
        "events",
        "list",
        "--start",
        "2030-01-15T00:00:00Z",
        "--end",
        "2030-01-16T00:00:00Z",
    )
    assert result["count"] == 1 and result["events"][0]["id"] == eid
    assert (
        result["events"][0]["occurrence"]["original_start"]
        == "2030-01-14T10:00:00+00:00"
    )
    cli(
        tmp_path,
        "events",
        "exceptions",
        "restore",
        eid,
        "--occurrence",
        "2030-01-14T10:00:00Z",
    )
    cli(
        tmp_path,
        "events",
        "delete",
        eid,
        "--scope",
        "occurrence",
        "--occurrence",
        "2030-01-14T10:00:00Z",
    )
    assert cli(tmp_path, "events", "exceptions", "list", eid)["excluded"]
    cli(
        tmp_path,
        "events",
        "update",
        eid,
        "--scope",
        "future",
        "--occurrence",
        "2030-01-21T10:00:00Z",
        "--data",
        '{"title":"Later"}',
    )
    result = cli(
        tmp_path,
        "events",
        "list",
        "--start",
        "2030-01-01T00:00:00Z",
        "--end",
        "2030-02-15T00:00:00Z",
    )
    assert [r["title"] for r in result["events"]] == ["A", "Later", "Later"]


def test_mixed_org_plan_staleness_and_item_semantics(tmp_path):
    data = tmp_path / "data"
    cli(data, "init", "--timezone", "America/Chicago")
    source = tmp_path / "source.org"
    source.write_text(
        "* Meeting\n<2030-01-07 Mon 10:00-11:00>\n* Birthday\n<2030-01-08 Tue +1y>\n* Appointment\n<2030-01-09 Wed 15:00>\n* TODO Habit\nSCHEDULED: <2030-01-07 Mon ++1d>\n* <2030-01-11 Fri 19:00-24:00 ++1w>\n"
    )
    plan = tmp_path / "plan.json"
    preview = cli(data, "import", str(source), "--plan", str(plan))
    assert preview["count"] == 5
    create(data, "Changed", "2031-01-07T10:00:00Z", "2031-01-07T11:00:00Z")
    stale = cli(data, "apply-plan", str(plan), ok=False)
    assert stale["problem"]["code"] == "stale_plan"
    imported = cli(data, "import", str(source), "--apply")
    assert imported["count"] == 5
    assert cli(data, "import", str(source))["created"] == []
    task = cli(data, "tasks", "list")["tasks"][0]
    complete = cli(
        data, "tasks", "complete", task["id"], "--at", "2030-01-10T12:00:00-06:00"
    )["task"]
    assert complete["state"] == "TODO" and complete["start"] == "2030-01-11"
    result = cli(data, "agenda", "--date", "2030-01-09")
    assert any(i["kind"] == "point" for i in result["events"])
    assert cli(data, "sources", "list")["sources"]


def test_exceptions_export_reimport_and_backup(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    cli(a, "init")
    cli(b, "init")
    eid = create(a, rrule="FREQ=DAILY;COUNT=3")["event_id"]
    cli(
        a,
        "events",
        "delete",
        eid,
        "--scope",
        "occurrence",
        "--occurrence",
        "2030-01-08T10:00:00Z",
    )
    cli(
        a,
        "events",
        "update",
        eid,
        "--scope",
        "occurrence",
        "--occurrence",
        "2030-01-09T10:00:00Z",
        "--data",
        '{"title":"Exception"}',
    )
    for format in ["org", "ics"]:
        file = tmp_path / ("calendar." + format)
        cli(a, "export", "--format", format, "--output", str(file))
        cli(b, "import", str(file), "--format", format, "--apply")
        result = cli(
            b,
            "events",
            "list",
            "--start",
            "2030-01-07T00:00:00Z",
            "--end",
            "2030-01-11T00:00:00Z",
        )
        assert [r["title"] for r in result["events"]] == ["A", "Exception"]
    backup = tmp_path / "backup"
    cli(a, "backup", "create", str(backup))
    cli(a, "events", "delete", eid)
    cli(a, "backup", "restore", str(backup), "--apply")
    assert cli(a, "events", "get", eid)["event"]["rrule"] == "FREQ=DAILY;COUNT=3"


def test_discovery_config_and_contact_interchange(tmp_path):
    data = tmp_path / "data"
    cli(data, "init")
    schema = cli(data, "schema", "events.create")["schemas"]["events.create"]["request"]
    assert schema["additionalProperties"] is False
    cli(data, "config", "set", "--timezone", "America/Chicago")
    assert (
        cli(data, "config", "show")["config"]["default_timezone"] == "America/Chicago"
    )
    created = cli(
        data,
        "contacts",
        "create",
        "--data",
        '{"name":"Alex","email":"alex@example.com"}',
    )
    cli(data, "contacts", "update", created["contact_id"], "--data", '{"email":null}')
    assert (
        cli(data, "contacts", "get", created["contact_id"])["contact"]["email"] is None
    )
    file = tmp_path / "contacts.csv"
    cli(data, "contacts", "export", "--format", "csv", "--output", str(file))
    cli(data, "contacts", "import", str(file), "--format", "csv", "--apply")
    (data / "config.json").write_text("[]")
    bad = cli(data, "status", ok=False)
    assert bad["problem"]["code"] == "validation"


def test_participants_calendars_and_availability(tmp_path):
    cli(tmp_path, "init")
    cli(tmp_path, "calendars", "create", "--data", '{"id":"work","name":"Work"}')
    eid = create(tmp_path, calendar_id="work")["event_id"]
    cli(
        tmp_path,
        "events",
        "participants",
        "set",
        eid,
        "--data",
        '{"identifiers":["alex@example.com"]}',
    )
    assert cli(tmp_path, "events", "get", eid)["event"]["participant_identifiers"] == [
        "alex@example.com"
    ]
    slots = cli(
        tmp_path,
        "availability",
        "slots",
        "--start",
        "2030-01-07T09:00:00Z",
        "--end",
        "2030-01-07T12:00:00Z",
        "--duration",
        "1h",
    )["slots"]
    assert len(slots) == 2
    cli(tmp_path, "calendars", "delete", "work", ok=False)
    assert cli(tmp_path, "doctor")["integrity"] == "ok"


def test_source_identity_survives_time_edit_and_same_series_conflict(tmp_path):
    data = tmp_path / "calendar"
    cli(data, "init")
    source = tmp_path / "source.org"
    source.write_text("* Meeting\n<2030-01-07 Mon 10:00-11:00>\n")
    first = cli(data, "import", str(source), "--apply")
    source.write_text("* Meeting\n<2030-01-07 Mon 12:00-13:00>\n")
    second = cli(data, "import", str(source), "--apply")
    assert second["created"] == [] and second["updated"] == first["created"]
    eid = create(
        data,
        "Recurring",
        "2030-02-01T10:00:00Z",
        "2030-02-01T11:00:00Z",
        rrule="FREQ=DAILY;COUNT=3",
    )["event_id"]
    reply = cli(
        data,
        "events",
        "update",
        eid,
        "--scope",
        "occurrence",
        "--occurrence",
        "2030-02-01T10:00:00Z",
        "--data",
        '{"start_time":"2030-02-02T10:00:00Z","end_time":"2030-02-02T11:00:00Z"}',
        ok=False,
    )
    assert reply["problem"]["code"] == "conflict"
