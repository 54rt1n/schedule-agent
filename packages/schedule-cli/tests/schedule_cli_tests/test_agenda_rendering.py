"""Human agendas and machine output stay separate through the real CLI."""

import json
from datetime import datetime
from types import SimpleNamespace

from schedule_cli.main import main


def test_cli_agenda_uses_configured_zone_and_human_details(tmp_path, capsys):
    def run(*args):
        status = main(["--data-dir", str(tmp_path), *args])
        output = capsys.readouterr().out
        assert status == 0, output
        return output

    run("init", "--timezone", "America/Chicago")
    created = json.loads(
        run(
            "--json",
            "events",
            "create",
            "--title",
            "OpenAI Dev Day",
            "--start",
            "2030-11-07T00:00:00Z",
            "--end",
            "2030-11-07T02:30:00Z",
            "--timezone",
            "UTC",
            "--location",
            "Tech Central",
            "--notes",
            "Bring notebook",
        )
    )
    human = run("agenda", "--date", "2030-11-06", "--details")
    assert "Wednesday, November 6" in human
    assert "6–8:30 pm  OpenAI Dev Day — Tech Central" in human
    assert "Bring notebook" in human
    for internal in (
        created["event_id"],
        '"kind"',
        "null",
        "UTC",
        "America/Chicago",
        "event(s)",
        "+00:00",
    ):
        assert internal not in human
    plain = run("agenda", "--date", "2030-11-06")
    assert "Bring notebook" not in plain
    machine = json.loads(run("--json", "agenda", "--date", "2030-11-06", "--details"))
    assert machine["events"][0]["id"] == created["event_id"]
    assert machine["events"][0]["start_time"] == "2030-11-07T00:00:00+00:00"
    assert "Wednesday" not in json.dumps(machine)
    override = run("agenda", "--date", "2030-11-07", "--timezone", "UTC")
    assert "12–2:30 am  OpenAI Dev Day" in override


def test_cacheable_chronology_points_tasks_and_midnight_continuity():
    from schedule_cli.agenda_output import AgendaContext, format_agenda

    context = AgendaContext(
        start=datetime.fromisoformat("2026-11-02T00:00:00-06:00"),
        end=datetime.fromisoformat("2026-11-09T00:00:00-06:00"),
        timezone="America/Chicago",
    )

    def timed(title, start, end, **extra):
        return {
            "id": title + start,
            "title": title,
            "kind": "timed",
            "start_time": start,
            "end_time": end,
            "time_zone": "America/Chicago",
            "calendar_id": "primary",
            **extra,
        }

    result = {
        "events": [
            timed("Breakfast", "2026-11-03T08:00:00-06:00", "2026-11-03T09:00:00-06:00"),
            timed("Lunch", "2026-11-03T11:30:00-06:00", "2026-11-03T12:30:00-06:00"),
            {
                "id": "point-id",
                "kind": "point",
                "title": "Call Alex",
                "start": "2026-11-03T15:00:00-06:00",
                "time_zone": "America/Chicago",
            },
            timed("Shabbat", "2026-11-06T18:00:00-06:00", "2026-11-07T00:00:00-06:00"),
            timed("Shabbat", "2026-11-07T00:00:00-06:00", "2026-11-07T20:00:00-06:00"),
        ],
        "tasks": [
            {
                "id": "task-id",
                "title": "Send proposal",
                "kind": "task",
                "state": "BLOCKED",
                "start": "2026-11-03",
                "deadline": "2026-11-04",
                "occurrence_start": "2026-11-03T00:00:00-06:00",
                "time_zone": "America/Chicago",
            }
        ],
    }
    original = json.dumps(result)
    human = format_agenda(result, context, details=True)
    assert human.index("Breakfast") < human.index("Lunch") < human.index("3 pm  Call Alex")
    assert "Tuesday, November 3, 2026" in human
    for relative in ("Earlier today", "Now", "Remaining today", "Later today"):
        assert relative not in human
    assert "Scheduled task (blocked): Send proposal" in human
    assert "6 pm–Saturday, November 7, 8 pm  Shabbat" in human
    assert "Continues until 8 pm  Shabbat" in human
    assert "12 am" not in human
    assert "point-id" not in human and "task-id" not in human
    assert json.dumps(result) == original
    utc_context = AgendaContext(context.start, context.end, "UTC")
    utc_text = format_agenda(result, utc_context)
    assert "6 am  Shabbat" not in utc_text


def test_empty_agenda_and_human_failure(capsys):
    from schedule_cli.agenda_output import AgendaContext, format_agenda
    from schedule_cli.main import render

    context = AgendaContext(
        datetime.fromisoformat("2026-11-03T00:00:00-06:00"),
        datetime.fromisoformat("2026-11-04T00:00:00-06:00"),
        "America/Chicago",
    )
    assert "No scheduled events or tasks." in format_agenda({"events": [], "tasks": []}, context)
    render(
        {"success": False, "error": "Calendar is not initialized"},
        SimpleNamespace(command="agenda", json=False),
    )
    assert capsys.readouterr().out == "Unable to read agenda: Calendar is not initialized\n"


def test_multi_day_dates_and_mixed_task_dates():
    from schedule_cli.agenda_output import AgendaContext, format_agenda

    context = AgendaContext(
        datetime.fromisoformat("2026-11-03T00:00:00-06:00"),
        datetime.fromisoformat("2026-11-06T00:00:00-06:00"),
        "America/Chicago",
    )
    events = [
        {
            "kind": "all_day",
            "title": "Conference",
            "start": "2026-11-02",
            "end": "2026-11-05",
            "time_zone": "UTC",
            "occurrence_start": "2026-11-02T00:00:00Z",
        }
    ]
    task = {
        "kind": "task",
        "title": "Proposal",
        "state": "TODO",
        "time_zone": "America/Chicago",
        "start": "2026-11-03T09:00:00-06:00",
        "deadline": "2026-11-04",
    }
    tasks = [
        {**task, "occurrence_start": point}
        for point in ("2026-11-03T09:00:00-06:00", "2026-11-04T00:00:00-06:00")
    ]
    text = format_agenda({"events": events, "tasks": tasks}, context)
    assert text.count("All day  Conference") == 2
    assert "9 am  Scheduled task: Proposal" in text
    assert "Due task: Proposal" in text and "12 am" not in text
    assert "Thursday, November 5" not in text
    task.update(start="2026-11-03", deadline="2026-11-03T15:00:00-06:00")
    text = format_agenda(
        {"events": [], "tasks": [{**task, "occurrence_start": task["deadline"]}]}, context
    )
    assert "3 pm  Due task: Proposal" in text
    assert "Scheduled and Due" not in text
    foreign = {
        "kind": "all_day",
        "title": "Foreign date",
        "start": "2026-11-06",
        "time_zone": "UTC",
        "occurrence_start": "2026-11-06T00:00:00Z",
    }
    text = format_agenda({"events": [foreign], "tasks": []}, context)
    assert "Friday, November 6, 2026" in text and "All day  Foreign date" in text


def test_dst_and_seconds_remain_precise_and_different_calendars_do_not_join():
    from schedule_cli.agenda_output import AgendaContext, format_agenda

    context = AgendaContext(
        datetime.fromisoformat("2026-11-01T00:00:00-05:00"),
        datetime.fromisoformat("2026-11-03T00:00:00-06:00"),
        "America/Chicago",
    )

    def event(start, end, calendar="primary"):
        return {
            "kind": "timed",
            "title": "Watch",
            "start_time": start,
            "end_time": end,
            "time_zone": "America/Chicago",
            "calendar_id": calendar,
        }

    events = [
        event("2026-11-01T01:30:00-05:00", "2026-11-01T01:30:00-06:00"),
        event("2026-11-01T23:30:15-06:00", "2026-11-02T00:00:00-06:00"),
        event("2026-11-02T00:00:00-06:00", "2026-11-02T01:00:00-06:00", "work"),
    ]
    text = format_agenda({"events": events, "tasks": []}, context)
    assert "1:30 am CDT–1:30 am CST" in text
    assert "11:30:15 pm–midnight" in text
    assert "12–1 am  Watch" in text
