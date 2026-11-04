# Schedule Agent

Manage events, tasks, and contacts from the terminal or through an AI agent.
Read your agenda, find available time, and import or export Org and iCalendar files.
Data is stored locally in SQLite. The CLI needs no background service or model API key.

[Install](#install) · [Quickstart](#quickstart) · [Usage](#usage) ·
[Org/ICS import and export](#import-and-export-org-or-ics-files) ·
[Commands](#commands) · [Agent integration](#agent-integration) ·
[How it works](#how-it-works) · [Development](#development) · [License](#license)

## Features

- **Events.** Create meetings with a start and end time, appointments with only a
  start time, all-day events, and anniversaries. Assign a calendar, location, and
  notes. Timed events can also have participants.
- **Tasks and deadlines.** Set scheduled dates and deadlines, mark tasks blocked
  or complete, and reopen them. Completing a task with an Org repeater advances
  its dates.
- **Agenda.** Read events and tasks for a day, week, or date range. List tasks
  separately to include undated and completed work.
- **Recurring appointments.** Move or cancel a single timed occurrence, change
  future occurrences, or edit the whole series.
- **Conflicts and availability.** Check for overlapping appointments and find free
  slots within working hours. Creating or updating timed events checks for
  conflicts; recurring events are checked for 366 days, up to 10,000 occurrences.
- **Org and ICS files.** Preview imports before applying them. Export recurring
  series or individual occurrences within a date range.
- **Contacts.** Store names, email addresses, phone numbers, and notes. Import or
  export contact details as JSON or CSV.
- **Storage and backups.** Choose a calendar directory, back up the database and
  settings, and restore them later. Hermes profiles keep separate databases.

## Install

Use **Python 3.11–3.13**. From a source checkout, create an isolated environment and
install the core and CLI editable:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e packages/schedule-core -e packages/schedule-cli
```

The root is a uv workspace; install the individual packages rather than `-e .`.
`schedule-agent` is an alias for `schedule`. MCP and HTTP integrations are optional.

## Quickstart

```bash
schedule init --timezone America/Chicago     # create .schedule/ here

schedule events create --title "Project planning" \
  --start 2030-01-07T10:00 --end 2030-01-07T11:00

schedule tasks create --title "Send the proposal" --deadline 2030-01-08

schedule agenda --date 2030-01-07 --details
schedule tasks list --state TODO
```

Replace the example dates with your own. Use `schedule agenda --today` or `--week`
for the current schedule. Commands find `.schedule/` in the current directory or
its parents; use `--data-dir PATH` to select a calendar elsewhere.

Agenda output groups appointments and tasks by weekday and date, using the calendar's
configured timezone and am/pm times. Dates include the year, and entries remain
chronological so a saved agenda does not imply a live status.
`--details` adds readable notes and participants. IDs, raw records, and metadata
are available only in the agenda's `--json` mode.

Global options precede the command. Add `--json` for machine-readable results:

```bash
schedule --data-dir ./calendar init --timezone America/Chicago
schedule --data-dir ./calendar --json agenda --week
```

## Usage

### Create and update events and tasks

Create and update records with flags. Dates without offsets use the calendar's
timezone; `--timezone` selects another timezone for a record. Creating a record
returns an ID; use it to read or update that record. Below,
replace `EVENT-ID` and `TASK-ID` with the returned values.

```bash
schedule events get EVENT-ID
schedule events update EVENT-ID --location "Main office"
schedule events update EVENT-ID --clear location
schedule tasks complete TASK-ID
schedule tasks update TASK-ID --state TODO  # reopen
schedule contacts create --name Alex --email alex@example.com
```

Updates change only the fields you supply. Use `--clear FIELD` to clear an optional
field, repeating it to clear several fields. Event creation defaults to a timed
appointment. Use `--kind point`, `--kind all_day`, or `--kind anniversary` for the
other event types. Repeat `--participant EMAIL` to supply event participants;
updates replace the participant list, and `--clear participants` empties it.

Scripts can still use `--data` with JSON, `@file.json`, or `-` for standard input.
Use either field flags or `--data` in a command. In JSON updates, `null` clears
nullable fields. To see accepted JSON fields and output formats:

```bash
schedule schema events.create
schedule schema tasks.update
schedule capabilities
```

Use `tasks list` to include tasks that have no date or are already complete. A task
with both a scheduled date and a deadline appears on both days with the same ID.

### Import and export Org or ICS files

Load events and tasks from an Org or ICS file into the selected calendar.
Imports preview changes by default. Add `--apply` to save them:

```bash
# Org
schedule import calendar.org
schedule import calendar.org --apply

# iCalendar (ICS)
schedule import calendar.ics --format ics
schedule import calendar.ics --format ics --apply
```

Org is the default input format; specify `--format ics` for ICS files. To load a
directory of Org files, use `schedule import ./org-files --recursive`.

Export the stored calendar to either format:

```bash
schedule export --format org --output exported.org
schedule export --format ics --output exported.ics
```

Exports include recurring series by default. To export individual occurrences
within a date range:

```bash
schedule export --format ics --mode occurrences \
  --start 2030-01-01T00:00:00-06:00 --end 2030-02-01T00:00:00-06:00 \
  --output january.ics
```

To apply exactly the changes you previewed, save an import plan first:

```bash
schedule import calendar.org --plan /tmp/calendar-plan.json
schedule apply-plan /tmp/calendar-plan.json
```

This plan file records proposed changes; the calendar input remains Org or ICS.
Choose new export paths; exports do not overwrite existing files. Imports never
rewrite their source documents. Keep Org IDs or ICS UIDs so reimports can match
records after you edit or move the source files. Unsupported entries are reported
as errors; use `--on-unsupported skip` to skip them. Applying a saved plan fails if
the source files or database have changed since the preview.

See the [CLI guide](docs/cli.md#myorg-and-interchange) for supported Org forms,
recurrence exceptions, occurrence snapshots, and format limits.

### Back up a calendar

```bash
schedule backup create /path/to/new-backup
schedule backup inspect /path/to/new-backup
schedule backup restore /path/to/new-backup          # preview
schedule backup restore /path/to/new-backup --apply
```

A backup contains the database, settings, and a manifest describing the backup.
Use it to restore all stored data; Org and ICS exports contain calendar records.
Before upgrading an existing calendar, make a backup, then run
`schedule db migrations upgrade --apply`.

## Commands

| Command | Role |
| --- | --- |
| `schedule agenda` | Read today's agenda, a week, a date, or a time range |
| `schedule events` | List, read, create, update, and delete events; inspect exceptions and participants |
| `schedule tasks` | List, read, create, update, complete, and delete tasks |
| `schedule contacts` | Manage contact details; import and export JSON or CSV |
| `schedule calendars` | Manage named calendars |
| `schedule conflicts check` | Check a proposed timed event for overlaps |
| `schedule availability` | Read busy intervals or find available slots |
| `schedule import` / `apply-plan` / `export` | Preview, apply, and export Org or ICS calendars |
| `schedule sources` | List imported sources and preview or apply a reimport |
| `schedule init` / `status` / `doctor` | Create a calendar directory, show its settings, and check its database |
| `schedule config` / `profiles` | Read or change settings; list and inspect Hermes profiles |
| `schedule backup` / `db` | Back up, restore, check, and upgrade storage |
| `schedule schema` / `capabilities` | Show command input/output schemas and available operations |

Use `schedule COMMAND --help` for flags and subcommands. The
[CLI application guide](docs/cli.md) covers recurring-event edits, conflict-check
limits, timezones, storage, and errors.

## Agent integration

The **hermes-schedule-agent** plugin adds twelve MCP tools and skills for using them.
Hermes runs the MCP server as a child process. The database and settings live in the
selected profile's plugin-data directory. The plugin uses a separate Python
environment with editable installs of core, CLI, and MCP.

| Tools | Purpose |
| --- | --- |
| `query_schedule` | Read stored events and tasks, or an agenda for a date range |
| `create_event`, `update_event`, `delete_event` | Modify appointments |
| `create_task`, `update_task`, `complete_task`, `delete_task` | Manage work and record completion |
| `query_contacts`, `create_contact`, `update_contact`, `delete_contact` | Read and maintain contact details |

Follow the plugin checkout's README to install it. Add its `.venv/bin` directory to
your PATH, then select a Hermes profile:

```bash
schedule --profile default agenda --week
schedule --profile default tasks list --state TODO
```

Without the plugin's profile adapter, choose a calendar with `--data-dir` or run
inside a directory containing `.schedule/`. Import, export, backups, and database
maintenance are available through the CLI.

For a standalone MCP server, install `packages/schedule-mcp` alongside core and run
`python -m schedule_mcp`. It defaults to SSE; Hermes uses the plugin's stdio launcher.
The HTTP/A2A server is separate: install the core, NLP, A2A, and API packages and run
`python -m schedule_api`. Model-backed text interpretation needs provider settings;
structured scheduling and local CLI operations do not.

## How it works

CLI commands and MCP tools use the same application functions and Pydantic models
to validate input and format results. Each calendar directory contains
`schedule-agent.db` and `config.json`. Hermes chooses that directory from the
selected profile.

Tasks appear in the agenda but do not reserve time when checking availability.
Point appointments have a start time and no duration. Timed recurring events allow
changes to one occurrence, future occurrences, or the whole series. Changes to
point, all-day, and anniversary events apply to the whole series.

The implementation is split into six distributions:

| Package | Owns |
| --- | --- |
| `schedule-core` | Contracts, application operations, persistence, and Org/ICS formats |
| `schedule-cli` | Command parsing and terminal output |
| `schedule-mcp` | MCP tools generated from shared contracts |
| `schedule-nlp` | Optional model-backed request interpretation |
| `schedule-a2a` | A2A protocol and task lifecycle |
| `schedule-api` | Standalone HTTP transport |

Each package lives under `packages/NAME/src/MODULE/`. CLI-only installations need
core and CLI; they do not need FastAPI, MCP, a model provider, or Hermes.

## Development

Install all workspace packages and test dependencies:

```bash
uv sync --python 3.12 --all-packages --extra dev
uv run scripts/run_tests.sh
```

For focused checks:

```bash
uv run scripts/run_tests.sh packages/schedule-cli/tests tests/integration/mcp
```

Tests use temporary calendars. Live-model tests require explicit opt-in and provider
configuration. Build all distributions with `uv build --all-packages`.

## License

GNU Affero General Public License v3 or later — see [LICENSE](LICENSE).

Martin Bukowski · [54rt1n](https://github.com/54rt1n)
