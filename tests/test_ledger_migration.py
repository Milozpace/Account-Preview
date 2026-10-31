import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))


class ActivationBoundaryTests(unittest.TestCase):
    def test_linked_worktree_cannot_activate_and_never_writes_config(self):
        import ledger_migration
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'.git').write_text('gitdir: synthetic')
            with self.assertRaises(ValueError):
                ledger_migration.activate(root,root/'audit.json',apply=True)
            self.assertFalse((root/'data'/'source.json').exists())

    def test_baseline_only_audit_cannot_activate(self):
        import ledger_migration
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'data').mkdir()
            (root/'data'/'account.sqlite3').touch()
            audit=root/'audit.json'
            audit.write_text(json.dumps({'ok':True,'databaseComparison':{'status':'pending'}}))
            with self.assertRaises(ValueError):
                ledger_migration.activate(root,audit,apply=True)
            self.assertFalse((root/'data'/'source.json').exists())


if __name__=='__main__':unittest.main()
