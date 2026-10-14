import argparse,json,sys,sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime,timezone
from .store import Store
from .ingest import preview_csv,to_csv
from .demo import demo_csv
from .providers import ProviderError
from .eodhd import EODHD
from .server import make_server
from .analysis import radar
from .calendars import calendar_status,parse_override
from .models import instant
from .contracts import ACTIVE_MARKETS,active_market

@contextmanager
def capture_receipts(store,provider,name):
    """Keep received evidence even when a later page/permission/parse fails."""
    try:yield provider
    finally:
        if provider.client.receipts:
            recorded=datetime.now(timezone.utc)
            for receipt in provider.client.receipts:
                store.save_payload(name,receipt['url'],receipt,recorded)

def main(argv=None):
    p=argparse.ArgumentParser(description='Private AU Short Radar local research app')
    p.add_argument('--db',default='runtime/radar.sqlite3');subs=p.add_subparsers(dest='command',required=True)
    subs.add_parser('demo');subs.add_parser('status')
    serve=subs.add_parser('serve');serve.add_argument('--port',type=int,default=8765)
    imp=subs.add_parser('import-csv');imp.add_argument('file');imp.add_argument('--confirm',action='store_true');imp.add_argument('--allow-rejections',action='store_true')
    au=subs.add_parser('asx-eod');au.add_argument('--symbols',required=True);au.add_argument('--start',required=True);au.add_argument('--end',required=True);au.add_argument('--max-requests',type=int,default=8)
    cal=subs.add_parser('calendar');cal.add_argument('--market',choices=['AU'],default='AU');cal.add_argument('--asof')
    override=subs.add_parser('calendar-override');override.add_argument('file');override.add_argument('--confirm',action='store_true')
    scan=subs.add_parser('scan');scan.add_argument('--mode',choices=['demo','imported'],default='demo');scan.add_argument('--horizon',type=int,choices=[5,10,20],default=10);scan.add_argument('--output')
    args=p.parse_args(argv);now=datetime.now(timezone.utc)
    try:
        store=Store(args.db)
        if args.command=='demo':
            preview=preview_csv(demo_csv(),datetime.now(timezone.utc),'demo');result=store.commit_preview(preview,datetime.now(timezone.utc));print(json.dumps({'mode':'demo',**result},ensure_ascii=False))
        elif args.command=='import-csv':
            text=Path(args.file).read_text(encoding='utf-8-sig');received=datetime.now(timezone.utc);preview=preview_csv(text,received,calendar_overrides=store.calendar_overrides(received,ACTIVE_MARKETS),allowed_markets=ACTIVE_MARKETS);print(json.dumps(preview.summary(),ensure_ascii=False,indent=2))
            if args.confirm:print(json.dumps(store.commit_preview(preview,datetime.now(timezone.utc),args.allow_rejections)))
        elif args.command=='asx-eod':
            provider=EODHD(max_requests=args.max_requests)
            with capture_receipts(store,provider,'eodhd'):
                rows=provider.daily_rows(args.symbols.split(','),args.start,args.end,now)
            received=datetime.now(timezone.utc)
            preview=preview_csv(to_csv(rows),received,calendar_overrides=store.calendar_overrides(received,ACTIVE_MARKETS),allowed_markets=ACTIVE_MARKETS);print(json.dumps(preview.summary(),ensure_ascii=False,indent=2));print(json.dumps(store.commit_preview(preview,datetime.now(timezone.utc))))
        elif args.command=='status':print(json.dumps(store.status(ACTIVE_MARKETS),indent=2))
        elif args.command=='calendar':
            cutoff=instant(args.asof) if args.asof else now
            if cutoff>now:raise ValueError('asof is in the future')
            changes=store.calendar_overrides(cutoff,ACTIVE_MARKETS)
            print(json.dumps({m:calendar_status(m,cutoff,changes) for m in ACTIVE_MARKETS},ensure_ascii=False,indent=2))
        elif args.command=='calendar-override':
            raw=Path(args.file).read_text(encoding='utf8')
            normalized=parse_override(raw,now)
            active_market(normalized['market'])
            print(json.dumps({'status':'review_required','override':normalized},ensure_ascii=False,indent=2))
            if args.confirm:print(json.dumps(store.save_calendar_override(raw,datetime.now(timezone.utc))))
        elif args.command=='scan':
            payload=json.dumps(radar(store,args.mode,now,args.horizon),ensure_ascii=False,indent=2)
            if args.output:Path(args.output).write_text(payload,encoding='utf8')
            else:print(payload)
        elif args.command=='serve':
            server=make_server(store,args.port);print(f'Private local development server: http://127.0.0.1:{server.server_port}/',flush=True)
            try:server.serve_forever()
            except KeyboardInterrupt:pass
            finally:server.server_close()
        return 0
    except (ValueError,ProviderError,OSError,sqlite3.DatabaseError) as e:
        if isinstance(e,ProviderError):
            print(json.dumps({'error':str(e),'code':e.code,'retry_after_seconds':e.retry_after}),file=sys.stderr)
        else:print('Error: '+str(e),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
