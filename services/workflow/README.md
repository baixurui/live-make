# Workflow orchestration (issue #11)

## Scope and integration baseline

This local MVP is based on member 2's business API at `7fd860f` and tested
against member 5's media package at `9dd6c3c`. The media branch is not merged
or copied into this change. Publishing is integrated through the HTTP adapter described below.

The workflow uses the existing Business API SQLite database, not a second copy
of business tasks. Each workflow operation atomically saves task transitions,
audit entries, inbox deduplication and outbox messages using one transaction.
Workflow code requires its own database connection; do not share that connection
between threads or call the engine from inside a Business API transaction.
This is a local integration design, not a deployed message broker or a separate
network service. Moving to separate databases requires an internal command API
with transactional outbox/CAS support, rather than replacing SQLite blindly.

## Operator commands

Run from the repository root. Use the same absolute database path as
`BUSINESS_API_DATABASE`. The operator CLI is trusted local administration, not
an authenticated public API. Do not expose it directly to browser users.

```powershell
python -m services.workflow --help
python -m services.workflow --database business-api.sqlite3 run
python -m services.workflow --database business-api.sqlite3 register TASK_ID PUBLISH_ACCOUNT_ID
python -m services.workflow --database business-api.sqlite3 pending
python -m services.workflow --database business-api.sqlite3 receive result.json
python -m services.workflow --database business-api.sqlite3 inspect TASK_ID
python -m services.workflow --database business-api.sqlite3 pause TASK_ID
python -m services.workflow --database business-api.sqlite3 reschedule TASK_ID 2026-09-23
python -m services.workflow --database business-api.sqlite3 resume TASK_ID
python -m services.workflow --database business-api.sqlite3 cancel TASK_ID
python -m services.workflow --database business-api.sqlite3 restart TASK_ID --script-version 2
```

The default database contains business records and must not be committed. Keep
it outside the checkout or add its path to your local Git exclude configuration.

Create the business task before registering it. The content service persists
the requested script version before submitting `task.script_reviewed.v1`.
The media service persists the requested media version before submitting QC.
The tick loop polls the existing Business API approval records; no new approval
notification transport is required for this local MVP. Startup and each tick
recover due jobs from the same database.

`pending()` returns dispatchable outbox events. A consumer must acknowledge an
event only after durable acceptance. Transport delivery is at least once, not
exactly once: consumers must deduplicate too. Cancellation suppresses queued
commands, but cannot stop an external supplier call already in progress.

## Implemented behavior and explicit policy choices

- All timing uses Beijing UTC+08:00 and an injectable aware clock.
- Search starts at or after 09:00 once per enabled topic/day. Failed searches
  receive one automatic retry at or after 09:10 and one manual retry per day.
  Late startup catches up the current day; it does not replay previous days.
- Approval must finish strictly before 11:30; exactly 11:30 is too late.
  Incomplete tasks pause. Already scheduled tasks publish at or after 12:00
  on that date; next-day recovery pauses instead of silently publishing late.
- Script and video are independent gates. LOW needs one enabled reviewer;
  HIGH needs two distinct enabled accounts at each gate. Either rejection wins.
  The same reviewers may review both stages. BLOCKED never produces media.
- Approvals bind to script/media versions. Old pre-migration approvals without
  version bindings are never counted. Pure rescheduling preserves approvals;
  new scripts invalidate both gates, new media invalidates the video gate.
- The daily slot belongs to the explicitly supplied publishing account ID,
  not the internal reviewer login. First successful reservation wins.
- Script/media operations get three retries after the initial call, waiting
  1, 5 and 15 minutes. Publishing owns its one retry internally; workflow never
  schedules a publishing retry. A final publishing failure transitions directly to PAUSED.
- Script/media failures use the active `request_key` and zero-based `attempt`.
  Publishing failures use `failed_step=publishing`, the logical `request_key`,
  matching `receipt_id`, `retryable=false`, and completed call count `attempt=0..2`.
  Success and failure envelope keys must match the active logical publishing request.
- Media retries retain script, voice and media versions. The existing member 5
  pipeline reuses its cached voice after render failure. That cache is in
  memory: workflow persistence does not make supplier assets restart-safe.
- CANCELLED and PUBLISHED are terminal. In-flight publication cannot be manually
  paused/cancelled; it must return a result. After a publication attempt, manual
  rescheduling/rebuilding is refused until an operator reconciles its receipt.

## State transitions

The executable edge table is `services/business_api/state_machine.py`.
Workflow guards are stricter than edges: versions, QC, approvals, time and
daily slots are checked before actions. Registered tasks cannot use the legacy
transition endpoint to bypass these guards.

| Source | Trigger / owner | Destination and output |
| --- | --- | --- |
| DISCOVERED | operator registers selected task | TOPIC_SELECTED -> SCRIPT_GENERATING; script request |
| SCRIPT_GENERATING | persisted content + reviewed event | SCRIPT_AUTO_REVIEWING -> pending approval or SCRIPT_REJECTED for BLOCKED |
| SCRIPT_PENDING_APPROVAL | recorded human votes / workflow poll | SCRIPT_REJECTED or SCRIPT_APPROVED -> MEDIA_GENERATING; approval + media request |
| MEDIA_GENERATING | persisted media + member 5 QC event | MEDIA_QC_RUNNING -> VIDEO_PENDING_APPROVAL or VIDEO_REJECTED |
| VIDEO_PENDING_APPROVAL | recorded human votes / workflow poll | VIDEO_REJECTED or VIDEO_APPROVED -> SCHEDULED; otherwise PAUSED |
| SCHEDULED | clock >= noon on scheduled date + valid versions | PUBLISHING; publish request |
| PUBLISHING | matching receipt | PUBLISHED |
| Active execution | matching failure event | FAILED with due retry, or PAUSED when exhausted/nonretryable |
| FAILED | due retry | same failed stage; unchanged upstream versions |
| Incomplete prepublication | 11:30 cutoff | PAUSED |
| PAUSED | eligible manual reschedule/resume/rebuild | SCHEDULED / pending gate / generating stage |
| Reject states | explicit newer content version | corresponding generation stage |
| Cancellable nonterminal | manual cancel | CANCELLED; queued execution suppressed |

## Member 2 changes

- Add nullable version columns to approvals through an additive SQLite migration.
- Support optional `expected_version` on approval submission so clients can
  reject stale displays; clients should always supply it.
- Reject approvals for managed tasks outside the relevant pending stage.
- Extend legal pause/recovery transitions and remove approval/publish shortcuts.
- Reject direct legacy state transitions for workflow-managed tasks.

Existing unmanaged Business API tests remain supported. Its current header-only
internal identity scheme is not production authentication; this change does not
claim to secure that scheme or deploy service credentials.

## Member 5 adapter

`LocalMediaAdapter(workflow, pipeline, resolve_video_uri)` accepts the existing
pipeline without changing it. It persists the media version, submits QC and
acknowledges the request. The URI resolver is required because member 5 currently
returns asset IDs and metadata, not a downloadable video URI. Tests use an
explicit `mock://` URI; production must resolve a real stored asset.
Use the same pipeline instance for local failed-node voice reuse.

## Remaining cross-module transport work

- Search dispatch uses private outbox command `workflow.search_requested.v1`;
  it is NOT a thirteenth shared event. The content adapter must route it and
  return `scan_result(topic_id, date, request_key, succeeded)`.
- Content generation/results, user-facing workflow HTTP
  commands and a broker dispatcher are not implemented by this module.
- Public UI actions need authenticated Business API command routes before
  exposing the trusted operator operations to users.
- Snapshot selection of a particular script candidate remains a content/API
  responsibility; register specifies the selected target script version.
- Supplier timeouts must become correlated failure results. An operation with
  no result eventually pauses at cutoff; no unsupported timeout SLA is invented.
- Receipt reconciliation after uncertain publication and cleanup of workflow
  inbox/outbox records require agreed retention and publishing semantics.

## Publishing HTTP adapter

Set `PUBLISHING_SERVICE_URL` and `WORKFLOW_PUBLISHING_TOKEN` before `run` to enable
automatic delivery. `sync-publishing` performs one synchronization. The Business
API must set `BUSINESS_API_PUBLISHING_TOKEN`; configure the same value as
`BUSINESS_API_TOKEN` in publishing. Use separate database connections for the
Business API server and the workflow, with the same business database file.

The adapter first pulls final results, commits workflow state and inbox deduplication,
and only then acknowledges publishing's outbox. It acknowledges workflow requests
only after publishing returns a matching durable receipt. Connection loss retries
the unchanged message; it does not consume another platform retry. A malformed
or mismatched result is left unacknowledged for investigation. Legacy persisted
workflow publication retries pause for reconciliation instead of dispatching again.

`python -I tests/integration/test_publishing_workflow.py` runs real HTTP Business API
and publishing services against the real workflow and simulator, including two
failures resulting in PAUSED, response/ack loss and restart recovery.

## Verification commands

```powershell
python -m unittest discover -s tests/workflow -v
python -m unittest discover -s tests/business_api -v
python -m unittest discover -s tests/contracts -v
```

Without member 5 source, two integration tests are explicitly skipped. To run
them, set `MEDIA_PRODUCTION_SRC` to the `services/media-production/src` directory
of its checkout, then repeat the workflow tests. The tests cover the real media
pipeline event exchange and upstream voice reuse after an injected render error.
