"""Synthetic fixture generator; weekday sessions are not exchange calendars."""
import math
from datetime import datetime,timedelta,date,timezone
from .models import market_zone,stamp,MARKET_CURRENCY
from .ingest import to_csv

def demo_csv():
    end=date(2026,9,30);days=[];d=end
    while len(days)<260:
        if d.weekday()<5:days.append(d)
        d-=timedelta(days=1)
    days.reverse();rows=[]
    specs=[('AU','STW','STW · 模拟澳股基准','基准',75,.04),('AU','GYG','GYG · 模拟行情','餐饮',25,-.18),('AU','360','Life360 · 模拟行情','其他',50,-.14),('AU','CSL','CSL · 模拟行情','医药',180,-.09),('AU','GMG','Goodman · 模拟行情','REIT',30,-.11)]
    for seed,(market,symbol,name,sector,base,drift) in enumerate(specs,start=6):
        for i,day in enumerate(days):
            zone=market_zone(market);op=datetime.combine(day,datetime.min.time()).replace(hour=9,minute=59,tzinfo=zone);cl=op.replace(hour=16,minute=42)
            close=base*(1+drift*i/259+.015*math.sin((i+seed)*.5));open_price=close*(1+.002*math.cos(i))
            rows.append({'market':market,'symbol':symbol,'name':name,'sector':sector,'currency':MARKET_CURRENCY[market],'session_date':day.isoformat(),'open':f'{open_price:.6f}','high':f'{max(close,open_price)*1.01:.6f}','low':f'{min(close,open_price)*.99:.6f}','close':f'{close:.6f}','volume':1000000+int(300000*math.sin(i)**2),'volume_unit':'shares','session_open_utc':stamp(op),'session_close_utc':stamp(cl),'available_at':stamp(cl+timedelta(hours=4)),'source':'synthetic-v0.3','basis':'split','close_definition':'asx_eod','calendar_source':'synthetic-weekdays-not-exchange-calendar','calendar_verified':'true','demo':'true','incomplete':'false'})
    return to_csv(rows)
