"""Loopback-only display service. The only POST rebuilds a disposable snapshot."""
from __future__ import annotations

from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import UUID
import webbrowser

import account
import dashboard_data as data
import ledger_source


class Controller:
    def __init__(self,workbook,*,clock=time.monotonic,close_grace=5,lease_timeout=120,startup_timeout=60):
        self.workbook=Path(workbook).resolve()
        self.lock=threading.Lock();self.syncing=False;self.error=None;self.worker=None
        self.source_signature=None;self.source_hash=None
        self.clock=clock;self.close_grace=close_grace;self.lease_timeout=lease_timeout
        self.startup_deadline=clock()+startup_timeout
        self.clients={};self.closed_clients={};self.empty_since=None;self.closing=False

    def session(self,page_id,action):
        with self.lock:
            if self.closing:return False
            now=self.clock()
            self.closed_clients={key:expiry for key,expiry in self.closed_clients.items() if expiry>now}
            if action=='close':
                self.closed_clients[page_id]=now+self.lease_timeout
                self.clients.pop(page_id,None)
                if not self.clients and self.empty_since is None:self.empty_since=now
            elif page_id not in self.closed_clients:
                if len(self.clients)>=256 and page_id not in self.clients:return False
                self.clients[page_id]=now;self.empty_since=None
            return True

    def should_shutdown(self):
        with self.lock:
            now=self.clock()
            expired=[key for key,seen in self.clients.items() if now-seen>=self.lease_timeout]
            for key in expired:self.clients.pop(key)
            if expired and not self.clients:self.empty_since=now
            deadline=self.empty_since+self.close_grace if self.empty_since is not None else self.startup_deadline
            if not self.clients and not self.syncing and now>=deadline:self.closing=True
            return self.closing

    def status(self):
        snapshot=data.read_snapshot(self.workbook)
        with self.lock:
            source_error=None
            try:
                ledger_source.assert_active(self.workbook)
                info=self.workbook.stat()
                signature=(info.st_mtime_ns,info.st_size,info.st_ctime_ns)
                if signature!=self.source_signature or ledger_source.is_database(self.workbook):
                    self.source_hash=ledger_source.source_hash(self.workbook);self.source_signature=signature
                changed=not snapshot or self.source_hash!=snapshot.get('sourceHash')
            except OSError:
                changed=True
            except ValueError:
                changed=True
                source_error='账本来源已切换或不可用，请关闭所有看板页面后重新打开。'
            return dict(service='account-dashboard-v1',lifecycleVersion=1,closing=self.closing,hasSnapshot=bool(snapshot),generatedAt=snapshot.get('generatedAt') if snapshot else None,
                sourceChanged=changed,monthChanged=not snapshot or snapshot.get('calendarMonth')!=data.current_month(),
                calendarMonth=data.current_month(),today=datetime.now(account.TZ).date().isoformat(),
                syncing=self.syncing,error=source_error or self.error)

    def start_sync(self):
        with self.lock:
            if self.syncing or self.closing:return False
            self.syncing=True;self.error=None
            self.worker=threading.Thread(target=self._sync,name='dashboard-sync',daemon=True)
            self.worker.start()
            return True

    def _sync(self):
        error=None
        try:data.sync_snapshot(self.workbook)
        except Exception as exc:error=str(exc)
        finally:
            with self.lock:self.error=error;self.syncing=False

    def wait_for_sync(self,timeout=None):
        if self.worker:self.worker.join(timeout)


class Handler(BaseHTTPRequestHandler):
    def __init__(self,*args,dist,**kwargs):
        self.dist=Path(dist).resolve()
        super().__init__(*args,**kwargs)

    def log_message(self,*args):
        pass  # Do not log transaction payloads or private query strings.

    def allowed_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')

    def respond(self,status,body,content_type='application/json; charset=utf-8'):
        if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers();self.wfile.write(body)

    def do_GET(self):
        if not self.allowed_host():return self.respond(403,{'error':'只允许本机访问'})
        path=unquote(urlsplit(self.path).path)
        if path=='/api/status':return self.respond(200,self.server.controller.status())
        if path=='/api/dashboard':
            snapshot=data.read_snapshot(self.server.controller.workbook)
            return self.respond(200,snapshot) if snapshot else self.respond(404,{'error':'尚无有效展示快照，请同步展示'})
        if path.startswith('/api/'):return self.respond(404,{'error':'接口不存在'})
        try:
            target=(self.dist/('index.html' if path=='/' else path.lstrip('/'))).resolve()
            if not target.is_relative_to(self.dist):return self.respond(403,{'error':'路径不允许'})
            if not target.is_file():return self.respond(404,{'error':'页面文件不存在'})
            mime=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            return self.respond(200,target.read_bytes(),mime+('; charset=utf-8' if mime.startswith('text/') else ''))
        except (OSError,ValueError):return self.respond(404,{'error':'页面文件不可读'})

    def do_POST(self):
        origin=self.headers.get('Origin')
        local_origin=f'http://{self.headers.get("Host")}'
        if self.path=='/api/session':
            # sendBeacon cannot set a custom header, so require its same-origin Origin.
            if not self.allowed_host() or (origin!=local_origin and
                (origin is not None or self.headers.get('X-Dashboard-Request')!='1')):
                return self.respond(403,{'error':'只接受本机页面的会话请求'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or self.headers.get('Transfer-Encoding'):raise ValueError()
                # Consume the one-byte-over-limit case before closing. Leaving
                # its body unread can reset a Windows socket before HTTP 400
                # reaches the caller. The read stays bounded for larger input.
                body=self.rfile.read(min(length,257))
                if length>256:raise ValueError()
                payload=json.loads(body)
                page_id=payload['id'];action=payload['action']
                if str(UUID(page_id))!=page_id or action not in ('open','ping','close'):raise ValueError()
            except (ValueError,TypeError,KeyError,AttributeError):
                return self.respond(400,{'error':'无效页面会话'})
            accepted=self.server.controller.session(page_id,action)
            return self.respond(200 if accepted else 503,{'accepted':accepted})
        if (not self.allowed_host() or self.headers.get('X-Dashboard-Request')!='1'
            or (origin and origin!=f'http://{self.headers.get("Host")}')):
            return self.respond(403,{'error':'只接受本机页面的同步请求'})
        if self.path!='/api/sync':return self.respond(404,{'error':'接口不存在'})
        if self.headers.get('Content-Length','0')!='0' or self.headers.get('Transfer-Encoding'):
            return self.respond(400,{'error':'同步接口不接受文件或参数'})
        started=self.server.controller.start_sync()
        return self.respond(202,{'started':started,'syncing':True})


class IdleShutdown(Exception):
    pass


class DashboardHTTPServer(ThreadingHTTPServer):
    def service_actions(self):
        if self.controller.should_shutdown():raise IdleShutdown()

    def serve_forever(self,poll_interval=.5):
        try:super().serve_forever(poll_interval)
        except IdleShutdown:self.server_close()


def make_server(workbook,dist,port=8765):
    server=DashboardHTTPServer(('127.0.0.1',port),partial(Handler,dist=dist))
    server.controller=Controller(workbook)
    server.daemon_threads=True
    return server


def serve(workbook,port=8765,open_browser=True):
    dist=account.ROOT/'dashboard'/'dist'
    if not (dist/'index.html').exists():
        raise ValueError('网页尚未构建；请在 dashboard 目录执行 npm.cmd install 和 npm.cmd run build')
    # Binding first prevents a second launcher from recalculating Excel unnecessarily.
    try:server=make_server(workbook,dist,port)
    except OSError as exc:
        raise RuntimeError(f'本机端口 {port} 已占用；请使用已打开的Dashboard，或指定 --port') from exc
    pid_path=ledger_source.runtime(workbook)/f'dashboard-server{("-"+str(port)) if port!=8765 else ""}.pid'
    pid_path.parent.mkdir(parents=True,exist_ok=True)
    try:
        pid_path.write_text(str(os.getpid()),encoding='utf-8')
        snapshot=data.read_snapshot(workbook)
        try:
            if not data.snapshot_current(workbook,snapshot):server.controller.start_sync()
        except Exception as exc:server.controller.error=str(exc)
        url=f'http://127.0.0.1:{server.server_port}'
        print(json.dumps({'url':url,'hasSnapshot':bool(data.read_snapshot(workbook)),
                          'syncError':server.controller.error},ensure_ascii=False),flush=True)
        if open_browser:webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.controller.wait_for_sync(250)
        server.server_close()
        try:
            if pid_path.read_text(encoding='utf-8').strip()==str(os.getpid()):pid_path.unlink()
        except OSError:pass
