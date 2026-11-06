# Tata AIG — weekly + monthly GA4 / GSC / Google Sheets automation

## Setup status

A GitHub-ready **template** with validated Python syntax, date logic, and 32-slot KPI mapping. It intentionally **will not write live data** until the spreadsheet layout has been approved and the missing properties are supplied. No API queries have been tested against the user's account.

## Folder paths

- `tata_aig/weekly.py` — daily-check scheduler, fixed monthly calendar 1–7, 8–14, 15–21, 22–28, 29–month end.
- `tata_aig/monthly.py` — complete calendar-month report.
- `tata_aig/metrics.py` — shared GA4 + GSC logic, no need to duplicate it.
- `tata_aig/config.yaml` — property IDs, scope, Sheets layout, row mapping.
- `.github/workflows/tata_aig_weekly.yml` and `tata_aig_monthly.yml` — separate GitHub Actions manual workflows, scheduled triggers commented until verified.
- `tata_aig/example_row_mapping.csv` — example 32 KPI rows (3–34), **NOT a verified sheet mapping**.

## Missing information that must be provided / confirmed

1. Exact GA4 numeric property ID, exact GSC Domain or URL-prefix identifier, service-account access, and spreadsheet ID.
2. Are the two tabs named exactly `weekly` and `monthly`? Which column has the metric labels? Which starting metric row? Which column is the first data column? Default assumptions: header row 2, metrics row 3 through 34, first report column L.
3. Start date of historical data, and how existing date headers are formatted. Exact `YYYY-MM-DD to YYYY-MM-DD` expected from the first reporting column; no overwriting, skip/gap checks.
4. AMP row scope is not specified: choose `segments.amp_scope` = `support`, `blog`, or `property`. Also confirm which AMP search appearance(s) were selected in the GSC dashboard (`AMP_BLUE_LINK` is only one subtype, while `AMP_TOP_STORIES`, `AMP_IMAGE_RESULT`, `AMP_STORY` are separate).
5. "Rich Snippet without AMP" is not a single native GSC searchAppearance filter. The example applies `REVIEW_SNIPPET` with a `notEquals AMP_BLUE_LINK` filter, **but this does not exclude all AMP formats** and the GSC API may reject multiple appearance filters. Validate against GSC UI before trusting these 3 rows or enable live writing. We can implement a stronger intersection strategy once the desired definition is confirmed.
6. Query `Car Insurance` is implemented as an exact, case-insensitive query (`(?i)^car insurance$`); change to `contains` logic / unanchored regex if the reporting requirement includes phrases such as `best car insurance`.
7. All support pages matching `/motor-insurance/car-insurance` and its descendants are included; car blog URLs matching `/knowledge-center/car-insurance/` and descendants are included. GA4 excludes query-string variants. Confirm that's the intended non-blog scope.

## GitHub steps

1. In `pmedia-debug/Reporting-auto` upload `tata_aig/` folder without changing existing project files. Create two YAML workflows under `.github/workflows/`.
2. Under Settings > Secrets and variables > Actions, reuse `GOOGLE_CREDENTIALS_JSON` and add secret `TATA_AIG_SHEET_ID` holding only the spreadsheet ID. Share both GA4 and GSC properties and the Sheet with the same service account.
3. Set `tata_aig/config.yaml` properties and `segments.amp_scope`. Confirm `site_base_url` and `first_week_start` / `first_month`. Validate source labels with `example_row_mapping.csv`.
4. Ensure the **weekly** and **monthly** tabs exist, and that row 2 has period headers matching `YYYY-MM-DD to YYYY-MM-DD`. Fill metric labels at the expected locations and set `report.label_column` to their column. This validator checks for nonempty labels, not their exact text.
5. Go to GitHub Actions > Tata AIG Weekly SEO Report > Run workflow > keep Preview enabled. Compare *every* query/filter group with its corresponding GSC / GA4 custom report. Repeat for Monthly.
6. Once verified, set `report.approved_layout: true` and `report.approved_special_filters: true` after confirming the special filters; do one controlled live run with Preview unchecked. Check the destination column, CTR percentage format, and that old columns were not edited.
7. Only then uncomment daily `schedule` in each YAML. GitHub cron is UTC: 04:30 UTC = 10:00 IST. Daily checks skip already-existing reporting periods and process missing periods in sequence.

## Key behaviors / accounting definitions

- GA4 uses `sessionDefaultChannelGroup == Organic Search`; Organic Social, Paid Search, Affiliates etc. are excluded. This is *session-scoped* organic channel attribution.
- GA4 uses `landingPagePlusQueryString` and rejects any `?` or `#` in landing-page values. A landing page qualifies if its path is exactly the support/blog prefix path or a nested descendant. It does *not* count nonorganic sessions.
- GSC query-specific rows are **property-wide**, not filtered to the car-insurance page URLs. Everything else is filtered by selected support/blog path unless configured differently for AMP.
- GSC sends `type=web`, `dataState=final`, 2–3 day delay. GSC dates are Pacific Time, whereas GA4 dates use the property's timezone; daily totals can therefore differ around day boundaries.
- CTR is calculated as clicks / impressions and written as a ratio. Format CTR cells as percentages in Sheets.
- GSC's anonymized queries can make the sum of branded/non-branded *query-filtered* totals smaller than unfiltered site totals.
- All 32 reported KPI rows are independent for each period. The scheduled automation never overwrites an existing month/week, uses separate tabs in one spreadsheet, and checks that each destination column is empty.
- If September or any earlier periods are already populated, adjust the first start values and period header format carefully; it does not alter historical data.

## Optional local checks

```bash
python -m compileall -q tata_aig
python tests/test_periods.py
```

These tests run without Google API access; they cannot validate live Search Console scope or Sheet mappings.
