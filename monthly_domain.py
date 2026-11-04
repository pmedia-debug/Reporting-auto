"""Append-only monthly domain-wide SEO report, independent of weekly reports.

Reads existing brand config.yaml and existing brand main.py API helpers.
No historical cells are cleared or overwritten.
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
    """Full-month queries, not the sum of the weekly report's CTR rows."""
    prop=cfg['properties']
    regex=cfg['filters']['branded_query_regex']
    if not regex or str(regex).startswith('REPLACE_'):
        raise ValueError('Brand regex not configured')
    event=cfg['report']['signup_event_name']
    if not event or str(event).startswith('REPLACE_'):
        raise ValueError('Overall GA4 conversion event not configured')
    site=prop['gsc_site_url']; pid=prop['ga4_property_id']
    api.verify_gsc_date(gsc,site,start,end)
    total=api.aggregate(api.gsc_report(gsc,site,start,end,aggregation='byProperty'))
    branded=api.aggregate(api.gsc_report(gsc,site,start,end,
        filters=[api.gsc_filter('query','includingRegex',regex)]))
    nonbrand=api.aggregate(api.gsc_report(gsc,site,start,end,
        filters=[api.gsc_filter('query','excludingRegex',regex)]))
    # Both GA4 requests are session-channel Organic Search only, via main.ga4_report.
    sessions=api.ga4_report(ga,pid,start,end,'sessions').get('__total__',0)
    conversions=api.ga4_report(ga,pid,start,end,'eventCount',signup_event=event).get('__total__',0)
    return {
        'organic_sessions':sessions,
        'nonbrand_impressions':nonbrand['impressions'],
        'nonbrand_clicks':nonbrand['clicks'],
        'nonbrand_ctr':nonbrand['ctr'],
        'overall_impressions':total['impressions'],
        'overall_clicks':total['clicks'],
        'overall_ctr':total['ctr'],
        'brand_impressions':branded['impressions'],
        'brand_clicks':branded['clicks'],
        'brand_ctr':branded['ctr'],
        'conversions':conversions,
    }

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
    mapping=mc.get('metric_rows',DEFAULT_ROWS)
    if set(mapping)!=set(DEFAULT_ROWS) or len(set(int(x) for x in mapping.values()))!=11:
        raise ValueError('monthly_domain.metric_rows requires 11 unique overall KPI rows')
    mapping={k:int(v) for k,v in mapping.items()}
    if any(r<=0 or r==header_row for r in mapping.values()):
        raise ValueError('Monthly metric rows must be positive and not equal the header row')
    sid_env=cfg.get('spreadsheet_id_env','SPREADSHEET_ID')
    sid=os.environ.get(sid_env,'').strip()
    if not sid:
        raise ValueError(f'Missing spreadsheet ID environment variable {sid_env}')
    creds=api.creds()
    sheets=build('sheets','v4',credentials=creds,cache_discovery=False)
    first_col=resolve_first_month_column(sheets,sid,tab,header_row,mc)
    # Validate actual tab and row-label layout before calculating any metrics.
    assert_layout(sheets,sid,tab,mapping)
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
    entries += [{'range':f'{api.quote_tab(tab)}!{col}{mapping[k]}','values':[[v]]} for k,v in values.items()]
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
