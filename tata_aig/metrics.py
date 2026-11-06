"""Shared Tata AIG GA4/GSC query and append-only Sheets reporting engine."""
from __future__ import annotations
import calendar
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import yaml
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest, Filter, FilterExpression, FilterExpressionList

LOG = logging.getLogger('tata_aig')
SCOPES = ['https://www.googleapis.com/auth/analytics.readonly',
          'https://www.googleapis.com/auth/webmasters.readonly',
          'https://www.googleapis.com/auth/spreadsheets']

# Exact user-provided order. Default rows 3..34, editable via metric_start_row.
LABELS = [
    'Organic Sessions - GA (D)',
    'Impressions - GSC (non blog)', 'Clicks - GSC (non blog)', 'CTR - GSC (non blog)',
    '"Car Insurance" - Impressions (Overall) - GSC', '"Car Insurance" - Clicks - GSC', '"Car Insurance" - CTR - GSC',
    'TATA AIG Car Insurance - Impressions (Overall) - GSC', 'TATA AIG Car Insurance - Clicks - GSC', 'TATA AIG Car Insurance - CTR - GSC',
    'AMP Impressions - GSC', 'AMP Clicks - GSC', 'CTR - GSC [AMP]',
    'Rich Snippet Impressions (without AMP)', 'Rich Snippet Clicks (without AMP)', 'Rich Snippet CTR (without AMP)',
    'Mobile Impressions - GSC', 'Mobile Clicks - GSC', 'CTR - GSC [Mobile]',
    'Desktop Impressions - GSC', 'Desktop Clicks - GSC', 'CTR - GSC [Desktop]',
    '"Tata|AIG" Brand Impressions - GSC', 'Brand Clicks - GSC', 'CTR - GSC [Brand]',
    'Non Brand Impressions - GSC', 'Non Brand Clicks - GSC', 'CTR - GSC [Non Brand]',
    'Blog Impressions GSC', 'Blog Clicks GSC', 'CTR - GSC [Blog]', 'Blog Organic Sessions GA'
]

def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(cfg, dict): raise ValueError('Invalid YAML config')
    p, r, s = (cfg[k] for k in ('properties','report','segments'))
    for k in ('ga4_property_id','gsc_site_url','site_base_url'):
        if not str(p.get(k, '')).strip() or str(p[k]).startswith('REPLACE_'):
            raise ValueError(f'Configure properties.{k} in {path}')
    if not str(s.get('amp_scope','')).lower() in ('support','blog','property'):
        raise ValueError('Choose segments.amp_scope = support, blog or property; GSC scope was not specified')
    if not str(r.get('label_column','')).strip() and r.get('label_column') is not None:
        raise ValueError('Set label_column to an actual column or null')
    if len(LABELS) != 32: raise AssertionError('Wrong KPI count')
    return cfg

def creds():
    raw = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if not raw: raise RuntimeError('Missing GOOGLE_CREDENTIALS_JSON GitHub secret')
    return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)

def column(n):
    s = ''
    while n:
        n, rem = divmod(n-1, 26)
        s = chr(65+rem)+s
    return s

def column_number(text):
    n = 0
    for c in text.upper():
        if c < 'A' or c > 'Z': raise ValueError(f'Invalid column: {text}')
        n = n*26+ord(c)-64
    return n

def quote_tab(s): return "'" + s.replace("'", "''") + "'"

def month_end(d): return date(d.year,d.month,calendar.monthrange(d.year,d.month)[1])

def next_month(d):
    return (month_end(d)+timedelta(days=1)).replace(day=1)

def week_period(start):
    if start.day not in (1,8,15,22,29): raise ValueError(f'Bad period start {start}')
    return start, min(start+timedelta(days=6), month_end(start))

def next_week(start):
    return week_period(start)[1]+timedelta(days=1)

def parse_period(start_str,end_str):
    start,end = date.fromisoformat(start_str),date.fromisoformat(end_str)
    if end < start: raise ValueError('End before start')
    return start,end

def period_for(cfg, cadence, completed):
    r=cfg['report']
    if cadence=='weekly':
        start=date.fromisoformat(r['first_week_start'])
        if completed:
            expected=start
            for a,b in completed:
                if a!=expected or week_period(a)[1]!=b: raise RuntimeError('Historical weekly headers have a gap or wrong period')
                expected=next_week(a)
            start=expected
        return week_period(start)
    start=date.fromisoformat(r['first_month']+'-01')
    if completed:
        expected=start
        for a,b in completed:
            if a!=expected or month_end(a)!=b: raise RuntimeError('Historical monthly headers have a gap or wrong period')
            expected=next_month(a)
        start=expected
    return start,month_end(start)

def period_key(start,end): return f'{start.isoformat()} to {end.isoformat()}'

def get_headers(sheets,sid,tab,r):
    first=column_number(r['first_column'])
    result=sheets.spreadsheets().values().get(spreadsheetId=sid,
       range=f'{quote_tab(tab)}!{column(first)}{r["header_row"]}:ZZ{r["header_row"]}',
       valueRenderOption='FORMATTED_VALUE').execute()
    vals=result.get('values',[[]])[0]
    filled=[(i,v.strip()) for i,v in enumerate(vals) if str(v).strip()]
    if filled and [i for i,_ in filled]!=list(range(filled[-1][0]+1)):
        raise RuntimeError('Blank gap in reporting headers; refusing to select a column')
    periods=[]
    for _,header in filled:
        m=re.fullmatch(r'(\d{4}-\d\d-\d\d) to (\d{4}-\d\d-\d\d)',header)
        if not m: raise RuntimeError(f'Unexpected date header: {header!r}; expected YYYY-MM-DD to YYYY-MM-DD')
        periods.append(parse_period(*m.groups()))
    return periods, column(first+len(filled))

def ensure_empty_and_labels(sheets,sid,tab,col,cfg,dry_run):
    r=cfg['report']; first=int(r['metric_start_row']); last=first+len(LABELS)-1
    ranges=[f'{quote_tab(tab)}!{col}{r["header_row"]}',f'{quote_tab(tab)}!{col}{first}:{col}{last}']
    result=sheets.spreadsheets().values().batchGet(spreadsheetId=sid,ranges=ranges,
                                                   valueRenderOption='FORMULA').execute()
    if any(x.get('values') for x in result.get('valueRanges',[])):
        raise RuntimeError(f'Column {col} already has values in the target cells; refusing to overwrite')
    label_col=r.get('label_column')
    if label_col is not None:
        lookup=sheets.spreadsheets().values().get(spreadsheetId=sid,
                range=f'{quote_tab(tab)}!{label_col}{first}:{label_col}{last}').execute()
        rows=lookup.get('values',[])
        if len(rows)<len(LABELS):
            raise RuntimeError('Reporting labels missing from expected rows; verify label_column and metric_start_row')
        for idx,(found,expected) in enumerate(zip(rows,LABELS),start=first):
            if not found or not str(found[0]).strip():
                raise RuntimeError(f'Empty metric label at {label_col}{idx}; refusing write')
        # Label presence checked; exact wording differs in user's sheet and is not required.

def make_filter(dim, operator, expression):
    return {'dimension':dim,'operator':operator,'expression':expression}

def path_regex(base,path):
    host=urlsplit(base).hostname
    if not host: raise ValueError('Bad site_base_url')
    host_re=re.escape(host)
    if host.startswith('www.'):
        host_re=r'(?:www\.)?'+re.escape(host[4:])
    clean='/'+path.strip('/')
    return rf'^https?://{host_re}{re.escape(clean)}(?:/|\?|$)'

def ga_landing_match(value,prefix):
    # Reject parameterized URLs, fragments, partial/sloppy slug matches.
    if not isinstance(value,str) or not value.startswith('/') or '?' in value or '#' in value:
        return False
    norm=prefix.rstrip('/')
    return value == norm or value.startswith(norm + '/')

def ga_sessions(client,property_id,start,end,path):
    filter_expr=FilterExpression(filter=Filter(field_name='sessionDefaultChannelGroup',
        string_filter=Filter.StringFilter(match_type=Filter.StringFilter.MatchType.EXACT,value='Organic Search')))
    req=RunReportRequest(property=f'properties/{property_id}',
        date_ranges=[DateRange(start_date=str(start),end_date=str(end))],
        dimensions=[Dimension(name='landingPagePlusQueryString')],metrics=[Metric(name='sessions')],
        dimension_filter=filter_expr,limit=100000)
    res=client.run_report(req)
    if res.row_count>len(res.rows): raise RuntimeError('GA4 result truncated; implement GA4 pagination')
    return sum(int(float(row.metric_values[0].value)) for row in res.rows
               if ga_landing_match(row.dimension_values[0].value,path))

def gsc_query(client,site,start,end,filters=None,dimensions=None,aggregation='auto'):
    body={'startDate':str(start),'endDate':str(end),'type':'web',
          'dataState':'final','dimensions':dimensions or [],'aggregationType':aggregation,'rowLimit':25000}
    if filters: body['dimensionFilterGroups']=[{'groupType':'and','filters':filters}]
    rows=[]; offset=0
    while True:
        body['startRow']=offset
        result=client.searchanalytics().query(siteUrl=site,body=body).execute()
        batch=result.get('rows',[]); rows.extend(batch)
        if len(batch)<25000: break
        offset+=25000
        if offset>=50000: raise RuntimeError('GSC returned 50K rows; increase granularity to avoid truncated data')
    return rows

def agg(rows):
    clicks=sum(float(row.get('clicks',0)) for row in rows)
    impressions=sum(float(row.get('impressions',0)) for row in rows)
    return [impressions,clicks,clicks/impressions if impressions else 0.0]

def gsc_metrics(client,site,start,end,filters=None,aggregation='auto'):
    return agg(gsc_query(client,site,start,end,filters,aggregation=aggregation))

def require_gsc_final(client,site,start,end):
    rows=gsc_query(client,site,start,end,dimensions=['date'])
    found={r['keys'][0] for r in rows}
    days={(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)}
    if not days.issubset(found): raise RuntimeError('GSC final data missing dates: '+str(sorted(days-found)))

def compute(cfg,ga,gsc,start,end):
    p=cfg['properties']; s=cfg['segments']; site=p['gsc_site_url']; base=p['site_base_url']
    require_gsc_final(gsc,site,start,end)
    support=make_filter('page','includingRegex',path_regex(base,s['support_path']))
    blog=make_filter('page','includingRegex',path_regex(base,s['blog_path']))
    support_filters=[support]; blog_filters=[blog]
    data=[]
    data.append(ga_sessions(ga,p['ga4_property_id'],start,end,s['support_path']))
    data+=gsc_metrics(gsc,site,start,end,support_filters)
    data+=gsc_metrics(gsc,site,start,end,[make_filter('query','includingRegex',s['query_car_insurance_regex'])],aggregation='byProperty')
    data+=gsc_metrics(gsc,site,start,end,[make_filter('query','includingRegex',s['query_tata_aig_car_insurance_regex'])],aggregation='byProperty')
    amp_scope=s['amp_scope'].lower(); amp_filters={'support':[support], 'blog':[blog], 'property':[]}[amp_scope]
    amp_filters=amp_filters+[make_filter('searchAppearance','equals',s['amp_appearance'])]
    data+=gsc_metrics(gsc,site,start,end,amp_filters)
    review_filters=[support,make_filter('searchAppearance','equals',s['review_appearance']),
                    make_filter('searchAppearance','notEquals',s['review_exclude_amp_appearance'])]
    data+=gsc_metrics(gsc,site,start,end,review_filters)
    data+=gsc_metrics(gsc,site,start,end,[support,make_filter('device','equals','MOBILE')])
    data+=gsc_metrics(gsc,site,start,end,[support,make_filter('device','equals','DESKTOP')])
    data+=gsc_metrics(gsc,site,start,end,[support,make_filter('query','includingRegex',s['brand_regex'])])
    data+=gsc_metrics(gsc,site,start,end,[support,make_filter('query','excludingRegex',s['brand_regex'])])
    data+=gsc_metrics(gsc,site,start,end,blog_filters)
    data.append(ga_sessions(ga,p['ga4_property_id'],start,end,s['blog_path']))
    if len(data)!=len(LABELS): raise AssertionError(f'Expected 32 KPI values, got {len(data)}')
    return data

def run(cadence,config_path,dry_run,today_override=None):
    cfg=load_config(config_path); r=cfg['report']; sid=os.getenv(r['sheet_id_env'])
    if not sid: raise RuntimeError(f'Missing {r["sheet_id_env"]} GitHub secret')
    if not dry_run and not r.get('approved_layout', False):
        raise RuntimeError('Live write blocked: verify Sheet row positions, then set report.approved_layout: true')
    if not dry_run and not r.get('approved_special_filters', False):
        raise RuntimeError('Live write blocked: confirm AMP and review-without-AMP filter semantics first')
    today=date.fromisoformat(today_override) if today_override else datetime.now(ZoneInfo(r['timezone'])).date()
    auth=creds(); sheets=build('sheets','v4',credentials=auth,cache_discovery=False)
    tab=r[f'{cadence}_tab']
    completed,col=get_headers(sheets,sid,tab,r)
    start,end=period_for(cfg,cadence,completed)
    if end+timedelta(days=int(r['min_days_after_end']))>today:
        LOG.info('%s not eligible yet: %s (today %s)',cadence,period_key(start,end),today)
        return
    LOG.info('%s: %s -> %s!%s (%d KPIs), dry_run=%s',cadence,period_key(start,end),tab,col,len(LABELS),dry_run)
    ensure_empty_and_labels(sheets,sid,tab,col,cfg,dry_run)
    gsc=build('searchconsole','v1',credentials=auth,cache_discovery=False)
    ga=BetaAnalyticsDataClient(credentials=auth)
    vals=compute(cfg,ga,gsc,start,end)
    # Recheck destination before writing. GitHub concurrency must also be enabled.
    _,still_col=get_headers(sheets,sid,tab,r)
    if still_col!=col: raise RuntimeError('Sheet changed while generating report; refusing overwrite')
    ensure_empty_and_labels(sheets,sid,tab,col,cfg,dry_run)
    if dry_run:
        for idx,(label,val) in enumerate(zip(LABELS,vals),start=int(r['metric_start_row'])):
            LOG.info('PREVIEW %s!%s%d: %s = %s',tab,col,idx,label,val)
        LOG.info('DRY RUN: no Sheet values written')
        return
    entries=[{'range':f'{quote_tab(tab)}!{col}{r["header_row"]}','values':[[period_key(start,end)]]}]
    for row,val in enumerate(vals,start=int(r['metric_start_row'])):
        entries.append({'range':f'{quote_tab(tab)}!{col}{row}','values':[[val]]})
    result=sheets.spreadsheets().values().batchUpdate(spreadsheetId=sid,body={
        'valueInputOption':'RAW','data':entries}).execute()
    expected=len(entries)
    if result.get('totalUpdatedCells')!=expected:
        raise RuntimeError(f'Unexpected updated-cell count {result.get("totalUpdatedCells")}, expected {expected}')
    LOG.info('SUCCESS: wrote %s, %d KPI values to %s!%s',period_key(start,end),len(vals),tab,col)
