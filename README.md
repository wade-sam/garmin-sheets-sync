# Garmin Sheets Sync

A one-shot Python service that moves Garmin weight, daily activity, and recorded
activities into Google Sheets or an Excel `.xlsx` workbook in OneDrive. It performs
no trend or target calculations.

The default configuration is deliberately offline: normalized fixture input is
written to a local SQLite database. Live clients are imported only when their
adapters are selected.

## Current status

- Offline fixture-to-SQLite sync is implemented and tested.
- Garmin Connect `0.3.9` parsing, bounded retries, native token persistence, and
  failed-login token restoration have been smoke-tested against the target account.
- Google Sheets targeted-cell upserts are implemented against the documented
  `gspread 6.2.1` API but not yet run against the target workbook.
- Personal OneDrive authentication, download, structural inspection, and an
  in-memory round-trip against `Sam Diet.xlsx` are verified. A guarded remote
  replacement inserted all six fixture records into a disposable copy while
  retaining its 16 chart and two drawing parts; a second run reported all six
  unchanged. A bounded Garmin sync then inserted five live records into the
  original workbook with the same package features intact.
- Activity links use the Garmin activities dashboard until a direct route is
  confirmed during the live smoke test.

## Run locally without APIs

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run garmin-sheets-sync sync \
  --source fixture \
  --destination sqlite \
  --fixture fixtures/sample.json \
  --database .local/garmin-sync.db \
  --start 2026-08-08 \
  --end 2026-08-09
```

Run the same command again. The first run reports six inserts; the second reports
six unchanged records and no inserts. Inspect the result with:

```bash
sqlite3 .local/garmin-sync.db \
  'SELECT * FROM weight_log; SELECT * FROM daily_activity; SELECT * FROM activities;'
```

Offline mode does not require the `live` extra, Garmin, Google, or Microsoft
credentials, or network access.

## Verification

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

## Live adapter isolation

The source and destination switches are independent. Validate one external system
at a time before enabling the complete path:

```bash
# Garmin read and parsing only; destination remains local.
uv sync --extra dev --extra live
ALERT_MODE=platform SYNC_SOURCE=garmin SYNC_DESTINATION=sqlite \
  uv run garmin-sheets-sync sync

# Google writes using safe fixture data; Garmin is never contacted.
ALERT_MODE=platform SYNC_SOURCE=fixture SYNC_DESTINATION=google \
  uv run garmin-sheets-sync sync \
  --start 2026-08-08 --end 2026-08-09

# OneDrive writes using fixture data, after one-time authentication. Point this at
# a disposable copy of the workbook, not the live Sam Diet.xlsx file.
ALERT_MODE=platform SYNC_SOURCE=fixture SYNC_DESTINATION=onedrive \
  uv run garmin-sheets-sync sync \
  --start 2026-08-08 --end 2026-08-09

# Complete OneDrive path after its fixture check and the Garmin read check pass.
ALERT_MODE=platform SYNC_SOURCE=garmin SYNC_DESTINATION=onedrive \
  uv run garmin-sheets-sync sync
```

Without explicit dates, the command syncs a configurable inclusive three-day
rolling window through today. Upserts make this safe for routine runs and backfills.
When Garmin has not populated the current day's step or active-calorie counter yet,
the adapter records zero; a later hourly run replaces it with the populated value.

## Secrets

Do not paste credentials into source files, logs, or chat. Export them from the
shell or use Dokploy secrets. For local work, a gitignored `.env` can be loaded with
`set -a; source .env; set +a` before running the command:

```bash
export GARMIN_EMAIL='...'
export GARMIN_PASSWORD='...'
export GARMIN_TOKEN_DIR="$HOME/.garminconnect"

export GOOGLE_SERVICE_ACCOUNT_FILE='/run/secrets/google-service-account.json'
export GOOGLE_SHEET_ID='...'

export ONEDRIVE_CLIENT_ID='...'
export ONEDRIVE_TOKEN_CACHE_FILE="$HOME/.local/share/garmin-sheets-sync/onedrive-token-cache.json"
export ONEDRIVE_WORKBOOK_PATH='/Sam Diet.xlsx'
```

Garmin does not issue a developer API key for this flow. A live login needs the
account email and password. The token directory is created with mode `0700`, and
`garmin_tokens.json` is set to `0600`. If Garmin begins requesting MFA, the
unattended run fails rather than waiting for input.

Credentials are used only to create the first token file. Once a token file exists,
the client is constructed without credentials so a revoked token fails and alerts
instead of silently performing a full password login. The last-known-good token file
is restored after failed authentication.

The Google service account's `client_email` must be an editor on the workbook.
The credential JSON should be mounted as a secret file, not stored in this repo.

The OneDrive client ID is not a secret. The token-cache file is a credential and
must remain private and persistent. See [Personal OneDrive adapter](docs/onedrive-adapter.md)
for app registration, one-time device login, safety limits, and the fixture smoke test.

## Spreadsheet contract

All tabs and headers must already exist. Headers may be in any column order within
their managed range, but their spelling must match exactly.

The Google destination retains its original row-1 contract:

`Weight Log`:

```text
Measurement Timestamp | Weight (kg) | Body Fat (%) | Skeletal Muscle Mass (kg) |
Bone Mass (kg) | Body Water (%) | BMI | Source
```

`Garmin Daily Activity`:

```text
Date | Steps | Active Calories
```

`Garmin Activities`:

```text
Activity ID | Activity Name | Activity Type | Start Time | Duration (seconds) |
Distance (meters) | Calories (kcal) | Average Heart Rate (bpm) |
Max Heart Rate (bpm) | Garmin Connect Link
```

Activity calories are Garmin's total calories for the recorded activity. They are
separate from the day's `Active Calories`. Average and maximum heart rate are blank
when Garmin does not provide an activity HR summary.

The `Settings` tab must also exist. A successful run writes an RFC3339 UTC timestamp
to `B2` by default. Configure the tab and cell with the destination-specific
`*_SETTINGS_TAB` and `*_LAST_SUCCESS_CELL` variables.

The adapter resolves columns by header, rejects duplicate keys, writes only owned
cells, and refuses to replace a colliding non-Garmin row in `Weight Log`.

The OneDrive destination recognizes the existing `Sam Diet.xlsx` named tables and
their row-3 headers:

- `WeightLog`: writes `Date`, `Timestamp`, `Weight (kg)`, body-fat percentage,
  muscle and bone mass in pounds, body-water percentage, `BMI`, `Source`, and
  `Sync Timestamp`. It preserves the pounds, rolling-average, goal, pace, and chart
  helper formulas.
- `GarminDaily`: writes `Date`, `Steps`, and `Active Calories`; `Total Calories`
  and `Notes/Metadata` remain workbook-owned.
- `GarminActivities`: writes date, type, name, duration in seconds, distance in
  metres, start time, activity calories, the exact text Garmin ID, and link.
  `Notes/Metadata` remains workbook-owned.

The adapter expands the named-table ranges and copies the existing formula rows.
It merges only the four managed sheets' cell data and the named-table range
references back into the original OOXML package, preserving the workbook's 16 chart
parts and its untouched dashboard/check-in sheets.

## Failures and alerts

Every failure exits nonzero and logs its source, destination, date window, and error
type. Live adapters refuse to run with implicit log-only alerting. Set
`ALERT_MODE=smtp` plus the `SMTP_*` and `ALERT_EMAIL_*` variables from `.env.example`
for production. Use `ALERT_MODE=platform` only after a scheduled-job failure
notification path has been independently configured and tested. External deployment
notifications remain important because in-process email cannot report a container
that never starts.

The dashboard/formula layer should flag a `Last Successful Sync` older than 36 hours.
A stopped ingestion process cannot reliably perform its own staleness check.

## Container

```bash
docker build -t garmin-sheets-sync .
docker run --rm \
  --env-file .env \
  --env SYNC_SOURCE=garmin \
  --env SYNC_DESTINATION=onedrive \
  --mount type=volume,src=garmin-sync-data,dst=/data \
  garmin-sheets-sync sync
```

The image defaults to a persistent `worker` process for Dokploy. The worker does not
contact external APIs. Dokploy schedules execute `garmin-sheets-sync sync` inside
the running container. The shared `/data/sync.lock` rejects overlapping runs, and
`/data/garmin` preserves tokens between releases.

## Production deployment

Production uses the same release-driven pattern as `fplbuddy`:

1. Release Please creates a Python release pull request from Conventional Commits.
2. Merging it creates a `v*` GitHub release tag.
3. GitHub Actions builds a multi-architecture image in GHCR.
4. GitHub Actions updates the existing Dokploy application to that exact version.
5. Dokploy continues running the worker; the next scheduled job performs the sync.

Required GitHub secrets are `RELEASE_PLEASE_TOKEN`, `DOKPLOY_HOST`,
`DOKPLOY_TOKEN`, `DOKPLOY_APP_ID`, and the three `DOKPLOY_REGISTRY_*` values
documented in the runbook. Garmin, Microsoft token-cache, and SMTP credentials
belong in Dokploy, not GitHub Actions.

The complete application, volume, secret-file mount, schedule, first-run, alerting,
and rollback procedure is in [docs/dokploy-deployment.md](docs/dokploy-deployment.md).
