# Independent brand reporting — GitHub Actions

This package creates **four standalone reporting jobs** and leaves your existing SocialBee `main.py`, `config.yaml`, and `.github/workflows/weekly_report.yml` unchanged. Each brand folder contains its own full Python engine, YAML configuration, and requirements. There is **no dependency on `multi_reporting/engine.py`**.

## Install into your existing repository

Copy the folders `xovi_com/`, `xovi_de/`, `solusvm/`, `cometbackup/` to the repository root. Copy the four files from `.github/workflows/` into your repository's existing `.github/workflows/` folder. Do **not** replace existing SocialBee or `multi_reporting` files. If GitHub's browser uploader hides `.github`, create each workflow with **Add file → Create new file** and type its complete path.

## Secrets

The existing `GOOGLE_CREDENTIALS_JSON` service-account JSON secret is reused. Add/update `XOVI_COM_SHEET_ID`, `XOVI_DE_SHEET_ID`, `SOLUSVM_SHEET_ID`, and `COMETBACKUP_SHEET_ID` repository secrets. Share each sheet with the service-account email as Editor. Grant GA4 read access and exact GSC property access.

## Per-brand GSC property type

XOVI.com: `sc-domain:xovi.com` and XOVI.de: `sc-domain:xovi.de`, based on the service account's **domain property** access confirmed later in the conversation. SolusVM is `https://www.solusvm.com/`, and Comet Backup is `sc-domain:cometbackup.com`.

## Report layout

All jobs use `Sheet1`, headers in row 2, first report column L, overall metrics rows 3–13, page rows according to each individual `config.yaml`. Metric totals: XOVI.com 76, XOVI.de 71, SolusVM 26, Comet Backup 116. Periods are 1–7, 8–14, 15–21, 22–28, then 29–month-end (except February with no remaining period). Wait 3 days after the end of a period. `first_period_start: '2026-10-01'` should match each sheet's first archived period or be modified to match the current historical headers. Script processes the earliest missing eligible period.

## Conversion events

XOVI.com and XOVI.de use exact event `free_trial`, SolusVM uses `sign_up`, Comet Backup overall uses `purchase`. **Comet Backup page-level event is still `REPLACE_WITH_GA4_EVENT_NAME` because it has not been confirmed.** Its job intentionally rejects this placeholder. Update `cometbackup/config.yaml` → `report.page_signup_event_name` with the verified event before running it. Don't assume page signups equal purchases.

Comet Backup also has `/solutions/microsoft-365-backup/` twice at different rows, exactly as supplied; verify whether the duplicate is intentional.

## Testing and going live

1. Leave all four new workflows `workflow_dispatch` only. Manually trigger each from GitHub Actions with `dry_run=true`.
2. Review date, destination column, and all KPI values. Reconcile with GA4/GSC and verify no previously reported periods are overwritten.
3. Trigger **one project at a time** with `dry_run=false` for a supervised live write; confirm sheet values and percentage formatting.
4. Disable or remove the schedule from `.github/workflows/multi_brand_report.yml` **before** enabling individual schedules. Avoid the old matrix and new workflows writing to the same sheets concurrently.
5. Enable `schedule` in each standalone workflow after successful validation. Scheduled runs are live; manual runs default to dry-run.

If all already-reported periods are in columns L onward, the date strings in row 2 must use the format `YYYY-MM-DD to YYYY-MM-DD`. If historical periods use a different header format, reconcile the header format before running. GitHub concurrency only coordinates within a given workflow group, not a separate old workflow. The Google Sheets update is not a cross-workflow atomic transaction; avoid overlapping writers.

## Test locally

`pip install -r xovi_de/requirements.txt`

`export GOOGLE_CREDENTIALS_JSON="$(cat /secure/service-account.json)"`

`export XOVI_DE_SHEET_ID='actual-sheet-id'`

`python xovi_de/main.py --dry-run`

The Python engine's GSC queries use finalized search performance data. Brand and non-brand filtered totals can differ from site-wide totals due to anonymized queries. GSC domain property totals include subdomains; verify comparisons with matching Search Console dashboard properties and dates.
