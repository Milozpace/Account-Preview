"""Exercise the launcher through Windows PowerShell 5.1's native stderr bridge."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PS51 = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


@unittest.skipUnless(os.name == 'nt' and PS51.is_file(), 'Windows PowerShell 5.1 required')
class BootstrapRedirectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='account-bootstrap-redirect-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'release.json').write_text(json.dumps({
            'repository': 'Milozpace/Account-Preview',
            'version': '0.4.0-preview.3', 'tag': 'v0.4.0-preview.3',
        }), encoding='utf-8')
        shutil.copy2(ROOT / 'bootstrap.ps1', self.root / 'bootstrap.ps1')
        uv = self.root / '.runtime/bootstrap/uv.exe'
        uv.parent.mkdir(parents=True)
        build = self.root / 'compile.ps1'
        build.write_text('''Add-Type -TypeDefinition @'
using System;
public class UVFixture {
    public static int Main(string[] args) {
        Console.Out.WriteLine("stdout-before-download");
        for (int i = 0; i < 35; i++) Console.Error.WriteLine("download-progress-" + i);
        Console.Out.WriteLine("stdout-after-download");
        return int.Parse(Environment.GetEnvironmentVariable("FIXTURE_EXIT"));
    }
}
'@ -OutputAssembly $args[0] -OutputType ConsoleApplication
''', encoding='utf-8-sig')
        built = self.run_ps(build, str(uv))
        self.assertEqual(built.returncode, 0, self.output(built))
        self.probe = self.root / 'redirect.ps1'
        self.probe.write_text('''$ErrorActionPreference='Stop'
if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) { throw 'Expected PowerShell 5.1' }
$env:UV_CACHE_DIR='prior-cache'
$env:UV_PYTHON_INSTALL_DIR='prior-python'
& (Join-Path $PSScriptRoot 'bootstrap.ps1') --apply --expected-root $PSScriptRoot 2>&1 | Select-Object -Last 20
$bootstrapExit=$LASTEXITCODE
Write-Output "restored=$($env:UV_CACHE_DIR),$($env:UV_PYTHON_INSTALL_DIR),$ErrorActionPreference"
exit $bootstrapExit
''', encoding='utf-8-sig')

    @staticmethod
    def output(result):
        return (result.stdout + result.stderr).decode(errors='replace')

    def run_ps(self, script, *args, code=0):
        env = {**os.environ, 'FIXTURE_EXIT': str(code)}
        # Python can retain a PowerShell 7 parent's module path; explicitly
        # exercise stock Windows PowerShell 5.1 modules with its executable.
        env['PSModulePath'] = ';'.join([
            str(Path(os.environ['USERPROFILE']) / 'Documents/WindowsPowerShell/Modules'),
            str(Path(os.environ['ProgramFiles']) / 'WindowsPowerShell/Modules'),
            str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules'),
        ])
        return subprocess.run([
            str(PS51), '-NoLogo', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', str(script), *args,
        ], env=env, capture_output=True, timeout=60)

    def test_last20_keeps_download_progress_exit_code_and_restores_environment(self):
        for code in (0, 7):
            with self.subTest(native_exit=code):
                result = self.run_ps(self.probe, code=code)
                output = self.output(result)
                self.assertEqual(result.returncode, code, output)
                self.assertIn('download-progress-34', output)
                self.assertIn('restored=prior-cache,prior-python,Stop', output)
                self.assertNotIn('"ok":false', output)

    def test_negative_control_without_guard_rejects_successful_native_command(self):
        launcher = self.root / 'bootstrap.ps1'
        source = launcher.read_text(encoding='utf-8')
        self.assertIn("$ErrorActionPreference='Continue'", source)
        launcher.write_text(source.replace("$ErrorActionPreference='Continue'", "$ErrorActionPreference='Stop'"), encoding='utf-8-sig')
        result = self.run_ps(self.probe)
        output = self.output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('download-progress-0', output)
        self.assertIn('"ok":false', output)
        self.assertIn('restored=prior-cache,prior-python,Stop', output)


if __name__ == '__main__':
    unittest.main()
