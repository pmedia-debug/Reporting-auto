"""Append-only weekly SocialBee SEO metrics to the supplied sheet layout."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import yaml
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Filter, FilterExpression, FilterExpressionList,
    Metric, RunReportRequest,
)

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]
LOG = logging.getLogger("weekly_report")



def creds():
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    if raw:
        # GitHub Actions: credentials from secret
        return service_account.Credentials.from_service_account_info(
            json.loads(raw),
            scopes=SCOPES
        )

    # Local testing: credentials from JSON file
    return service_account.Credentials.from_service_account_file(
        "service-account.json",
        scopes=SCOPES
    )



def col_num(s):
    result = 0
    for c in s.upper():
        if not ('A' <= c <= 'Z'):
            raise ValueError("Invalid column: " + s)
        result = result * 26 + ord(c) - 64
    return result


def col_name(n):
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def quote_tab(name):
    return "'" + name.replace("'", "''") + "'"


def reporting_dates(tz, min_delay, date_override=None):
    today = date.fromisoformat(date_override) if date_override else datetime.now(ZoneInfo(tz)).date()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(days=7)
    end = start + timedelta(days=6)
    if (today - end).days < min_delay:
        raise ValueError("Reporting week is too recent for configured data delay")
    return start.isoformat(), end.isoformat()


def aggregate(rows):
    clicks = sum(float(x.get("clicks", 0)) for x in rows)
    impressions = sum(float(x.get("impressions", 0)) for x in rows)
    return {"clicks": clicks, "impressions": impressions, "ctr": (clicks / impressions if impressions else 0.0)}


def gsc_report(service, site, start, end, filters=None, dimensions=None, aggregation="auto"):
    body = {"startDate": start, "endDate": end, "type": "web", "dataState": "final",
            "dimensions": dimensions or [], "rowLimit": 25000, "aggregationType": aggregation}
    if filters:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
    rows = []
    pos = 0
    while True:
        body["startRow"] = pos
        response = service.searchanalytics().query(siteUrl=site, body=body).execute()
        batch = response.get("rows", [])
        rows.extend(batch)
        if len(batch) < 25000:
            break
        pos += 25000
        if pos >= 50000:
            raise RuntimeError("GSC row cap reached; stop rather than publish partial data")
    return rows


def gsc_filter(dimension, operator, expression):
    return {"dimension": dimension, "operator": operator, "expression": expression}


def verify_gsc_date(service, site, start, end):
    rows = gsc_report(service, site, start, end, dimensions=["date"])
    dates = {r["keys"][0] for r in rows}
    expected = {(date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(7)}
    if not expected.issubset(dates):
        raise RuntimeError(f"Finalized GSC data missing dates: {sorted(expected - dates)}")


def ga_filter(*items):
    return FilterExpression(and_group=FilterExpressionList(expressions=list(items)))


def ga_exact(field, value):
    return FilterExpression(filter=Filter(field_name=field, string_filter=Filter.StringFilter(
        match_type=Filter.StringFilter.MatchType.EXACT, value=value)))


def ga4_report(client, property_id, start, end, metric, group_dimension=None, signup_event=None):
    filters = [ga_exact("sessionDefaultChannelGroup", "Organic Search")]
    if signup_event:
        filters.append(ga_exact("eventName", signup_event))
    request = RunReportRequest(
        property=f"properties/{property_id}", date_ranges=[DateRange(start_date=start, end_date=end)],
        dimensions=[Dimension(name=group_dimension)] if group_dimension else [],
        metrics=[Metric(name=metric)], dimension_filter=ga_filter(*filters), limit=100000,
    )
    response = client.run_report(request)
    data = {}
    for row in response.rows:
        key = row.dimension_values[0].value if group_dimension else "__total__"
        data[key] = data.get(key, 0) + int(float(row.metric_values[0].value))
    if response.row_count > len(response.rows):
        raise RuntimeError(f"GA4 {metric} has more rows than returned; refuse partial result")
    return data


def normalize_landing(value):
    """Convert GA4 landingPagePlusQueryString into pathname, discarding query strings."""
    return urlsplit(value).path or "/"


def canonical(url):
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"Invalid page URL: {url}")
    return urlunsplit((u.scheme, u.netloc, u.path, "", ""))


def metric_rows(cfg):
    rows = list(range(3, 14))
    for page in cfg["pages"]:
        rows.extend(range(page["heading_row"] + 1, page["heading_row"] + 6))
    if len(rows) != 111 or len(set(rows)) != 111 or max(rows) != 155:
        raise ValueError("Page row mapping does not match 111 KPI cells")
    return rows


def planned_values(cfg, ga, gsc, start, end):
    prop = cfg["properties"]
    regex = cfg["filters"]["branded_query_regex"]
    if not regex or regex.startswith("REPLACE_"):
        raise ValueError("Set your approved branded_query_regex (GSC RE2 syntax) in config.yaml")
    blog_prefix = cfg["report"].get("blog_path_prefix", "/blog/")
    if not blog_prefix.startswith("/") or not blog_prefix.endswith("/"):
        raise ValueError("blog_path_prefix must look like /blog/")
    hostname = urlsplit(prop["site_base_url"]).hostname
    if not hostname:
        raise ValueError("Set a valid site_base_url")
    hostname = hostname.removeprefix("www.")
    blog_page_regex = r"^https?://(?:www\.)?" + re.escape(hostname) + re.escape(blog_prefix) + r".*"
    page_base = prop["site_base_url"].rstrip("/")
    property_id = prop["ga4_property_id"]
    site = prop["gsc_site_url"]
    if str(property_id).startswith("REPLACE_"):
        raise ValueError("Set GA4 property ID in config.yaml")

    verify_gsc_date(gsc, site, start, end)
    overall_gsc = aggregate(gsc_report(gsc, site, start, end, aggregation="byProperty"))
    branded = aggregate(gsc_report(gsc, site, start, end, filters=[gsc_filter("query", "includingRegex", regex)]))
    nonbranded = aggregate(gsc_report(gsc, site, start, end, filters=[gsc_filter("query", "excludingRegex", regex)]))
    total_sessions = ga4_report(ga, property_id, start, end, "sessions").get("__total__", 0)
    signup_event = cfg["report"]["signup_event_name"]
    total_signups = ga4_report(ga, property_id, start, end, "eventCount", signup_event=signup_event).get("__total__", 0)

    vals = dict(zip(range(3, 14), [total_sessions, nonbranded["impressions"], nonbranded["clicks"],
        nonbranded["ctr"], overall_gsc["impressions"], overall_gsc["clicks"], overall_gsc["ctr"],
        branded["impressions"], branded["clicks"], branded["ctr"], total_signups]))
    landing_dim = cfg["report"]["ga4_landing_page_dimension"]
    session_map = ga4_report(ga, property_id, start, end, "sessions", group_dimension=landing_dim)
    signup_map = ga4_report(ga, property_id, start, end, "eventCount", group_dimension=landing_dim,
                            signup_event=signup_event)
    sessions = {}
    signups = {}
    for key, n in session_map.items():
        p = normalize_landing(key)
        sessions[p] = sessions.get(p, 0) + n
    for key, n in signup_map.items():
        p = normalize_landing(key)
        signups[p] = signups.get(p, 0) + n
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
    if cfg["report"]["week_start"].lower() != "monday":
        raise ValueError("This version is configured for Monday-Sunday reporting only")
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
