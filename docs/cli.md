# Schedule CLI application surface

Use `schedule` (or `schedule-agent`) directly. The Hermes plugin installs an optional
profile adapter; it delegates profile resolution to the Hermes CLI rather than
reimplementing the profile directory rules.

```sh
schedule --profile default status
schedule --profile default agenda --week --details
schedule schema events.create
schedule --profile default --json events list --definitions
```

Global options precede the command. Standalone installations use `--data-dir PATH`
or an existing `.schedule` directory in the current directory's ancestry. No Hermes
profile is selected implicitly. The plugin runtime remains external to Hermes's
Python environment. The profile adapter fails closed if Hermes cannot resolve a
profile or changes its `profile show` output contract.

## Typed requests and results

`schedule_core.resource_contracts` defines event variants, tasks and schedule queries;
`schedule_core.contracts` supplies contact and legacy timed persistence DTOs;
`schedule_core.commands` is their shared execution registry. CLI and twelve MCP tools
use these contracts, and MCP schemas are generated from the request models.
Unknown fields are rejected. Patch omission means unchanged; explicit null clears
nullable fields. Merged events must have valid timezones, positive durations and
parseable recurrence rules. Errors expose `problem.code`, `message`, and field
issues; successful records serialize offset-bearing timestamps.

Interchange uses `ImportRequest`, `ImportPlan`, `ImportResult`, `ExportRequest`, and
`ExportResult`. Calendar items have separate typed task, anniversary, all-day and
point-in-time semantics; a missing end time never invents a duration. Additional
reporting contracts are in `surface.py`. `schema` lists the exposed schemas;
`--help` is the command/flag reference. `capabilities` lists schema-bearing operations
and supported formats. These do not imply every transport exposes every operation.

## Calendar operations

Events, tasks and contacts support `list`, `get`, `create`, `update`, and `delete`.
Create and update commands accept ordinary field flags. Optional `--data` accepts
JSON, `@file`, or `-` for stdin; it cannot be combined with field flags. Event listings can filter by
calendar, title, participant or location; `--definitions` returns series definitions.
`agenda` accepts `--today`, `--week`, `--date`, or an offset-bearing start/end range,
with an optional display timezone and detail output.

The agenda has two output modes. By default it is a human schedule: weekday/date
headings, am/pm times in the selected calendar's configured timezone, titles and
locations and separate tasks. Dates include the year and entries are chronological;
there are no clock-relative status labels that become misleading in a saved copy. `--details`
adds notes, descriptions and participants as readable text. It never adds JSON or
internal identifiers. Use the global `--json` flag before `agenda` for the complete
structured result, including IDs and timestamps; `--details` does not change JSON.

Cross-midnight events show both dates and continuation lines on later days. Equal
adjacent timed segments that meet at midnight in their record timezone are grouped
for display only when their title, calendar, timezone, location, notes, description,
recurrence value and participants match, with no ambiguous competing segment. Stored
records and JSON retain the separate entries. Date-only end dates remain exclusive.
The output can be saved directly without adding commands, profile names or debug
headers; any separately added snapshot note should be limited to update time and
the fact that the saved copy does not refresh automatically.

```sh
schedule --profile default events update EVENT-ID --scope occurrence \
  --occurrence 2030-01-14T10:00:00-06:00 \
  --start 2030-01-14T11:00:00-06:00 --end 2030-01-14T12:00:00-06:00
schedule --profile default events delete EVENT-ID --scope future \
  --occurrence 2030-01-21T10:00:00-06:00
schedule --profile default events exceptions list EVENT-ID
schedule --profile default events exceptions restore EVENT-ID --occurrence TIME
schedule --profile default events participants set EVENT-ID \
  --participant alex@example.com
```

Events accept `--title`, `--kind` (create), `--start`, `--end`, `--timezone`,
`--calendar`, `--location`, `--notes`, `--description`, `--rrule`, `--repeat`, and
repeatable `--participant`. Fields must apply to the event type: descriptions and
participants belong to timed events; Org repeaters belong to non-timed events.
On update, start/end flags apply to the stored event type. Tasks accept `--title`,
`--scheduled` (alias `--start`), `--deadline`, `--timezone`, `--calendar`,
`--location`, `--notes`, `--repeat`, and `--state`. Contacts accept `--name`,
`--email`, `--phone`, and `--notes`. Calendars use `--id` and `--name` on create,
and `--name` on update. Conflict checks accept the timed-event field flags too.

Use repeated `--clear FIELD` on updates to clear optional values. For tasks,
`--clear scheduled` clears the scheduled date; for events, `--clear participants`
empties the participant list. Setting and clearing the same field is an error.
Absent flags leave existing values unchanged. Dates/times without offsets use the
record's timezone (or the configured timezone on create).

Occurrence identity is `(series_id, original_start)`. A moved occurrence keeps that
identity. Future-scope edits split the series, preserving the remaining COUNT,
cancellations and moved instances. Future exceptions retain their original dates
and overrides under the replacement series ID.
Updates/deletes carrying an occurrence timestamp require explicit occurrence or
future scope; series scope rejects an occurrence timestamp.
Changing a recurrence anchor/rule on a series with exceptions is rejected until
those exceptions are restored; this prevents guessing how to remap them.
Descriptions, participants and calendar membership belong to the series;
occurrence-scope attempts to change them are rejected. Restoring an occurrence
checks for conflicts before removing its cancellation or override. Occurrence
queries filter the effective title and location, including overrides.

Ordinary timed-event creation and updates reject overlaps. Recurring writes check a 366-day
horizon, capped at 10,000 proposed occurrences. This is not an infinite guarantee.
`conflicts check --data @event.json --through TIME` allows an explicit bounded check.
`availability busy` returns merged timed-event intervals; `availability slots` accepts
`--duration 30m` and an optional working-hours JSON map (`0` Monday through `6` Sunday).
Point appointments lack a duration and tasks are not automatically reserved time.

`tasks complete ID --at TIME` records completion and advances Org repeaters according
to `+`, `++`, or `.+`. Reopen with `tasks update ID --state TODO`.
Ordinary patches cannot set DONE; completion owns that transition. Task list/get
include undated and completed tasks; the agenda is a dated occurrence view.
If repeat advancement lands in a DST gap, completion fails without changing the
task. Resolve the intended timing explicitly before making another write.
Generated recurrence times follow the same rule: a nonexistent local time produces
an explicit validation error rather than moving the appointment. Timed-series writes
check this within their conflict horizon; an imported series or later query window
can still encounter a gap and report it. Definitions remain available for correction.
The overlapping `items`, `anniversaries` and `tasks reopen` commands are removed.
Events use `kind: timed|point|all_day|anniversary`; omitted kind in CLI create means
timed. Timed events use start_time/end_time, points use start, and date-only variants
use start (plus optional exclusive end for all-day). Point appointments have no end.
Occurrence/future modification and deletion remain supported for timed series;
non-timed variants currently support series edits/deletes and reject scoped changes.
Participants and descriptions currently belong to timed event contracts.
`calendars` supports list/get/create/update/delete; nonempty calendars cannot be deleted.
Contacts additionally support JSON/CSV import preview/apply and export.

## MyOrg and interchange

```sh
schedule --profile default import ~/myorg/workspace --recursive \
  --on-unsupported skip --plan /tmp/myorg-plan.json --report /tmp/myorg-report.json
schedule --profile default apply-plan /tmp/myorg-plan.json
schedule --profile default export --format org --output calendar.org
schedule --profile default export --format ics --mode occurrences --start TIME --end TIME
schedule --profile default sources list
schedule --profile default sources reconcile SOURCE-ID --plan /tmp/reconcile.json
```

Recursive import excludes archive, vendor, Git/venv/Hermes directories, templates
and the MyOrg tutorial by default. Repeated `--exclude` replaces that default set.
Every imported/skipped/error record has a source and line, title, identity, action
and diagnostic. Preview distinguishes create/update/unchanged; a saved plan is
rejected if its source files or calendar state change. Apply is a single transaction.
Source reconciliation does not infer deletions and is not background synchronization.
Plans and source registrations store absolute paths, so reconciliation is independent
of the current directory. Changing an imported record between a point appointment
and a timed event replaces its representation while retaining its identity.

Supported Org forms include timed body/heading timestamps, overnight ranges ending
at 24:00, multi-day ranges, date-only entries, start-only appointments, scheduled
and deadline tasks, simple event repeaters, completion-based task repeaters, annual
birthdays and `org-anniversary` diary entries. Calendar/zone properties, participant
identifiers, location, notes, IDs and recurrence exceptions survive generated Org
interchange. General Emacs Lisp diary expressions and arbitrary document editing
are outside this calendar format; unsupported date-shaped syntax is reported.
Org date-only ranges include their last day; import/export converts that boundary
to/from the exclusive end used by the application. Multiple independent active
timestamps under one heading and ranges of daily time blocks are explicitly
unsupported and follow the error/skip policy for the whole entry. A task's distinct
scheduled and deadline timestamps remain supported.
When both dates are present, their repeaters must match (including whether a
repeater is present). Different repeaters follow the whole-entry error/skip policy;
the application currently has one completion repeater per task.
Generated Org records retain their explicit item kind. Generated ICS records retain
calendar membership, timezone and the separate event description through extension
properties. Foreign deadline-only tasks use the DUE timezone when DTSTART is absent.
Floating ICS exclusion and override identities use the series timezone, including
its offset on the exception date. Encoded Org occurrence metadata is not scanned
for scheduling timestamps; literal timestamps in occurrence notes remain notes.
Generated ICS marks derived recurrence rules so reimport restores the original Org
repeater or implicit anniversary rule without replacing its date advancement.
Explicit RRULEs remain authoritative. Other ICS clients use the projected RFC rule,
whose month/year recurrence skips invalid dates rather than clamping to month end.

Org `ID`/`EVENT_ID` and ICS UID are authoritative identities. ID-less Org uses the
source path and heading ancestry; moving/renaming those headings can require an
explicit ID to avoid creating another record. Existing records imported by the
previous CLI's deterministic title/date identity are recognized. Exact duplicate
source identities are rejected rather than merged ambiguously. Source Org files
are never rewritten. Keep the generated IDs for durable edits across reimports.
Heading timestamps do not participate in source identity. Existing timestamp-bearing
registrations are reused only when they identify one record; ambiguous matches
require explicit Org IDs.

Non-timed recurrence exceptions (for example, EXDATE on an all-day series) are
reported as unsupported; `--on-unsupported skip` skips the whole affected record.
They are never imported with their cancellations silently discarded.
ICS imports also report duplicate or missing UIDs, recurrence updates without a
base component, standalone cancelled events, and RDATE-based entries as unsupported.
Duplicate recurrence overrides for the same original instant are also rejected
through the whole-series error/skip policy.
RECURRENCE-ID RANGE overrides are also unsupported; the entire affected series
follows the selected error/skip policy rather than losing future changes.
The same error/skip policy applies; none are silently imported as active or incomplete
appointments. Cancellation exceptions attached to a supported timed series remain supported.

Import defaults to preserving source overlaps; `--conflicts reject` opts into
checks of the final imported series, including cancellations and moved occurrences.
Org and ICS exports preserve event exceptions; occurrence exports flatten a bounded view.
JSON export is a diagnostic view, not a
replacement for SQLite backup. Existing output files are not overwritten.
Flattened Org event instances and recurring items receive stable, distinct IDs.
Recurring all-day items and anniversaries are emitted as concrete dates without
recurrence rules; completion-based tasks emit only their current action. ICS
imports accept both DTEND and DURATION. Encoded Org notes preserve literal heading
and timestamp text without treating it as additional calendar entries.
Completion-based task snapshots have their own stable IDs, so reimport cannot
remove the source task's repeater. ICS durations retain the distinction between
calendar days (`P1D`) and elapsed hours (`PT24H`) across DST transitions. Malformed
encoded notes produce per-record diagnostics and respect `--on-unsupported skip`,
including for undated tasks.
When an item snapshot already exists in storage, occurrence export uses that
stored record instead of regenerating it from the recurring source. Its edits,
completion state and calendar/date placement take precedence. Nonrecurring items
retain their IDs on subsequent exports, so repeated import/export cycles do not
create new snapshots or duplicate identities.
The same precedence applies to timed snapshots created through either Org or ICS,
including when a snapshot has moved outside the requested window or calendar.

Tasks with both a scheduled date and a deadline appear on both dates (once if
the dates coincide). Ordinary edits preserve completion timestamps; reopening a
task explicitly clears its completion state.
Equivalent timestamps with different UTC offsets count as the same instant.

## Configuration and storage

```sh
schedule --profile default config show
schedule --profile default config set --timezone America/Chicago
schedule --profile default doctor
schedule --profile default db migrations status
schedule --profile default backup create /path/to/new-backup-directory
schedule --profile default db migrations upgrade --apply
schedule --profile default backup inspect /path/to/backup-directory
schedule --profile default backup restore /path/to/backup-directory       # preview
schedule --profile default backup restore /path/to/backup-directory --apply
```

Version 1 adds item/source/calendar tables without rewriting existing event/contact
rows. `init` initializes the schema; existing installations use explicit upgrade.
Backup uses SQLite's online backup API and stores configuration plus a manifest in
a new directory. Restore validates integrity and supported schema first. Core
SQLite is canonical; exports and agenda results are derived views. CLI processes
read config per invocation; a running MCP child picks up config on its next start.

## Shared MCP resource surface

Twelve tools: query_schedule; create/update/delete_event; create/update/delete_task;
complete_task; query_contacts; create/update/delete_contact. No import/export tools.
`query_schedule` returns typed `events` and `tasks` arrays, with count. Definitions
are the default; view=occurrences selects the agenda and defaults to today in the
owning profile timezone. Filters include resource, ID, event kind, title, calendar,
location, participant and task state/scheduled/due_before. Task-specific filters do
not return events. Agenda/CLI JSON retains nullable fields consistently.

MCP event creation takes `{event: {kind: ..., ...}}`; CLI `events create --data`
takes the inner event object. Updates use `{id, patch}` and deletes `{id}`;
event update/delete also accept scope and occurrence. Completion uses
`{id, completed_at?}`. Contact queries use id/name/email. Old event_id/contact_id,
update_data and name_contains argument shapes are replaced, not silently accepted.
CLI get/list and agenda are adapters over the same query implementation. The public
registry no longer exposes competing timed-only event get/list operations.

The physical SQLite tables and IDs remain unchanged. Existing imported task/item
rows are read through resource adapters; no live data rewrite is required.
