"""Portable helper and desktop bridge paths; no production profile reads."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import account
import app_desktop
import app_export
import onboarding
from bill_inbox import Inbox
import runtime_tools

class PortableRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()

    def test_inbox_uses_current_platform_helper_under_runtime_bin(self):
        path=runtime_tools.helper_path(self.root,'bill_inbox_cli')
        path.parent.mkdir(parents=True);path.touch()
        with patch('bill_inbox.subprocess.run') as run:
            run.return_value.returncode=0;run.return_value.stdout='{}'
            Inbox(self.root)._run('receive')
        self.assertEqual(run.call_args.args[0][0],str(path))

    def test_publish_gate_explicitly_enabled_can_use_portable_helper(self):
        directory=self.root/'.runtime/ledger';directory.mkdir(parents=True)
        (directory/'publish.json').write_text(json.dumps({'enabled':True}),encoding='utf-8')
        gate=app_export.publish_gate(self.root/'.runtime')
        self.assertTrue(gate['enabled'])
        self.assertEqual(gate['publisher'],str(runtime_tools.helper_path(self.root,'ledger_cli')))
        (directory/'publish.json').write_text(json.dumps({'enabled':False}),encoding='utf-8')
        self.assertFalse(app_export.publish_gate(self.root/'.runtime')['enabled'])

    def test_bootstrap_manifest_selects_versioned_local_helper(self):
        directory=self.root/'.runtime';directory.mkdir()
        (directory/'helpers.json').write_text(json.dumps({'version':1,'helpers':{'ledger_cli':'.runtime/bin/preview-1/ledger_cli.exe'}}),encoding='utf-8')
        self.assertEqual(runtime_tools.helper_path(self.root,'ledger_cli'), self.root/'.runtime/bin/preview-1/ledger_cli.exe')
        self.assertIn('sync_diagnose',runtime_tools.helper_status(self.root))

    def test_manifest_rejects_unknown_helper_names(self):
        directory=self.root/'.runtime';directory.mkdir()
        (directory/'helpers.json').write_text(json.dumps({'version':1,'helpers':{'other_tool':'.runtime/bin/other.exe'}}),encoding='utf-8')
        with self.assertRaises(ValueError): runtime_tools.helper_path(self.root,'ledger_cli')

    def test_manifest_rejects_external_helper(self):
        directory=self.root/'.runtime';directory.mkdir()
        (directory/'helpers.json').write_text(json.dumps({'version':1,'helpers':{'ledger_cli':'../other.exe'}}),encoding='utf-8')
        with self.assertRaises(ValueError):runtime_tools.helper_path(self.root,'ledger_cli')

    def test_bridge_does_not_require_owner_memory_or_excel_or_windows(self):
        scripts=self.root/'scripts';scripts.mkdir()
        for name in ('account.py','app_export.py','dashboard_data.py','app_desktop.py'):
            (scripts/name).touch()
        onboarding.initialize(self.root,'2026-10',dict.fromkeys(account.POOLS,0),apply=True)
        (self.root/'PROJECT_MEMORY.md').unlink()
        root,db,snapshot=app_desktop.project_paths(self.root)
        self.assertEqual(db,self.root/'data/account.sqlite3')
        self.assertIsNone(app_desktop.load_snapshot(root)['snapshot'])
        result=app_desktop.refresh_snapshot(root)
        self.assertEqual(result['snapshot']['ledgerCount'],0)
        self.assertTrue(result['appPublish']['skipped'])

    def test_doctor_uses_adopted_personal_binding_not_preview_binding(self):
        state=self.root/'.runtime/product/state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({'format':1,'root':str(self.root),'repository':'Milozpace/Account-Preview','profile':'personal'}))
        config=self.root/'system/AccountDesktop/project.json'
        config.parent.mkdir(parents=True); config.write_text('{}')
        with patch('onboarding.platform.system',return_value='Windows'), patch.dict(os.environ,{'LOCALAPPDATA':str(self.root/'system')}):
            result=onboarding.doctor(self.root)
        self.assertEqual(result['desktop']['profile'],'personal')
        self.assertTrue(result['desktop']['profilePresent'])

    def test_invalid_adopted_profile_never_falls_back_to_preview(self):
        state=self.root/'.runtime/product/state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({'format':1,'root':str(self.root),'repository':'Milozpace/Account-Preview','profile':'someone-else'}))
        with self.assertRaises(ValueError): runtime_tools.owner_profile(self.root)

if __name__=='__main__':unittest.main()
