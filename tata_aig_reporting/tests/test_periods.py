"""Dependency-free tests for pure date and slug functions."""
import ast
import calendar
from datetime import date,timedelta
from pathlib import Path
source=Path(__file__).resolve().parents[1]/'tata_aig'/'metrics.py'
tree=ast.parse(source.read_text())
names={'week_period','next_week','month_end','next_month','period_for','ga_landing_match'}
subset=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])
ns={'date':date,'timedelta':timedelta,'calendar':calendar}
exec(compile(subset,str(source),'exec'),ns)
wp=ns['week_period']; nw=ns['next_week']; pf=ns['period_for']; gm=ns['ga_landing_match']
assert wp(date(2026,10,29)) == (date(2026,10,29),date(2026,10,31))
assert wp(date(2026,11,29)) == (date(2026,11,29),date(2026,11,30))
assert nw(date(2026,10,29)) == date(2026,11,1)
assert wp(date(2028,2,29)) == (date(2028,2,29),date(2028,2,29))
assert gm('/motor-insurance/car-insurance/plan/','/motor-insurance/car-insurance')
assert not gm('/motor-insurance/car-insurance/?utm_source=abc','/motor-insurance/car-insurance')
assert not gm('/motor-insurance/car-insurance-plus','/motor-insurance/car-insurance')
assert not gm('/knowledge-center/car-insurance/?ref=42','/knowledge-center/car-insurance/')
cfg={'report':{'first_week_start':'2026-10-01','first_month':'2026-10'}}
assert pf(cfg,'weekly',[])==(date(2026,10,1),date(2026,10,7))
assert pf(cfg,'monthly',[])==(date(2026,10,1),date(2026,10,31))
print('All date and page filtering tests passed')
