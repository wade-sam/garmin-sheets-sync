# Personal OneDrive adapter

The `onedrive` destination is an initial adapter for a free personal Microsoft
account. It updates an existing `.xlsx` file in OneDrive without requiring Excel
desktop or a paid Microsoft 365 business tenant.

Microsoft does not expose its cell/table workbook APIs for personal OneDrive. This
adapter therefore downloads the latest complete workbook, updates its managed cells
locally, and uploads the replacement through a OneDrive upload session. The
downloaded file's eTag guards creation of that session. If the file changed or is
locked before the upload starts, the run fails rather than replacing that newer
version.

## 1. Register a personal-account application

In Microsoft Entra app registrations:

1. Create a registration that supports **Personal Microsoft accounts only**.
2. Under Authentication, enable **Allow public client flows**.
3. Add the delegated Microsoft Graph permission `Files.ReadWrite`.
4. Copy the Application (client) ID. Do not create a client secret.

The adapter uses the `/consumers` authority and device-code login. Microsoft may
still require user or administrator consent depending on account policy.

References:

- [Device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Download DriveItem content](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0)
- [Create an upload session](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0)
- [OneDrive URL upload retirement](https://devblogs.microsoft.com/microsoft365dev/important-update-on-onedrive-url-upload-feature/)

## 2. Prepare the workbook

The live workbook is `/Sam Diet.xlsx`. Its required ingestion tables are
`WeightLog`, `GarminDaily`, and `GarminActivities`, with their headers on row 3 as
documented in the README. The adapter will not create a missing file, sheet, table,
or header. It writes only the documented Garmin-owned columns and the success
marker.

For the safest first live test, use a copy of the workbook containing fixture data.
Keep another recoverable copy until the workbook has been opened and inspected in
Excel for the web after a sync.

Important limitation: `openpyxl` alone reduced the live workbook from 16 chart
parts to 4 in a test render, so the adapter does not upload that direct render. It
instead merges only the managed sheets' cell data and table range references into
the original OOXML package, then rejects the result if chart, drawing, media,
external-link, pivot, or slicer part counts decrease. The live in-memory round-trip
retained all 16 charts and both drawing parts. Formula calculation still occurs in
Excel rather than Python, and a manual Excel-for-the-web check remains required
after the first remote write. Tables with totals rows are rejected on the three
ingestion tabs.

For existing personal-OneDrive items, the adapter deliberately omits the optional
`fileSize` upload-session property: the live API returned `invalidRequest` when it
was present. Any session that reaches byte transfer but then fails is explicitly
cancelled so it cannot leave the workbook locked until session expiry.

The adapter uses the upload session's normal automatic commit. Although Microsoft's
upload-session reference still describes a deferred personal-account commit using
`@microsoft.graph.sourceUrl`, the live API rejects that request because OneDrive's
URL-upload feature was retired. Automatic commit leaves a small concurrency window
between the session's eTag check and completion of the single-chunk upload. Do not
edit the workbook during a sync; schedule it for a time when Excel and Claude are
closed.

## 3. Authenticate once

Install live dependencies and set the client, cache, and existing workbook path:

```bash
uv sync --extra dev --extra live
export ONEDRIVE_CLIENT_ID='your-application-client-id'
export ONEDRIVE_TOKEN_CACHE_FILE="$HOME/.local/share/garmin-sheets-sync/onedrive-token-cache.json"
export ONEDRIVE_WORKBOOK_PATH='/Sam Diet.xlsx'
uv run garmin-sheets-sync onedrive-auth
```

Open the displayed Microsoft URL, enter the device code, and sign in to the personal
account that owns the workbook. The serialized MSAL cache is written with mode
`0600`; its parent directory is set to `0700`. Back it up and protect it like a
password. Scheduled syncs use silent token refresh and never pause for interactive
login. If Microsoft revokes the grant, run `onedrive-auth` again.

Inspect the existing workbook without changing or printing its row data:

```bash
uv run garmin-sheets-sync onedrive-inspect
```

The JSON report lists required/missing/extra headers, row counts, formula columns,
tables, and advanced OOXML package features. Exit code `0` means the structural
contract is ready, `2` means the workbook was read successfully but needs changes,
and `1` means authentication, download, or parsing failed.

## 4. Smoke-test with fixtures

```bash
ALERT_MODE=platform uv run garmin-sheets-sync sync \
  --source fixture \
  --destination onedrive \
  --fixture fixtures/sample.json \
  --start 2026-08-08 \
  --end 2026-08-09
```

Use a disposable copy of `Sam Diet.xlsx` for fixture writes. Run it twice: the first
run should insert records and the second should report them unchanged while updating
`Settings!B2`. Then open that copy in Excel for the web and verify formulas, tables,
formatting, charts, validations, and any Claude-created content before enabling
Garmin or a schedule against the live file.

OneDrive returns HTTP `423` while Excel, Claude, or a post-upload processing lock
holds the workbook. The adapter treats that as a conflict, cancels its upload
session, and leaves the remote file unchanged. Close the workbook, allow the lock to
clear, and rerun the command.

Do not edit the workbook during a sync. The eTag is checked when the upload session
starts, but the personal OneDrive API provides no final compare-and-swap for its
automatic commit. Whole-file replacement also cannot provide the same fine-grained
coexistence as Excel's business workbook APIs.

For Dokploy, replace the Google destination variables with:

```text
SYNC_DESTINATION=onedrive
ONEDRIVE_CLIENT_ID=<application-client-id>
ONEDRIVE_TOKEN_CACHE_FILE=/data/onedrive-token-cache.json
ONEDRIVE_WORKBOOK_PATH=/Sam Diet.xlsx
ONEDRIVE_SETTINGS_TAB=Settings
ONEDRIVE_LAST_SUCCESS_CELL=B2
```

Run `garmin-sheets-sync onedrive-auth` once through the application console and
keep `/data` persistent across deployments. No Google JSON file mount is needed for
OneDrive. Leave the Google adapter available in the release until the OneDrive
fixture and Garmin smoke tests both pass.

## Claude for Excel

Claude for Excel supports Claude Pro and Excel for the web, so the user's existing
Claude Pro subscription is sufficient for the add-in. Install it from Microsoft
Marketplace, open the OneDrive workbook in Excel for the web, and sign in with the
Claude account. Its usage counts against the normal Claude plan limits.

Do not leave Claude actively editing while this sync runs. The current 16 charts
survive the package-merge round trip, but repeat the copy-and-smoke-test process
after adding new advanced objects to one of the four managed sheets. Claude can edit
the other dashboard and check-in sheets without those sheet parts being regenerated.

Reference: [Use Claude for Excel](https://support.claude.com/en/articles/12650343-use-claude-for-excel)
