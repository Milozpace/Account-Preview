import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import shutil
import sys
import zipfile
import io
import urllib.error
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
POSIX_SH = shutil.which('sh') or (str(Path('C:/Program Files/Git/bin/sh.exe')) if Path('C:/Program Files/Git/bin/sh.exe').is_file() else None)
spec = importlib.util.spec_from_file_location('preview_bootstrap', ROOT / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
if spec.loader and spec.origin and Path(spec.origin).exists(): spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def source(self):
        (self.root/'release.json').write_text(json.dumps({'version':'0.4.1-preview.1', 'tag':'v0.4.1-preview.1', 'repository':'Milozpace/Account-Preview'}))

    def cli(self, *args):
        out = io.StringIO()
        with patch.object(bootstrap, 'ROOT', self.root), patch.object(bootstrap, 'platform_key', return_value='windows-x64'), redirect_stdout(out):
            code = bootstrap.main(list(args))
        return code, json.loads(out.getvalue())

    def test_apply_requires_matching_absolute_root_before_preparation(self):
        self.source()
        for options in [[], ['--expected-root','.'], ['--expected-root',str(self.root/'wrong')]]:
            with self.subTest(options=options), patch.object(bootstrap, 'prepare_python') as prepare:
                with self.assertRaises(ValueError): self.cli('--apply', *options)
                prepare.assert_not_called()
        self.assertFalse((self.root/'.runtime').exists())

    def test_public_download_needs_no_gh_or_login_and_checks_outer_hash(self):
        archive=self.bundle()
        payload=archive.read_bytes()
        name='Account-Preview-0.4.0-preview.1-windows-x64.zip'
        checksums=(hashlib.sha256(payload).hexdigest()+'  '+name+'\n').encode()
        for gh in (None,'gh'):
            with self.subTest(gh=gh), patch.object(bootstrap.shutil,'which',return_value=gh), patch.object(bootstrap,'run',side_effect=subprocess.CalledProcessError(1,['gh'])), patch('urllib.request.urlopen',side_effect=lambda request,**kw: io.BytesIO(checksums if request.full_url.endswith('SHA256SUMS') else payload)) as fetch:
                result=bootstrap.download_release(self.root,'Milozpace/Account-Preview','v0.4.0-preview.1','windows-x64')
                self.assertEqual(result['version'],'0.4.0-preview.1')
                self.assertEqual(fetch.call_count,2)
                for call in fetch.call_args_list:
                    self.assertTrue(call.args[0].full_url.startswith('https://github.com/Milozpace/Account-Preview/releases/download/v0.4.0-preview.1/'))
        with patch.object(bootstrap.shutil,'which',return_value=None), patch('urllib.request.urlopen',side_effect=lambda request,**kw: io.BytesIO(b'0'*64+b'  '+name.encode()+b'\n' if request.full_url.endswith('SHA256SUMS') else payload)):
            with self.assertRaisesRegex(ValueError,'SHA256'):
                bootstrap.download_release(self.root,'Milozpace/Account-Preview','v0.4.0-preview.1','windows-x64')

    def test_dry_run_reports_source_and_only_selected_steps(self):
        self.source()
        before = sorted(self.root.rglob('*'))
        _, result = self.cli()
        self.assertEqual(result['source']['kind'], 'source-archive')
        self.assertEqual(result['source']['repository'], 'Milozpace/Account-Preview')
        self.assertEqual(result['steps']['python']['status'], 'planned')
        for name in ['components','desktop','app','native']:
            self.assertEqual(result['steps'][name]['status'], 'skipped')
        self.assertEqual(before, sorted(self.root.rglob('*')))

    def test_git_origin_identity_is_checked_and_credentials_never_reported(self):
        self.source(); (self.root/'.git').mkdir()
        for url, accepted in [('https://token:secret@github.com/Milozpace/Account-Preview.git',True), ('git@github.com:Milozpace/Account-Preview.git',True), ('https://github.com/other/repo.git',False)]:
            with patch.object(bootstrap.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout=url+'\n')):
                if accepted:
                    _, result = self.cli()
                    self.assertNotIn('secret', json.dumps(result))
                    self.assertEqual(result['source']['kind'], 'git')
                else:
                    with self.assertRaises(ValueError): self.cli()

    def test_invalid_source_and_wrong_git_origin_never_prepare_runtime(self):
        self.source()
        (self.root/'.git').mkdir()
        with patch.object(bootstrap.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='https://fixture:secret@github.com/wrong/project.git')), patch.object(bootstrap,'prepare_python') as prepare:
            with self.assertRaisesRegex(ValueError,'Git origin') as error: self.cli('--apply','--expected-root',str(self.root))
            self.assertNotIn('secret',str(error.exception)); prepare.assert_not_called()
        (self.root/'.git').rmdir()
        (self.root/'release.json').write_text('{}')
        with patch.object(bootstrap,'prepare_python') as prepare:
            with self.assertRaises(ValueError): self.cli('--apply','--expected-root',str(self.root))
            prepare.assert_not_called()
        self.assertFalse((self.root/'.runtime').exists())

    def test_invalid_release_shapes_and_invalid_binding_are_preserved(self):
        for invalid in [[],None,{'repository':'Milozpace/Account-Preview','version':1,'tag':'v1'}]:
            (self.root/'release.json').write_text(json.dumps(invalid))
            with self.assertRaises(ValueError): self.cli()
        cfg=self.root/'project.json'; python=self.root/'python'; python.write_bytes(b'fixture')
        for invalid in [None,[],{'projectRoot':str(self.root)}]:
            cfg.write_text(json.dumps(invalid)); before=cfg.read_bytes()
            with self.assertRaises(ValueError): bootstrap.configure_desktop(self.root,python,config=cfg,apply=True)
            self.assertEqual(cfg.read_bytes(),before)

    def test_rebind_requires_matching_old_root_and_preserves_raw_backup(self):
        cfg=self.root/'config/project.json'; cfg.parent.mkdir()
        old=self.root/'old'; old.mkdir()
        original=json.dumps({'projectRoot':str(old),'pythonExecutable':'old-python'},indent=4).encode()
        cfg.write_bytes(original)
        python=self.root/'python'; python.write_bytes(b'interpreter')
        with self.assertRaises(ValueError): bootstrap.configure_desktop(self.root,python,config=cfg,apply=True,rebind_from=self.root/'wrong')
        self.assertEqual(cfg.read_bytes(),original)
        result=bootstrap.configure_desktop(self.root,python,config=cfg,apply=True,rebind_from=old)
        self.assertEqual(Path(result['backup']).read_bytes(),original)
        self.assertEqual(json.loads(cfg.read_text())['projectRoot'],str(self.root))
        repeated=bootstrap.configure_desktop(self.root,python,config=cfg,apply=True,rebind_from=old)
        self.assertFalse(repeated['changed'])
        self.assertEqual(len(list(cfg.parent.glob('project.json.backup-*'))),1)

    def test_rebind_cli_missing_intent_is_read_only(self):
        self.source()
        for args in [('--rebind-from',str(self.root)),('--apply','--expected-root',str(self.root),'--rebind-from',str(self.root))]:
            with patch.object(bootstrap,'prepare_python') as prepare:
                with self.assertRaises(ValueError): self.cli(*args)
                prepare.assert_not_called()

    def test_binding_conflict_cli_preflight_happens_before_any_runtime_write(self):
        self.source()
        cfg=self.root/'config/project.json'; cfg.parent.mkdir()
        cfg.write_text(json.dumps({'projectRoot':str(self.root/'old'),'pythonExecutable':'old'}))
        before=cfg.read_bytes()
        for extra in [[],['--rebind-from',str(self.root/'wrong')]]:
            with patch.object(bootstrap,'config_location',return_value=cfg), patch.object(bootstrap,'prepare_python') as prepare:
                with self.assertRaises(ValueError): self.cli('--apply','--expected-root',str(self.root),'--configure-desktop',*extra)
                prepare.assert_not_called()
            self.assertEqual(cfg.read_bytes(),before)
        self.assertFalse((self.root/'.runtime').exists())

    def test_rebind_refuses_configuration_changed_after_preflight(self):
        cfg=self.root/'config/project.json'; cfg.parent.mkdir()
        old=self.root/'old'; old.mkdir()
        original=json.dumps({'projectRoot':str(old),'pythonExecutable':'old'}).encode()
        cfg.write_bytes(original)
        python=self.root/'python'; python.write_bytes(b'fixture')
        other=json.dumps({'projectRoot':str(self.root/'third'),'pythonExecutable':'newer'}).encode()
        from contextlib import contextmanager
        @contextmanager
        def race(_):
            cfg.write_bytes(other)
            yield
        with patch.object(bootstrap,'config_lock',race):
            with self.assertRaises(ValueError): bootstrap.configure_desktop(self.root,python,config=cfg,apply=True,rebind_from=old)
        self.assertEqual(cfg.read_bytes(),other)
        self.assertFalse(list(cfg.parent.glob('project.json.backup-*')))

    def test_apply_reruns_python_and_requested_install_and_reports_truth(self):
        self.source()
        for _ in range(2):
            with patch.object(bootstrap,'prepare_python',return_value=self.root/'python') as prepare, patch.object(bootstrap,'stage_bundle',return_value={'installer':'fixture'}) as stage, patch.object(bootstrap,'install_app',return_value={'installed':True}) as install:
                _, result=self.cli('--apply','--expected-root',str(self.root),'--bundle','fixture.zip','--install-app')
                prepare.assert_called_once(); install.assert_called_once(); stage.assert_called_once()
            self.assertEqual(result['steps']['python']['status'],'completed')
            self.assertEqual(result['steps']['app']['status'],'completed')
            self.assertEqual(result['steps']['desktop']['status'],'skipped')

    def bundle(self, extra=None, wrong_hash=False):
        files = {'bin/ledger_cli.exe': b'publisher', 'bin/bill_inbox_cli.exe': b'inbox',
                 'bin/sync_diagnose.exe': b'diagnose', 'install/Account-Preview.exe': b'installer'}
        if extra: files[extra] = b'unsafe'
        manifest = {'format': 1, 'version': '0.4.0-preview.1', 'platform': 'windows-x64',
                    'files': {p: hashlib.sha256(v).hexdigest() for p, v in files.items()}}
        if wrong_hash: manifest['files']['bin/ledger_cli.exe'] = '0' * 64
        archive = self.root / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            for p, v in files.items(): z.writestr(p, v)
        return archive

    def test_platform_selection(self):
        self.assertEqual(bootstrap.platform_key('Darwin', 'arm64'), 'macos-arm64')
        self.assertEqual(bootstrap.platform_key('Darwin', 'x86_64'), 'macos-x64')
        self.assertEqual(bootstrap.platform_key('Windows', 'AMD64'), 'windows-x64')
        with self.assertRaises(ValueError): bootstrap.platform_key('Linux', 'x86_64')

    def test_cli_json_is_utf8_even_with_legacy_console_encoding(self):
        if sys.platform not in ('win32', 'darwin'):
            self.skipTest('desktop bootstrap')
        result = subprocess.run([sys.executable, str(ROOT/'bootstrap.py')],
                                env={**os.environ, 'PYTHONIOENCODING': 'cp1252'}, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertTrue(json.loads(result.stdout.decode('utf-8'))['dryRun'])

    @unittest.skipUnless(sys.platform == 'win32' and shutil.which('powershell'), 'Windows PowerShell compatibility')
    def test_stock_windows_powershell_can_parse_launcher(self):
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(ROOT/'bootstrap.ps1')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    @unittest.skipUnless(sys.platform == 'win32' and shutil.which('powershell'), 'Windows PowerShell 5.1 fixture')
    def test_ps51_native_stderr_pipeline_preserves_progress_exit_and_environment(self):
        self.source(); shutil.copy2(ROOT/'bootstrap.ps1',self.root/'bootstrap.ps1')
        uv=self.root/'.runtime/bootstrap/uv.exe'; uv.parent.mkdir(parents=True)
        build=self.root/'compile.ps1'
        build.write_text('''Add-Type -TypeDefinition @'
using System;
public class UVFixture { public static int Main(string[] args) { Console.Out.WriteLine("fixture stdout"); Console.Error.WriteLine("fixture progress stderr"); return int.Parse(Environment.GetEnvironmentVariable("FIXTURE_EXIT")); } }
'@ -OutputAssembly $args[0] -OutputType ConsoleApplication
''',encoding='utf-8-sig')
        result=subprocess.run(['powershell','-NoProfile','-File',str(build),str(uv)],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        probe=self.root/'probe.ps1'
        probe.write_text('''$ErrorActionPreference='Stop'
$env:UV_CACHE_DIR='original-cache'
$env:UV_PYTHON_INSTALL_DIR='original-python'
& (Join-Path $PSScriptRoot 'bootstrap.ps1') --apply --expected-root $PSScriptRoot 2>&1 | Select-Object -Last 30
$nativeExit=$LASTEXITCODE
Write-Output "restored=$($env:UV_CACHE_DIR),$($env:UV_PYTHON_INSTALL_DIR),$ErrorActionPreference"
exit $nativeExit
''',encoding='utf-8-sig')
        for code in [0,7]:
            with self.subTest(code=code):
                result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(probe)],env={**os.environ,'FIXTURE_EXIT':str(code)},capture_output=True)
                output=(result.stdout+result.stderr).decode(errors='replace')
                self.assertEqual(result.returncode,code,output)
                self.assertIn('fixture stdout',output)
                self.assertIn('fixture progress stderr',output)
                self.assertIn('restored=original-cache,original-python,Stop',output)

    @unittest.skipUnless(sys.platform == 'win32' and shutil.which('powershell'), 'Windows launcher guard')
    def test_ps_guard_refuses_before_creating_runtime(self):
        self.source(); shutil.copy2(ROOT/'bootstrap.ps1',self.root/'bootstrap.ps1')
        probe=self.root/'guard.ps1'
        probe.write_text("function Invoke-WebRequest { throw 'fixture forbids network' }\n& (Join-Path $PSScriptRoot 'bootstrap.ps1') @args\nexit $LASTEXITCODE\n",encoding='utf-8-sig')
        for args in [[],['--expected-root',str(self.root/'wrong')],['--expected-root','.']]:
            result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(probe),'--apply',*args],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('--expected-root',result.stderr.decode(errors='replace'))
            self.assertFalse((self.root/'.runtime').exists())

    @unittest.skipUnless(sys.platform == 'win32' and shutil.which('powershell'), 'Windows launcher source and download errors')
    def test_ps_source_mismatch_and_checksum_errors_are_fatal_json(self):
        self.source(); shutil.copy2(ROOT/'bootstrap.ps1',self.root/'bootstrap.ps1')
        probe=self.root/'guard.ps1'
        probe.write_text('''function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing) [IO.File]::WriteAllText($OutFile,'synthetic invalid zip') }
function Get-FileHash { @{Hash=('0'*64)} }
& (Join-Path $PSScriptRoot 'bootstrap.ps1') @args
exit $LASTEXITCODE
''',encoding='utf-8-sig')
        # No real network is used; a synthetic download must fail its checksum.
        result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(probe),'--apply','--expected-root',str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('checksum',json.loads(result.stderr)['error'])
        self.assertFalse((self.root/'.runtime/bootstrap/uv.exe').exists())
        shutil.rmtree(self.root/'.runtime')
        (self.root/'.git').mkdir()
        probe.write_text('''function git { $global:LASTEXITCODE=0; 'https://fixture:secret@github.com/wrong/repo.git' }
function Invoke-WebRequest { throw 'fixture forbids network' }
& (Join-Path $PSScriptRoot 'bootstrap.ps1') @args
exit $LASTEXITCODE
''',encoding='utf-8-sig')
        result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(probe),'--apply','--expected-root',str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('Git origin',json.loads(result.stderr)['error'])
        self.assertNotIn('secret',result.stdout+result.stderr)
        self.assertFalse((self.root/'.runtime').exists())

    @unittest.skipUnless(POSIX_SH, 'POSIX launcher guard')
    def test_posix_launcher_syntax_and_pre_download_guard(self):
        self.source(); shutil.copy2(ROOT/'bootstrap.sh',self.root/'bootstrap.sh')
        script=(self.root/'bootstrap.sh').as_posix()
        if sys.platform=='win32':
            script=subprocess.check_output([POSIX_SH,'-c','cygpath -u "$1"','sh',script],text=True).strip()
        syntax=subprocess.run([POSIX_SH,'-n',script],capture_output=True)
        self.assertEqual(syntax.returncode,0,syntax.stderr)
        dry=subprocess.run([POSIX_SH,script],capture_output=True,text=True)
        self.assertEqual(dry.returncode,0,dry.stderr)
        self.assertEqual(json.loads(dry.stdout)['source']['kind'],'source-archive')
        for args in [[],['--expected-root',str(self.root/'wrong')],['--expected-root','.']]:
            result=subprocess.run([POSIX_SH,script,'--apply',*args],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('--expected-root',result.stderr)
            self.assertFalse((self.root/'.runtime').exists())

    def test_config_dry_run_and_no_project_rebind(self):
        cfg = self.root / 'config/project.json'
        python = self.root / 'python'; python.write_bytes(b'interpreter')
        bootstrap.configure_desktop(self.root, python, config=cfg)
        self.assertFalse(cfg.exists())
        bootstrap.configure_desktop(self.root, python, config=cfg, apply=True)
        before = cfg.read_bytes()
        bootstrap.configure_desktop(self.root, python, config=cfg, apply=True)
        self.assertEqual(before, cfg.read_bytes())
        other = self.root / 'other'; other.mkdir()
        with self.assertRaises(ValueError): bootstrap.configure_desktop(other, python, config=cfg, apply=True)
        self.assertEqual(before, cfg.read_bytes())

    def test_bundle_stages_verified_version_and_switches_manifest(self):
        archive = self.bundle()
        result = bootstrap.stage_bundle(self.root, archive, 'windows-x64')
        manifest = json.loads((self.root / '.runtime/helpers.json').read_text())
        for name in ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose'):
            self.assertTrue((self.root / manifest['helpers'][name]).is_file())
        self.assertTrue(Path(result['installer']).is_file())
        bootstrap.stage_bundle(self.root, archive, 'windows-x64')
        (self.root / 'local').mkdir(); (self.root / 'local/个人规则.md').write_text('keep')
        bootstrap.stage_bundle(self.root, archive, 'windows-x64')
        self.assertEqual((self.root / 'local/个人规则.md').read_text(), 'keep')

    def test_bundle_rejects_tamper_and_paths_before_creating_runtime(self):
        for extra, bad_hash in (('../escape', False), ('bin\\escape.exe', False), ('local/private.txt', False),
                                ('bin/./ledger_cli.exe', False), ('bin/LEDGER_CLI.exe', False), (None, True)):
            with self.subTest(extra=extra, bad_hash=bad_hash):
                archive = self.bundle(extra, bad_hash)
                with self.assertRaises(ValueError): bootstrap.stage_bundle(self.root, archive, 'windows-x64')
                self.assertFalse((self.root / '.runtime').exists())

    def test_source_and_bundle_version_must_match_before_any_runtime_write(self):
        archive = self.bundle()
        (self.root/'release.json').write_text(json.dumps({'version':'0.4.1-preview.1','tag':'v0.4.1-preview.1'}))
        with self.assertRaisesRegex(ValueError, '源码'):
            bootstrap.stage_bundle(self.root, archive, 'windows-x64')
        self.assertFalse((self.root/'.runtime').exists())

    def test_native_setup_preserves_existing_identity_and_enables_both_gates(self):
        self.bundle()
        bootstrap.stage_bundle(self.root, self.root/'bundle.zip', 'windows-x64')
        (self.root/'data').mkdir()
        (self.root/'data/source.json').write_text('{"kind":"sqlite","path":"account.sqlite3"}')
        (self.root/'data/account.sqlite3').write_bytes(b'synthetic marker')
        calls = []
        def native(args, **kwargs):
            calls.append([str(x) for x in args])
            return subprocess.CompletedProcess(args, 0, stdout='{"initialized":true,"publishEnabled":false}')
        with patch.object(bootstrap, 'run', side_effect=native):
            bootstrap.setup_native(self.root, initialize=True, enable_publish=True)
        self.assertFalse(any('--init' in c for c in calls))
        self.assertTrue(any('--enable-publish' in c for c in calls))
        self.assertEqual(json.loads((self.root/'.runtime/ledger/publish.json').read_text()), {'enabled': True})

    def test_native_failure_does_not_enable_local_gate(self):
        self.bundle(); bootstrap.stage_bundle(self.root, self.root/'bundle.zip', 'windows-x64')
        (self.root/'data').mkdir()
        (self.root/'data/source.json').write_text('{"kind":"sqlite","path":"account.sqlite3"}')
        (self.root/'data/account.sqlite3').write_bytes(b'synthetic marker')
        with patch.object(bootstrap, 'run', side_effect=subprocess.CalledProcessError(2, ['native'])):
            with self.assertRaises(subprocess.CalledProcessError): bootstrap.setup_native(self.root, enable_publish=True)
        self.assertFalse((self.root/'.runtime/ledger/publish.json').exists())

    def test_direct_bootstrap_reuses_managed_uv_without_pip(self):
        name = 'uv.exe' if sys.platform == 'win32' else ('uv-aarch64-apple-darwin/uv' if bootstrap.platform.machine() == 'arm64' else 'uv-x86_64-apple-darwin/uv')
        uv = self.root/'.runtime/bootstrap'/name
        uv.parent.mkdir(parents=True); uv.write_bytes(b'managed uv')
        python = self.root/('.venv/Scripts/python.exe' if sys.platform == 'win32' else '.venv/bin/python')
        python.parent.mkdir(parents=True); python.write_bytes(b'no pip interpreter')
        with patch.object(bootstrap, 'run') as execute:
            bootstrap.prepare_python(self.root)
        commands = [[str(a) for a in call.args[0]] for call in execute.call_args_list]
        self.assertTrue(any(command[0] == str(uv) and command[1:3] == ['pip','sync'] for command in commands))
        self.assertFalse(any(command[1:3] == ['-m','pip'] for command in commands))

    def test_external_tools_uv_creates_owner_managed_runtime(self):
        tools=self.root/'tools'; owner=self.root/'owner'; owner.mkdir()
        name='uv.exe' if sys.platform=='win32' else ('uv-aarch64-apple-darwin/uv' if bootstrap.platform.machine()=='arm64' else 'uv-x86_64-apple-darwin/uv')
        uv=tools/'.runtime/bootstrap'/name
        uv.parent.mkdir(parents=True); uv.write_bytes(b'isolated tool uv')
        with patch.object(bootstrap,'ROOT',tools), patch.object(bootstrap,'run') as execute:
            bootstrap.prepare_python(owner)
        commands=[[str(a) for a in call.args[0]] for call in execute.call_args_list]
        self.assertEqual(commands[0][0],str(uv))
        self.assertIn('--managed-python',commands[0])
        self.assertEqual(execute.call_args_list[0].kwargs['env']['UV_PYTHON_INSTALL_DIR'],str(owner/'.runtime/python'))


if __name__ == '__main__': unittest.main()
