# Dokploy deployment

This runbook provisions `garmin-sheets-sync` as a release-driven Dokploy
application. A GitHub release publishes an image to GHCR and asks Dokploy to
redeploy that exact version. Deployments do not run a Garmin sync. Dokploy schedule
jobs execute the one-shot sync command inside the persistent worker container.

## Architecture

- Release Please maintains the release PR, changelog, Python package version, and
  `v*` GitHub release tag.
- GitHub Actions builds `linux/amd64` and `linux/arm64` images and publishes exact
  version tags such as `ghcr.io/wade-sam/garmin-sheets-sync:0.2.0`.
- GitHub Actions updates the existing Dokploy application through the Dokploy API.
- The application runs one non-root worker replica with no public port or domain.
- Dokploy runs `garmin-sheets-sync sync` inside that replica on a schedule.
- A persistent `/data` volume holds the Garmin token and the cross-run lock.

Dokploy application schedules require the target container to remain running. The
image therefore defaults to `garmin-sheets-sync worker`; the worker does not call
Garmin or Google and exits cleanly on `SIGTERM`.

## 1. GitHub configuration

Create a fine-grained personal access token for Release Please with access to this
repository and read/write permissions for Contents and Pull requests. Add it as a
repository secret:

```text
RELEASE_PLEASE_TOKEN
```

The separate token is required because a release tag created with GitHub's built-in
workflow token does not trigger another workflow.

Create a GitHub environment named `production`. Add these environment secrets:

```text
DOKPLOY_HOST
DOKPLOY_TOKEN
DOKPLOY_APP_ID
DOKPLOY_REGISTRY_URL
DOKPLOY_REGISTRY_USERNAME
DOKPLOY_REGISTRY_PASSWORD
```

`DOKPLOY_HOST` may be either a hostname or a full `https://` URL; plaintext HTTP is
rejected. `DOKPLOY_TOKEN` must be an API key that can read, update, and redeploy the
application. The application ID is available in Dokploy after creating the
application in the next step.

The release workflow uses the built-in `GITHUB_TOKEN` to publish to GHCR. Set
`DOKPLOY_REGISTRY_URL=ghcr.io`; use the GitHub account name as the registry username
and a dedicated token with `read:packages` as the registry password. The workflow
sends these values to Dokploy without logging them so private release images can be
pulled.

## 2. Create the Dokploy application

1. Create an application named `garmin-sheets-sync` in the target environment.
2. Select the Docker image provider. It is fine to enter the future release image
   and leave the application undeployed until the first image exists.
3. Set the image to `ghcr.io/wade-sam/garmin-sheets-sync:<version>`.
4. Configure exactly one replica. Do not expose a port or attach a domain.
5. Do not override the image entrypoint or command. The default command is `worker`.
6. Copy the application ID into the GitHub `production` environment secret
   `DOKPLOY_APP_ID`.

Configure a persistent volume named for this service and mount it at `/data`. It
must survive releases and rollbacks and remain on the same Docker node. The process
runs as UID `10001`; after the first deployment, use Dokploy's Run Command feature
to verify access without printing token contents:

```sh
test -w /data
test -d /data/garmin || mkdir -m 700 /data/garmin
```

Do not delete or replace this volume during a rollback. The Garmin token stored in
it is credential material and should be covered by the same access restrictions as
other secrets.

## 3. Configure runtime secrets

Set these application environment variables in Dokploy:

```text
SYNC_SOURCE=garmin
SYNC_DESTINATION=google
SYNC_LOOKBACK_DAYS=3
SYNC_LOCK_FILE=/data/sync.lock
GARMIN_TOKEN_DIR=/data/garmin
GARMIN_RETRY_ATTEMPTS=3
GARMIN_RETRY_BASE_SECONDS=10
GARMIN_RETRY_MAX_SECONDS=60
GARMIN_EMAIL=<secret>
GARMIN_PASSWORD=<secret>
GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google-service-account.json
GOOGLE_SHEET_ID=<secret>
GOOGLE_SETTINGS_TAB=Settings
GOOGLE_LAST_SUCCESS_CELL=B2
TZ=Europe/London
LOG_LEVEL=INFO
ALERT_MODE=smtp
SMTP_HOST=<secret>
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=<secret when required>
SMTP_PASSWORD=<secret when required>
ALERT_EMAIL_FROM=<secret>
ALERT_EMAIL_TO=<secret>
```

Keep Garmin, Google, and SMTP values in Dokploy only. They are runtime secrets and
must never be added as GitHub Actions build arguments.

Create a Dokploy File Mount containing the Google service-account JSON at:

```text
/run/secrets/google-service-account.json
```

Mount it read-only and ensure UID `10001` can read it. Verify through Run Command:

```sh
test -r /run/secrets/google-service-account.json
test ! -w /run/secrets/google-service-account.json
```

The service account's `client_email` must have Editor access to the Google
workbook. Create all required tabs and exact headers from the README before the
first sync.

### Personal OneDrive pilot

To use the personal OneDrive adapter instead of Google, set these values in place
of the `GOOGLE_*` variables:

```text
SYNC_DESTINATION=onedrive
ONEDRIVE_CLIENT_ID=<application-client-id>
ONEDRIVE_TOKEN_CACHE_FILE=/data/onedrive-token-cache.json
ONEDRIVE_WORKBOOK_PATH=/Sam Diet.xlsx
ONEDRIVE_SETTINGS_TAB=Settings
ONEDRIVE_LAST_SUCCESS_CELL=B2
```

Do not mount the Google service-account JSON for a OneDrive-only deployment. Keep
the schedule disabled, then run this once through Dokploy's Run Command feature and
complete the displayed device login:

```sh
garmin-sheets-sync onedrive-auth
```

Run a fixture-to-OneDrive smoke test twice against a disposable workbook copy before
enabling Garmin or the schedule against `Sam Diet.xlsx`, and inspect the copy in
Excel for the web after each run. The token cache under `/data` is credential
material and must survive releases. Full setup and workbook round-trip limitations
are documented in [onedrive-adapter.md](onedrive-adapter.md).

Use SMTP for production failures. Dokploy records schedule execution logs, but do
not use `ALERT_MODE=platform` unless a scheduled-job failure notification path has
been independently configured and tested.

## 4. Configure the schedule

Create an Application schedule in Dokploy:

```text
Name: Garmin Sheets Sync
Command: garmin-sheets-sync sync
Cron: 15 * * * *
Timezone: Europe/London
Shell: sh
Enabled: false during provisioning
```

This runs hourly at 15 minutes past the hour. The three-day rolling window and
idempotent upserts safely revisit recent data. `/data/sync.lock` rejects an
overlapping invocation before a Garmin or spreadsheet client is constructed.

If Excel or Claude holds the personal OneDrive workbook open for editing, the run
can fail safely with HTTP `423` and leave the file unchanged. Do not force the
lock. The next scheduled run revisits the same three-day window after the workbook
is closed, so the hourly runs provide automatic catch-up without a long
in-process wait.

Keep the schedule disabled while provisioning runtime credentials and performing
the first manual sync. Enable it only after that sync completes successfully.

The explicit application and schedule timezones keep the rolling date window
aligned with Garmin calendar dates. Partial current-day data is revisited on every
subsequent run.

## 5. First deployment and live verification

1. Merge the Release Please pull request. This creates the release tag, publishes
   the GHCR image, and redeploys the Dokploy worker.
2. Confirm the application remains running and healthy as UID `10001`.
3. Confirm `/data` is writable and the Google credential file is readable but not
   writable.
4. Run the Dokploy schedule manually once. Do not trigger a separate sync from CI.
5. Confirm `sync_completed` in the schedule log and verify data in all three Sheets
   tabs.
6. Confirm `Settings!B2` contains the new UTC success timestamp.
7. Confirm `/data/garmin/garmin_tokens.json` exists with mode `0600` without
   displaying or downloading its contents.
8. Send a test SMTP alert or deliberately use invalid fixture input in a separate
   non-live environment to verify alert delivery without another Garmin login.
9. Enable the Dokploy schedule.

The first successful Garmin login bootstraps the persisted token. Later runs reuse
it. If Garmin revokes that token, investigate before replacing it; repeatedly
deleting the token forces password logins and increases rate-limit risk.

## Release and rollback flow

Normal release:

1. Merge Conventional Commits to `main`.
2. Release Please opens or updates a release pull request.
3. Merge the release pull request when ready.
4. The `v*` tag builds the exact GHCR version, redeploys Dokploy, and waits for the
   application deployment to complete successfully.
5. The next scheduled sync is the live canary.

Rollback:

1. Open GitHub Actions and run `Build and Deploy` manually.
2. Enter a known-good version without the `v` prefix, or leave it empty to redeploy
   the latest GitHub release.
3. The workflow selects the existing GHCR image and redeploys it without rebuilding
   or modifying `/data`.

The release pipeline intentionally does not publish a mutable `latest` tag.

## Troubleshooting

### Worker exits immediately

Confirm the application does not override the image command. It must run `worker`,
not `sync`. The sync command belongs in the Dokploy schedule.

### Schedule cannot start

Dokploy application schedules execute inside a running container. Confirm the
worker is healthy and the application has exactly one replica.

### Permission denied under `/data`

The mounted volume must be writable by UID `10001`. Correct ownership on the host or
replace the empty volume with one initialized for that UID. Do not recursively
change ownership on a volume that already contains a Garmin token until it has been
backed up securely.

### GHCR image cannot be pulled

Check the `DOKPLOY_REGISTRY_URL`, `DOKPLOY_REGISTRY_USERNAME`, and
`DOKPLOY_REGISTRY_PASSWORD` GitHub environment secrets. The password must be a token
with `read:packages` access to this private package.

### Deploy workflow reports a Dokploy HTTP error

Check `DOKPLOY_HOST`, API-key permissions, and `DOKPLOY_APP_ID`. The deploy script
uses `curl --fail-with-body`, so Dokploy 4xx and 5xx responses fail the workflow.

Official references:

- <https://docs.dokploy.com/docs/core/schedule-jobs>
- <https://docs.dokploy.com/docs/core/applications>
- <https://docs.dokploy.com/docs/api/application>
- <https://github.com/googleapis/release-please-action>
