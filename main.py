            continue
        signups[p] = signups.get(p, 0) + n
    LOG.info("Excluded parameterized/invalid GA4 landing pages: %s sessions, %s events", excluded_sessions, excluded_signups)
    for page in cfg["pages"]:
        is_blog = page.get("blog_prefix_group", False)
        if is_blog:
            page_sessions = sum(n for path, n in sessions.items() if path.startswith(blog_prefix))
            page_signups = sum(n for path, n in signups.items() if path.startswith(blog_prefix))
            # No 'page' dimension: get section-wide aggregated GSC totals,
            # avoiding a capped, potentially incomplete per-URL enumeration.
            gsc_rows = gsc_report(gsc, site, start, end,
                filters=[gsc_filter("page", "includingRegex", blog_page_regex)])
        else:
            path = page["path"]
            page_sessions = sessions.get(path, 0)
            page_signups = signups.get(path, 0)
            gsc_rows = gsc_report(gsc, site, start, end,
                filters=[gsc_filter("page", "equals", canonical(page_base + path))])
        perf = aggregate(gsc_rows)
        r = page["heading_row"]
        vals.update(dict(zip(range(r + 1, r + 6), [page_sessions, perf["impressions"],
                perf["clicks"], perf["ctr"], page_signups])))
    if set(vals) != set(metric_rows(cfg)):
        raise AssertionError("Unexpected missing/extra KPI rows")
    return vals


def sheet_columns(sheets, spreadsheet_id, tab, header_row, first_col):
    grid = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_tab(tab)}!{col_name(first_col)}{header_row}:ZZ{header_row}").execute()
    return grid.get("values", [[]])[0]


def destination(sheets, spreadsheet_id, cfg, key):
    report = cfg["report"]
    tab = report["sheet_tab"]
    header_row = int(report["date_header_row"])
    first_col = col_num(report["first_week_column"])
    values = sheet_columns(sheets, spreadsheet_id, tab, header_row, first_col)
    normalized = [str(v).strip() for v in values]
    if key in normalized:
        LOG.info("Week %s already reported, skipping", key)
        return None
    # Refuse gaps between completed periods; this is append-only, not gap-filling.
    last_occupied = max((i for i, v in enumerate(normalized) if v), default=-1)
    if any(not normalized[i] for i in range(last_occupied)):
        raise RuntimeError("Gap in historical week headers; resolve before appending")
    col = col_name(first_col + last_occupied + 1)
    ranges = [f"{quote_tab(tab)}!{col}{header_row}"] + [
        f"{quote_tab(tab)}!{col}{r}" for r in metric_rows(cfg)]
    # Strictly refuse any existing values/formulas in destination cells.
    result = sheets.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id, ranges=ranges, valueRenderOption="FORMULA").execute()
    nonempty = [v["range"] for v in result["valueRanges"] if v.get("values")]
    if nonempty:
        raise RuntimeError(f"Destination {col} contains existing content: {nonempty[:5]}")
    return col


def write(sheets, spreadsheet_id, cfg, col, key, vals, dry_run):
    tab = quote_tab(cfg["report"]["sheet_tab"])
    h = int(cfg["report"]["date_header_row"])
    items = [{"range": f"{tab}!{col}{h}", "values": [[key]]}]
    for row in sorted(vals):
        items.append({"range": f"{tab}!{col}{row}", "values": [[vals[row]]]})
    if dry_run:
        LOG.info("DRY RUN: would write %s to %s (%d metrics)", key, col, len(vals))
        for item in items:
            LOG.info("%s => %s", item["range"], item["values"][0][0])
        return
    result = sheets.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": items}).execute()
    if result.get("totalUpdatedCells") != len(items):
        raise RuntimeError(f"Expected {len(items)} updated cells, got {result.get('totalUpdatedCells')}")
    LOG.info("Success: wrote week %s to column %s (%s cells)", key, col, len(items))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--today", help="Test/reference local date YYYY-MM-DD")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if cfg["report"]["week_start"].lower() != "month_day_1_7":
        raise ValueError("Set report.week_start to month_day_1_7 in config.yaml")
    start, end = reporting_dates(cfg["report"]["timezone"],
        cfg["report"]["min_days_after_week_end"], args.today)
    key = f"{start} to {end}"
    credential = creds()
    sid = os.environ["SPREADSHEET_ID"]
    sheets = build("sheets", "v4", credentials=credential, cache_discovery=False)
    col = destination(sheets, sid, cfg, key)
    if col is None:
        return
    gsc = build("searchconsole", "v1", credentials=credential, cache_discovery=False)
    ga = BetaAnalyticsDataClient(credentials=credential)
    vals = planned_values(cfg, ga, gsc, start, end)
    # Recheck after API work in case another run/manual edit filled the column.
    again = destination(sheets, sid, cfg, key)
    if again != col:
        raise RuntimeError(f"Destination changed from {col} to {again}; retry safely")
    write(sheets, sid, cfg, col, key, vals, args.dry_run)

if __name__ == "__main__":
    main()

