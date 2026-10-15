# SocialBee weekly organic SEO report automation

## What it does
- Reports Monday–Sunday, normally running Wednesday 10:00 IST (three days after Sunday).
- Writes **11 overall metrics** to rows 3–13 and **100 page metrics** to specified rows 18–155.
- Appends to the next weekly column starting from L. Existing historical weeks are skipped.
- Does **not** modify page heading, separator, or other cells. A date header is written to row 2 (configurable).
- Reads Search Console `web` data with `dataState: final` and blocks publishing if the week has missing date rows.
- The `/Blog` group aggregates **all URLs under `/blog/`** (not a fixed list), including GSC clicks and impressions, plus GA4 organic sessions and signup events.

## Setup
1. In Google Cloud enable **Google Analytics Data API**, **Search Console API**, and **Google Sheets API**.
2. Share the Google spreadsheet with the service account email as Editor. Give the service account GA4 Viewer (or higher) and GSC property access.
3. Edit `config.yaml`: sheet tab, reporting header row, numerical GA4 property ID, exact GSC property ID (domain or URL-prefix), base URL, approved **GSC RE2** branded regex, `blog_path_prefix` (default `/blog/`), and actual signup event name.
4. Verify the exact paths/canonical URLs and review GA4 signup attribution: this counts signup **events** by organic session landing page, not assisted conversions or unique people.
5. Configure GitHub Actions secrets: `GOOGLE_CREDENTIALS_JSON` as the entire service account JSON and `SPREADSHEET_ID` from the sheet URL. **Never commit JSON secrets.**
6. For local testing: `pip install -r requirements.txt`, set the two environment variables, then run `python main.py --dry-run`. Check the proposed cells and values before running `python main.py`.
7. Commit and enable Actions in your private repository. Use Actions → Weekly Report → Run workflow to test manually.

## Important semantics
- `Overall Organic Traffic` = GA4 Organic Search `sessions`; `Signups` = GA4 organic `eventCount` filtered to the configured `sign_up` event. Session-scoped landing-page attribution is used for page signup counts.
- Page sessions use GA4 `landingPagePlusQueryString` grouped by pathname; this code strips the query string. For GA4 homepage use `/`.
- Overall Search Console impressions and clicks are aggregated at **property** level; query-filtered brand and non-brand values may not add up to overall due to anonymized queries.
- Page GSC metrics are exact canonical URL filters, and CTR is a ratio of summed clicks to summed impressions. Percentages are written as decimal values (e.g., `0.0345`) and must have percentage number-format applied to their destination rows in the Google Sheet.
- Blog GSC clicks and impressions use a `page` regex anchored to the configured website host and `/blog/` prefix, with no page dimension. This includes all blog pages under that URL prefix. Blog GA4 sessions and signups include all matching landing page paths. Blog category and pagination pages under `/blog/` are also included.
- This project assumes weekly date headers are contiguous starting in column L, in a dedicated header row that does not contain unrelated values. If that assumption does not match your actual sheet, change the column-selection logic before enabling live writes.
- For additional safety, keep a backup of the original sheet and limit editor access. Google Sheets value batch updates do **not** provide transactional compare-and-swap locking. GitHub Actions concurrency reduces overlapping scheduled executions, but does not guard independent editors or separate workflows. Never run multiple copies of this automation at the same time.
- GA4 API filtering and landing page/event dimensions must be validated against the specific GA4 property using the actual client data before relying on numbers in a client report.

## Failures
Missing configuration, failed API authentication, missing finalized GSC date rows, malformed historical headers, unexpected existing destination content, truncated API responses, and Google API errors abort the run rather than writing placeholder zeroes. Failed GitHub Actions runs can trigger standard GitHub notifications.
