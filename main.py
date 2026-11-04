    if not raw:
        raise ValueError("GOOGLE_CREDENTIALS_JSON secret is missing")
    return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)


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
    """Most recent eligible calendar-month reporting block (1-7, 8-14, etc.)."""
    today = date.fromisoformat(date_override) if date_override else datetime.now(ZoneInfo(tz)).date()
    cutoff = today - timedelta(days=int(min_delay))
    final_day = monthrange(cutoff.year, cutoff.month)[1]
    periods = [(1, 7), (8, 14), (15, 21), (22, 28)]
    if final_day >= 29:
        periods.append((29, final_day))
    eligible = [(a, b) for a, b in periods if b <= cutoff.day]
    if eligible:
        first, last = eligible[-1]
        year, month = cutoff.year, cutoff.month
    else:
        prev = cutoff.replace(day=1) - timedelta(days=1)
        year, month = prev.year, prev.month
        first, last = (29, prev.day) if prev.day >= 29 else (22, prev.day)
    return date(year, month, first).isoformat(), date(year, month, last).isoformat()


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
    expected = {(date.fromisoformat(start) + timedelta(days=i)).isoformat()
                for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)}
    if not expected.issubset(dates):
        raise RuntimeError(f"Finalized GSC data missing dates: {sorted(expected - dates)}")


def ga_filter(*items):
    return FilterExpression(and_group=FilterExpressionList(expressions=list(items)))


def ga_exact(field, value):
    return FilterExpression(filter=Filter(field_name=field, string_filter=Filter.StringFilter(
        match_type=Filter.StringFilter.MatchType.EXACT, value=value)))


def ga4_report(client, property_id, start, end, metric, group_dimension=None, signup_event=None):
    # REQUIRED: filter sessions and events to Organic Search only.
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


def exact_landing(value):
    """Accept only an exact GA4 landing path without parameters or fragments.

    Do not normalize query-string URLs to their bare page paths: the report
    requires exact provided landing-page slugs only.
    """
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    if "?" in value or "#" in value:
        return None
    return value


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
