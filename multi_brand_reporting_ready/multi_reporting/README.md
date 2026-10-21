# Multi-brand reporting (isolated from SocialBee)

**Do not overwrite the existing `main.py`, `config.yaml` or `.github/workflows/weekly_report.yml`.** Copy the contents of this ZIP into the repository root, preserving `multi_reporting/` and `.github/workflows/multi_brand_report.yml`. Four brands run separately. SocialBee stays on its existing workflow.

## Credentials / permissions
Use the existing `GOOGLE_CREDENTIALS_JSON` GitHub Actions secret. Grant the same service-account email access to each GA4 property, GSC property, and the four spreadsheets as Editor. Add four GitHub Actions repository secrets:

- `XOVI_COM_SHEET_ID`
- `XOVI_DE_SHEET_ID`
- `SOLUSVM_SHEET_ID`
- `COMETBACKUP_SHEET_ID`

Use the sheet IDs provided by the client. The secrets are referenced by configs through `spreadsheet_id_env`. The IDs are not OAuth tokens; nevertheless the workflow keeps configuration separate from credentials.

## Critical configuration checks
- For XOVI.com and XOVI.de, replace `REPLACE_WITH_GA4_EVENT_NAME` in both `signup_event_name` and `page_signup_event_name` with the **exact** GA4 event identifier from Admin > Events (or Events report). "Free trial" is the sheet label, not necessarily an event name. Until corrected, these two projects intentionally fail rather than publish conversion counts.
- SolusVM uses `sign_up` for overall and page-level conversion rows. Comet Backup uses `purchase` for the overall Purchase row only. Page rows say "Sign Up" and therefore `page_signup_event_name` is intentionally unset pending confirmation of the actual signup event. Set the exact GA4 event before a run.
- The duplicate `/solutions/microsoft-365-backup/` entry for Comet Backup is intentionally preserved at rows 59 and 101 because the supplied template lists it twice. Correct one mapping if that is a spreadsheet typo.
- The row-2 header must contain the date string `YYYY-MM-DD to YYYY-MM-DD` for each recorded period. `first_period_start` defaults to October 1, 2026. If the destination Sheet has earlier history or a different first period, reconcile the headers before going live.
- Original page paths (including trailing slash on GSC canonical URLs) must match real GA4 landing page paths and GSC selected canonical URLs. Cross-check values before enabling scheduled writes.
- Percentage KPI rows are stored as decimals: format them in Sheets as Percent.

## Getting started
1. Copy package into repo. Keep the original SocialBee workflow enabled only as previously tested.
2. Verify config files and add four new spreadsheet secrets.
3. Leave the new workflow `multi_brand_report.yml` on manual mode and use **Preview only** to test. Four independent jobs show distinct successes/failures.
4. Compare dry-run data against GA4/GSC for each brand.
5. Run each project's first live update only after protecting historical ranges and verifying new reporting column is truly unused.
6. After all four verified, uncomment the `schedule` block to run daily eligibility checks at 10:00 AM IST.

## Operation
Each daily job selects the **earliest eligible missing** date period from the initial period, runs GA4 and GSC queries, verifies GSC finalized daily data, then appends the 11 overall KPIs plus five KPIs per page. A project that is already up to date skips. If several periods are missed, the first missing period is written in one run and the remainder are filled on subsequent scheduled days; additional manual runs can catch up faster.

For a local dry run:
```
python multi_reporting/engine.py --config multi_reporting/configs/solusvm.yaml --dry-run
```
Set `GOOGLE_CREDENTIALS_JSON` and the appropriate `<PROJECT>_SHEET_ID` environment variable first.

**Limits:** The sheet pre-write check is not a transactional compare-and-swap; avoid parallel writers to the same sheet outside this workflow. GSC anonymized search-query totals need not sum to overall totals. A report can legitimately contain zero events; verify GA4 event attribution and nomenclature independently.
