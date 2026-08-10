# Initial implementation decisions

## Adapters

The application owns a small canonical data model. Garmin response keys remain in
the Garmin adapter, and Sheet header/range behavior remains in the Google adapter.
This supports all four combinations of fixture/Garmin and SQLite/Google without
conditional behavior in the sync service.

The normalized fixture is stable application input, not a claim that it exactly
matches Garmin's private response. Sanitized raw Garmin responses should be added as
contract fixtures after the first live read.

## Idempotency and concurrency

Keys are canonical UTC measurement timestamp for weight, calendar date for daily
activity, and string Garmin activity ID for activities. Activity IDs remain strings
to avoid spreadsheet numeric precision loss.

SQLite upserts all records and the success marker in one transaction. Google Sheets
cannot make the read-then-write upsert atomic, so a non-blocking file lock is required
on shared persistent storage. Dokploy should also disallow overlapping jobs.

## Garmin package and response contract

The live extra pins `garminconnect==0.3.9`, the current release at implementation
time. It uses the package's native mobile/web SSO engine and requires Python 3.12.
Application retries are used because the package intentionally does not retry HTTP
429 responses. Package retries are disabled to avoid nested retry loops.

Credentials bootstrap a missing token file. When a token file already exists, the
library client receives no credentials; rejected/revoked cached tokens therefore
fail instead of triggering the package's automatic full password login. The adapter
restores the pre-run token bytes after any login failure.

Known fields used by the adapter:

- `get_body_composition(start, end)` -> `dateWeightList`; `timestampGMT` is epoch
  milliseconds and mass fields are converted from grams to kilograms.
- `get_user_summary(date)` -> `calendarDate`, `totalSteps`, and
  `activeKilocalories`.
- `get_activities_by_date(start, end, sortorder="asc")` -> activity ID/name/type,
  UTC start time, duration seconds, distance metres, total activity calories
  (`calories`), average heart rate (`averageHR`), and maximum heart rate (`maxHR`).
  Calorie and heart-rate summaries are optional and do not require extra API calls.

Body-composition muscle and bone units and the Index S2 response shape remain
live-test items.

Primary references:

- <https://pypi.org/project/garminconnect/0.3.9/>
- <https://github.com/cyberjunky/python-garminconnect/releases/tag/0.3.9>
- <https://github.com/cyberjunky/python-garminconnect/blob/67794071952be2625adf647ac5c5b1fb18234899/garminconnect/client.py>
- <https://github.com/cyberjunky/python-garminconnect/blob/67794071952be2625adf647ac5c5b1fb18234899/garminconnect/typed.py#L396-L426>

## Activity links

The library exposes no stable activity-link field or helper. The production value
therefore defaults to `https://connect.garmin.com/modern/activities`. A direct URL
may be enabled through `GARMIN_ACTIVITY_URL_TEMPLATE` only after live confirmation;
the value must contain `{activity_id}`.

## Open production decisions

1. Confirm the workbook headers and `Settings!B2` success-marker location.
2. Select SMTP details or rely on a configured Dokploy failure channel.
3. Confirm direct activity links and sanitized Garmin response shapes.
4. Confirm whether the initial run needs history beyond the rolling lookback.
