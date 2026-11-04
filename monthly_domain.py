"""Full-month SEO reporting: overview AND every page KPI in the weekly layout.

Uses existing main.py API queries and strict Organic Search landing-page filters.
Preserves the independent monthly tab and all previously reported columns.
"""
from __future__ import annotations
import argparse
from calendar import monthrange
from datetime import date, datetime, timedelta
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient
import main as api

LOG = logging.getLogger("monthly_domain")
DEFAULT_ROWS = dict(zip([
    "organic_sessions", "nonbrand_impressions", "nonbrand_clicks", "nonbrand_ctr",
    "overall_impressions", "overall_clicks", "overall_ctr", "brand_impressions",
    "brand_clicks", "brand_ctr", "conversions"
], range(3, 14)))

# Allowed label variants. Labels must be in columns A-K at the corresponding rows.
LABELS = {
    "organic_sessions": ("overall organic traffic", "organic sessions", "organic traffic"),
    "nonbrand_impressions": ("non-brand impressions", "non brand impressions", "nonbranded impressions"),
    "nonbrand_clicks": ("non-brand clicks", "non brand clicks", "nonbranded clicks"),
    "nonbrand_ctr": ("non-brand ctr", "non brand ctr", "nonbranded ctr"),
    "overall_impressions": ("overall impressions", "total impressions"),
    "overall_clicks": ("overall clicks", "total clicks"),
    "overall_ctr": ("overall ctr", "total ctr"),
    "brand_impressions": ("brand impressions", "branded impressions"),
    "brand_clicks": ("brand clicks", "branded clicks"),
    "brand_ctr": ("brand ctr", "branded ctr"),
    "conversions": ("signups", "signup", "sign up", "free trial", "free_trial", "purchase", "purchases", "conversions"),
}

def ym_next(y, m):
    return (y + 1, 1) if m == 12 else (y, m + 1)

def month_bounds(y, m):
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])

def month_key(y, m):
    a,b=month_bounds(y,m)
    return f"{a.isoformat()} to {b.isoformat()}"

def eligible_date(cfg, today):
    return today - timedelta(days=int(cfg['report'].get('min_days_after_week_end', 3)))

def next_missing_month(sheet, sid, tab, header_row, first_column, first_month, cutoff):
    """Require one contiguous sequence of month headers beginning October 2026."""
    col_index=api.col_num(first_column)
    raw=sheet.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"{api.quote_tab(tab)}!{first_column}{header_row}:ZZ{header_row}",
        valueRenderOption='FORMATTED_VALUE'
    ).execute().get('values', [[]])[0]
    existing=[str(v).strip() for v in raw]
    y,m=(int(v) for v in first_month.split('-'))
    next_offset=len(existing)
    for i,label in enumerate(existing):
        if not label:
            # Any populated header after a gap is unsafe.
            if any(str(v).strip() for v in existing[i+1:]):
                raise RuntimeError(f"Gap in monthly headers at {api.col_name(col_index+i)}")
            next_offset=i
            break
        expected=month_key(y,m)
        if label != expected:
            raise RuntimeError(f"Monthly header mismatch at {api.col_name(col_index+i)}: found {label!r}; expected {expected!r}. Do not overwrite or relabel historical data automatically")
        y,m=ym_next(y,m)
    _,end=month_bounds(y,m)
    if end > cutoff:
        return None
    return y,m,api.col_name(col_index+next_offset)

def resolve_first_month_column(sheet, sid, tab, header_row, mc):
    explicit=mc.get('first_month_column', 'AUTO')
    if str(explicit).upper() != 'AUTO':
        return str(explicit).upper()
    anchor=str(mc.get('anchor_column','L')).upper()
    result=sheet.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"{api.quote_tab(tab)}!{anchor}{header_row}",
        valueRenderOption='FORMATTED_VALUE'
    ).execute().get('values',[[]])
    label=str(result[0][0]).strip() if result and result[0] else ''
    if not label:
        return anchor
    normalized=label.casefold()
    import re as _re
    # September has already been recorded in the anchor column.
    if ('sep' in normalized or '2026-09' in normalized
        or bool(_re.search(r'(?<!\d)09[/-]2026(?!\d)',normalized))):
        return api.col_name(api.col_num(anchor)+1)
    raise RuntimeError(
        f"Cannot identify prior month in {tab}!{anchor}{header_row}: {label!r}. "
        "Set monthly_domain.first_month_column explicitly after verifying the sheet"
    )

def assert_layout(sheet, sid, tab, row_mapping):
    """Never assume monthly-domain tab matches weekly template: validate labels first."""
    errors=[]
    for key,row in row_mapping.items():
        got=sheet.spreadsheets().values().get(
            spreadsheetId=sid,
            range=f"{api.quote_tab(tab)}!A{row}:K{row}",
            valueRenderOption='FORMATTED_VALUE'
        ).execute().get('values', [[]])[0]
        labels=[str(x).strip().casefold().replace('–','-').replace('—','-') for x in got]
        if not any(x in LABELS[key] for x in labels):
            errors.append(f"row {row} ({key}): labels in A:K were {got!r}")
    if errors:
        raise RuntimeError("Monthly domain tab layout not verified. " + '; '.join(errors) + ". Set monthly_domain.metric_rows to actual rows or adjust labels; no values written")

def monthly_values(cfg, ga, gsc, start, end):
    """Calculate ALL weekly-template metrics over the full calendar month.

    Uses exactly the same Organic Search, exact landing URL, signup event,
    branded-query, page/canonical and blog-prefix filters as main.py.
    """
    return api.planned_values(cfg, ga, gsc, start, end)


def assert_page_layout(sheet, sid, tab, cfg):
    """Verify the monthly sheet has the same page and KPI row positions.

    Reads labels only in A:K; avoids touching date or KPI cells.
    Rejects a different layout rather than silently writing the wrong rows.
    """
    pages = cfg["pages"]
    for page in pages:
        heading = int(page["heading_row"])
        expected = str(page["label"]).strip().casefold()
        # Restrict to labels/URLs, never read existing monthly KPI data.
        result = sheet.spreadsheets().values().get(
            spreadsheetId=sid,
            range=f"{api.quote_tab(tab)}!A{heading}:K{heading+5}",
            valueRenderOption="FORMATTED_VALUE",
        ).execute().get("values", [])
        label_row = result[0] if result else []
        normalized = [str(v).strip().casefold() for v in label_row]
        if expected not in normalized:
            raise RuntimeError(
                f"Monthly tab page heading mismatch at row {heading}: "
                f"expected {page['label']!r}, found {label_row!r}. "
                "Verify that monthly domain has the same row mapping as Sheet1."
            )
        expected_metrics = ["sessions", "impressions", "clicks", "ctr", "sign up"]
        for offset, expected_metric in enumerate(expected_metrics, start=1):
            row_values = result[offset] if len(result) > offset else []
            labels = [str(v).strip().casefold() for v in row_values]
            allowed = ("sign up", "signup", "signups", "free trial", "purchase") if offset == 5 else (expected_metric,)
            if not any(v in allowed for v in labels):
                raise RuntimeError(
                    f"Monthly tab KPI label mismatch at row {heading+offset}: "
                    f"expected {expected_metric!r}, found {row_values!r}. "
                    "No data was written."
                )


def assert_blank(sheet, sid, tab, col, header_row, mapping):
    ranges=[f"{api.quote_tab(tab)}!{col}{r}" for r in [header_row,*mapping.values()]]
    result=sheet.spreadsheets().values().batchGet(
        spreadsheetId=sid,ranges=ranges,valueRenderOption='FORMULA'
    ).execute()
    occupied=[x['range'] for x in result.get('valueRanges',[]) if x.get('values')]
    if occupied:
        raise RuntimeError(f"Refusing to overwrite existing monthly data in {occupied[:5]}")

def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--today', help='Simulation date, YYYY-MM-DD')
    parser.add_argument('--config',default=str(Path(__file__).with_name('config.yaml')))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    cfg=yaml.safe_load(Path(args.config).read_text(encoding='utf-8'))
    mc=cfg.get('monthly_domain',{})
    if not mc.get('enabled',False):
        LOG.info('Monthly report disabled in config.yaml; skipping')
        return
    tab=mc.get('sheet_tab','monthly domain')
    header_row=int(mc.get('date_header_row',2))
    first_month=mc.get('first_month','2026-10')
    overview_mapping=mc.get('metric_rows',DEFAULT_ROWS)
    if set(overview_mapping)!=set(DEFAULT_ROWS) or len(set(int(x) for x in overview_mapping.values()))!=11:
        raise ValueError('monthly_domain.metric_rows requires 11 unique overall KPI rows')
    overview_mapping={k:int(v) for k,v in overview_mapping.items()}
    if overview_mapping != DEFAULT_ROWS:
        raise ValueError('Full monthly template must match weekly overall rows 3-13')
    row_numbers=api.metric_rows(cfg)
    if header_row in row_numbers:
        raise ValueError('Monthly date header conflicts with a KPI row')
    mapping={str(n):n for n in row_numbers}
    sid_env=cfg.get('spreadsheet_id_env','SPREADSHEET_ID')
    sid=os.environ.get(sid_env,'').strip()
    if not sid:
        raise ValueError(f'Missing spreadsheet ID environment variable {sid_env}')
    creds=api.creds()
    sheets=build('sheets','v4',credentials=creds,cache_discovery=False)
    first_col=resolve_first_month_column(sheets,sid,tab,header_row,mc)
    # Validate actual tab and row-label layout before calculating any metrics.
    assert_layout(sheets,sid,tab,overview_mapping)
    assert_page_layout(sheets,sid,tab,cfg)
    today=date.fromisoformat(args.today) if args.today else datetime.now(ZoneInfo(cfg['report']['timezone'])).date()
    selected=next_missing_month(sheets,sid,tab,header_row,first_col,first_month,eligible_date(cfg,today))
    if selected is None:
        LOG.info('No eligible missing full month for %s',tab)
        return
    y,m,col=selected
    start,end=month_bounds(y,m)
    key=month_key(y,m)
    assert_blank(sheets,sid,tab,col,header_row,mapping)
    gsc=build('searchconsole','v1',credentials=creds,cache_discovery=False)
    ga=BetaAnalyticsDataClient(credentials=creds)
    values=monthly_values(cfg,ga,gsc,start.isoformat(),end.isoformat())
    # Revalidate headers and destination immediately before writing.
    again=next_missing_month(sheets,sid,tab,header_row,first_col,first_month,eligible_date(cfg,today))
    if again != selected:
        raise RuntimeError('Monthly destination changed during API extraction; aborting')
    assert_blank(sheets,sid,tab,col,header_row,mapping)
    entries=[{'range':f'{api.quote_tab(tab)}!{col}{header_row}','values':[[key]]}]
    if set(values) != set(row_numbers):
        raise RuntimeError('Monthly values do not match the full weekly KPI row mapping')
    entries += [{'range':f'{api.quote_tab(tab)}!{col}{r}','values':[[values[r]]]} for r in sorted(values)]
    LOG.info('%s MONTHLY %s; column %s; metrics %d; dry_run=%s',tab,key,col,len(values),args.dry_run)
    if args.dry_run:
        for x in entries:
            LOG.info('%s => %s',x['range'],x['values'][0][0])
        return
    result=sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sid,body={'valueInputOption':'RAW','data':entries}
    ).execute()
    if result.get('totalUpdatedCells') != len(entries):
        raise RuntimeError(f'Unexpected updated cell count: {result.get("totalUpdatedCells")}')
    LOG.info('MONTHLY SUCCESS: wrote %s to %s column %s',key,tab,col)

if __name__ == '__main__':
    run()
