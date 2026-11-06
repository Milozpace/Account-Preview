import importlib.util
import json
import socket
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import account
import dashboard_data as data
if importlib.util.find_spec('dashboard_server'):
    import dashboard_server as server
else:server=None


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(server,'Local dashboard service must exist')
        clock=patch.object(data,'current_month',return_value='2026-10')
        clock.start();self.addCleanup(clock.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.workbook=self.root/'book.xlsx';self.workbook.write_bytes(b'ledger')
        self.dist=self.root/'dist';self.dist.mkdir();(self.dist/'index.html').write_text('<h1>Dashboard</h1>')
        (self.root/'.runtime').mkdir()
        self.snapshot={'schemaVersion':data.SCHEMA_VERSION,'sourceHash':account.sha(self.workbook),
            'calendarMonth':data.current_month(),'generatedAt':'2026-10-03T12:00:00+08:00','months':[]}
        (self.root/'.runtime'/'dashboard.json').write_text(json.dumps(self.snapshot))
        self.http=server.make_server(self.workbook,self.dist,0)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.url=f'http://127.0.0.1:{self.http.server_port}'

    def stop(self):
        self.http.shutdown();self.http.server_close();self.thread.join()

    def get(self,path,headers=None):
        with urlopen(Request(self.url+path,headers=headers or {}),timeout=3) as response:
            return response.status,response.read(),response.headers

    def test_static_and_snapshot_are_available_without_excel(self):
        with patch.object(data,'run_excel',side_effect=AssertionError('No Excel during browsing')):
            self.assertIn(b'Dashboard',self.get('/')[1])
            self.assertEqual(json.loads(self.get('/api/dashboard')[1]),self.snapshot)
            state=json.loads(self.get('/api/status')[1])
            self.assertFalse(state['sourceChanged']);self.assertFalse(state['monthChanged'])
            self.assertEqual(self.get('/api/dashboard')[2]['Cache-Control'],'no-store')

    def test_saved_source_and_calendar_changes_are_reported(self):
        self.workbook.write_bytes(b'user saved')
        with patch.object(data,'current_month',return_value='2026-11'):
            state=json.loads(self.get('/api/status')[1])
        self.assertTrue(state['sourceChanged']);self.assertTrue(state['monthChanged'])

    def test_activated_source_marks_old_service_stale_and_blocks_old_export(self):
        self.assertFalse(self.http.controller.status()['sourceChanged'])
        (self.root/'data').mkdir()
        (self.root/'data'/'account.sqlite3').touch()
        (self.root/'data'/'source.json').write_text(json.dumps({'kind':'sqlite','path':'account.sqlite3'}))
        state=self.http.controller.status()
        self.assertTrue(state['sourceChanged'])
        self.assertIn('来源已切换',state['error'])
        with patch.object(data,'run_excel',side_effect=AssertionError('old source must never start Excel')):
            with self.assertRaises(ValueError):data.sync_snapshot(self.workbook)

    def test_path_traversal_and_foreign_host_are_rejected(self):
        for route in ['/../book.xlsx','/%2e%2e/book.xlsx','/.runtime/dashboard.json']:
            with self.assertRaises(HTTPError) as error:self.get(route)
            self.assertIn(error.exception.code,[403,404])
        with self.assertRaises(HTTPError) as error:self.get('/api/dashboard',{'Host':'evil.example'})
        self.assertEqual(error.exception.code,403)

    def test_foreign_or_form_sync_request_is_rejected(self):
        for headers in [{},{'Origin':'https://evil.example','X-Dashboard-Request':'1'}]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url+'/api/sync',data=b'',headers=headers),timeout=3)
            self.assertEqual(error.exception.code,403)

    def test_sync_failure_is_separate_from_valid_old_snapshot(self):
        done=threading.Event()
        def fail(_):done.set();raise RuntimeError('Excel unavailable')
        with patch.object(data,'sync_snapshot',side_effect=fail):
            with urlopen(Request(self.url+'/api/sync',data=b'',headers={'X-Dashboard-Request':'1'}),timeout=3) as response:
                self.assertEqual(response.status,202)
            self.assertTrue(done.wait(3))
            self.http.controller.wait_for_sync(3)
        state=json.loads(self.get('/api/status')[1])
        self.assertIn('Excel unavailable',state['error'])
        self.assertTrue(state['hasSnapshot'])
        self.assertEqual(json.loads(self.get('/api/dashboard')[1]),self.snapshot)

    def test_concurrent_sync_does_not_start_second_excel_instance(self):
        entered=threading.Event();release=threading.Event()
        def wait(_):entered.set();release.wait(3);return {'ok':True}
        with patch.object(data,'sync_snapshot',side_effect=wait) as exporter:
            self.http.controller.start_sync();self.assertTrue(entered.wait(3))
            self.assertFalse(self.http.controller.start_sync())
            self.assertTrue(json.loads(self.get('/api/status')[1])['syncing'])
            release.set();self.http.controller.wait_for_sync(3)
            self.assertEqual(exporter.call_count,1)

    def test_same_origin_page_session_and_beacon_close(self):
        page_id='12345678-1234-4234-8234-123456789abc'
        def session(action,headers):
            payload=json.dumps({'id':page_id,'action':action}).encode()
            with urlopen(Request(self.url+'/api/session',data=payload,headers=headers),timeout=3) as response:
                return response.status
        self.assertEqual(session('open',{'X-Dashboard-Request':'1'}),200)
        self.assertEqual(len(self.http.controller.clients),1)
        self.assertEqual(session('close',{'Origin':self.url,'Content-Type':'text/plain'}),200)
        self.assertEqual(len(self.http.controller.clients),0)
        with self.assertRaises(HTTPError) as error:
            session('open',{'Origin':'https://evil.example','Content-Type':'text/plain'})
        self.assertEqual(error.exception.code,403)

    def test_invalid_session_does_not_create_a_client(self):
        for payload in [b'{}',b'not json',json.dumps({'id':'bad','action':'open'}).encode(),
                        json.dumps({'id':'12345678-1234-4234-8234-123456789abc','action':'invalid'}).encode(),b'x'*257]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url+'/api/session',data=payload,headers={'X-Dashboard-Request':'1'}),timeout=3)
            self.assertEqual(error.exception.code,400)


class PageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.controller=server.Controller(account.LIVE,clock=lambda:self.now)
        self.a='12345678-1234-4234-8234-123456789abc'
        self.b='87654321-1234-4234-8234-123456789abc'

    def test_no_browser_started_service_exits_after_startup_timeout(self):
        self.now=59;self.assertFalse(self.controller.should_shutdown())
        self.now=61;self.assertTrue(self.controller.should_shutdown())

    def test_last_close_exits_after_short_reload_grace(self):
        self.controller.session(self.a,'open')
        self.controller.session(self.a,'close')
        self.now=4;self.assertFalse(self.controller.should_shutdown())
        self.now=6;self.assertTrue(self.controller.should_shutdown())
        self.assertFalse(self.controller.start_sync())

    def test_other_page_keeps_service_running(self):
        self.controller.session(self.a,'open');self.controller.session(self.b,'open')
        self.controller.session(self.a,'close')
        self.now=6;self.assertFalse(self.controller.should_shutdown())
        self.controller.session(self.b,'close')
        self.now=12;self.assertTrue(self.controller.should_shutdown())

    def test_reload_registers_replacement_before_service_exits(self):
        self.controller.session(self.a,'open');self.controller.session(self.a,'close')
        self.now=3;self.controller.session(self.b,'open')
        self.now=10;self.assertFalse(self.controller.should_shutdown())

    def test_heartbeat_and_abnormal_browser_exit(self):
        self.controller.session(self.a,'open')
        self.now=90;self.controller.session(self.a,'ping')
        self.now=200;self.assertFalse(self.controller.should_shutdown())
        self.now=211;self.assertFalse(self.controller.should_shutdown())
        self.now=217;self.assertTrue(self.controller.should_shutdown())

    def test_sync_finishes_before_last_page_shutdown(self):
        self.controller.session(self.a,'open');self.controller.session(self.a,'close')
        self.controller.syncing=True
        self.now=20;self.assertFalse(self.controller.should_shutdown())
        self.controller.syncing=False
        self.assertTrue(self.controller.should_shutdown())

    def test_late_heartbeat_cannot_revive_closed_page(self):
        self.controller.session(self.a,'open');self.controller.session(self.a,'close')
        self.now=2;self.controller.session(self.a,'ping');self.controller.session(self.a,'open')
        self.now=6;self.assertTrue(self.controller.should_shutdown())

    def test_close_still_releases_when_initial_registration_was_lost(self):
        self.controller.session(self.a,'close')
        self.now=6;self.assertTrue(self.controller.should_shutdown())


class ServiceProcessTests(unittest.TestCase):
    def test_unopened_server_releases_socket(self):
        http=server.make_server(account.LIVE,account.ROOT/'dashboard'/'dist',0)
        port=http.server_port
        http.controller.startup_deadline=http.controller.clock()+.05
        thread=threading.Thread(target=lambda:http.serve_forever(.01),daemon=True)
        thread.start()
        try:
            thread.join(1);self.assertFalse(thread.is_alive())
            with socket.socket() as client:
                client.settimeout(1);self.assertNotEqual(client.connect_ex(('127.0.0.1',port)),0)
        finally:http.shutdown();http.server_close();thread.join(1)

    def test_last_page_closes_actual_process_port_and_pid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'dashboard'/'dist').mkdir(parents=True)
            (root/'dashboard'/'dist'/'index.html').write_text('<h1>Dashboard</h1>')
            book=root/'book.xlsx';book.write_bytes(b'fixture ledger')
            (root/'.runtime').mkdir()
            (root/'.runtime'/'dashboard.json').write_text(json.dumps({'schemaVersion':data.SCHEMA_VERSION,
                'sourceHash':account.sha(book),'calendarMonth':data.current_month(),'months':[]}))
            # Managed Windows Python may have a launcher parent. Capture the
            # actual server interpreter independently rather than its launcher.
            script="import os,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import account,dashboard_server;account.ROOT=Path(sys.argv[2]);(account.ROOT/'actual-pid').write_text(str(os.getpid()));dashboard_server.serve(account.ROOT/'book.xlsx',port=0,open_browser=False)"
            process=subprocess.Popen([sys.executable,'-c',script,str(account.ROOT/'scripts'),str(root)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                metadata=json.loads(process.stdout.readline());url=metadata['url']
                pid=root/'.runtime'/'dashboard-server-0.pid'
                self.assertEqual(pid.read_text(),(root/'actual-pid').read_text())
                def session(page_id,action):
                    payload=json.dumps({'id':page_id,'action':action}).encode()
                    with urlopen(Request(url+'/api/session',data=payload,headers={'X-Dashboard-Request':'1'}),timeout=3) as response:
                        self.assertEqual(response.status,200)
                a='12345678-1234-4234-8234-123456789abc';b='87654321-1234-4234-8234-123456789abc'
                session(a,'open');session(b,'open');session(a,'close')
                # The first close must not stop a second page's service.
                self.assertIsNone(process.poll())
                session(b,'close')
                stdout,stderr=process.communicate(timeout=8)
                self.assertEqual(process.returncode,0,stderr)
                self.assertFalse(pid.exists())
                port=int(url.rsplit(':',1)[1])
                with socket.socket() as client:
                    client.settimeout(1);self.assertNotEqual(client.connect_ex(('127.0.0.1',port)),0)
                self.assertEqual(book.read_bytes(),b'fixture ledger')
            finally:
                if process.poll() is None:process.kill();process.communicate()


class WorkflowTests(unittest.TestCase):
    def test_export_failure_does_not_fail_successful_import_result(self):
        self.assertTrue(hasattr(account,'update_dashboard'),'Import needs a display hook')
        with patch.object(data,'sync_snapshot',side_effect=RuntimeError('Excel unavailable')):
            result=account.update_dashboard(account.LIVE,{'ok':True,'added':1})
        self.assertTrue(result['ok']);self.assertEqual(result['added'],1)
        self.assertFalse(result['dashboard']['ok'])


if __name__=='__main__':unittest.main()
