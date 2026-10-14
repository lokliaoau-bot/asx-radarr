"""Loopback-only development HTTP server, not an internet production server."""
import json,mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs,unquote
from datetime import datetime,timezone
from .models import instant
from .analysis import radar
from . import __version__
from .calendars import calendar_status
from .contracts import API_PARAMS,ACTIVE_MARKETS,active_market

def make_server(store,port=8765,dist=None):
    dist=Path(dist or Path(__file__).resolve().parents[1]/'dist').resolve()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def send(self,status,body,ctype='application/json; charset=utf-8'):
            if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            if isinstance(body,str):body=body.encode()
            self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Cross-Origin-Resource-Policy','same-origin');self.end_headers();self.wfile.write(body)
        def do_POST(self):self.send(405,{'error':'read_only_api_use_local_cli_for_import'})
        def do_GET(self):
            expected={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in expected:self.send(403,{'error':'invalid_host'});return
            origin=self.headers.get('Origin')
            if origin and origin not in {'http://'+h for h in expected}:self.send(403,{'error':'cross_origin_blocked'});return
            if len(self.path)>4096:self.send(414,{'error':'url_too_long'});return
            try:
                parsed=urlparse(self.path)
                q=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=20)
                if parsed.path.startswith('/v1/'):
                    endpoint='/v1/instruments/{id}' if parsed.path.startswith('/v1/instruments/') else parsed.path
                    allowed=API_PARAMS.get(endpoint,frozenset())
                    if any(key not in allowed or len(values)!=1 or not values[0] and key!='query' for key,values in q.items()):
                        raise ValueError('invalid, repeated or blank API parameter')
                if parsed.path=='/v1/status':
                    self.send(200,{'version':__version__,'local_only':True,**store.status(ACTIVE_MARKETS),'providers':{'asx_eodhd':'candidate_credential_and_reconciliation_pending','asx_csv':'supported'},'ai':'not_integrated'});return
                if parsed.path in ('/v1/radar','/v1/coverage','/v1/calendars') or parsed.path.startswith('/v1/instruments/'):
                    mode=q.get('mode',['demo'])[0];cutoff=instant(q['asof'][0]) if 'asof' in q else datetime.now(timezone.utc)
                    if cutoff>datetime.now(timezone.utc):raise ValueError('asof is in the future')
                    if parsed.path=='/v1/calendars':
                        market=active_market(q.get('market',['AU'])[0])
                        changes=store.calendar_overrides(cutoff,ACTIVE_MARKETS)
                        self.send(200,{'asof':cutoff.isoformat(),'calendars':{market:calendar_status(market,cutoff,changes)}});return
                    data=radar(store,mode,cutoff,int(q.get('horizon',['10'])[0]),q.get('market',['AU'])[0],q.get('sector',['ALL'])[0],q.get('query',[''])[0])
                    if parsed.path=='/v1/coverage':self.send(200,{'mode':mode,'asof':data['asof'],'coverage':data['coverage'],'calendars':data['calendars']});return
                    if parsed.path.startswith('/v1/instruments/'):
                        iid=unquote(parsed.path.rsplit('/',1)[1]);row=next((r for r in data['rows'] if r['id']==iid),None)
                        self.send(200 if row else 404,row or {'error':'instrument_not_found'});return
                    self.send(200,data);return
                path=dist/('workbench.html' if parsed.path=='/' else unquote(parsed.path).lstrip('/'))
                path=path.resolve()
                if not path.is_relative_to(dist) or not path.is_file() or path.suffix.lower() not in {'.html','.js','.css','.json','.zip','.md'}:
                    self.send(404,{'error':'not_found'});return
                self.send(200,path.read_bytes(),mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
            except (ValueError,KeyError) as e:self.send(422,{'error':str(e)})
            except Exception:self.send(500,{'error':'internal_error_check_local_cli_diagnostics'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)
