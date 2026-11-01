"""Owner update status is available through account.py even before ledger setup."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class UpdateEntryTests(unittest.TestCase):
    def test_status_does_not_initialize_or_write_to_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,str(ROOT/'scripts/account.py'),'--root',folder,'update-status'],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            report=json.loads(result.stdout.decode('utf-8'))
            self.assertFalse(report['adopted'])
            self.assertEqual(list(Path(folder).iterdir()),[])


if __name__=='__main__': unittest.main()
