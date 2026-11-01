"""Removed credentials remain relevant when a private Git history becomes public."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
try:
    import public_readiness
except ImportError:
    public_readiness = None


class PublicReadinessTests(unittest.TestCase):
    def test_scans_removed_history_and_reports_only_locations(self):
        self.assertIsNotNone(public_readiness, 'public history scanner must exist')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout
            git('init','-q'); git('config','user.name','Synthetic'); git('config','user.email','synthetic@example.invalid')
            (root/'README.md').write_text('Clean source')
            git('add','.'); git('commit','-qm','clean')
            secret = 'ghp_' + 'x'*35
            (root/'README.md').write_text(secret)
            (root/'data').mkdir(); (root/'data/account.sqlite3').write_bytes(b'synthetic')
            git('add','.'); git('commit','-qm','accidental synthetic leak')
            git('rm','-r','data'); (root/'README.md').write_text('Clean again')
            git('add','.'); git('commit','-qm','remove')
            report = public_readiness.inspect(root)
            self.assertFalse(report['ok'])
            self.assertTrue(any(f['reason']=='protected-path' for f in report['findings']))
            self.assertTrue(any(f['reason']=='sensitive-pattern' for f in report['findings']))
            self.assertNotIn(secret, str(report))

    def test_clean_history_is_still_subject_to_manual_publication_review(self):
        self.assertIsNotNone(public_readiness, 'public history scanner must exist')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for args in [('init','-q'),('config','user.name','Synthetic'),('config','user.email','synthetic@example.invalid')]:
                subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
            (root/'README.md').write_text('No private content')
            for args in [('add','.'),('commit','-qm','clean')]:
                subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
            report=public_readiness.inspect(root)
            self.assertTrue(report['ok'])
            self.assertFalse(report['visibilityChanged'])
            self.assertTrue(report['manualReviewRequired'])

    def test_commit_and_annotated_tag_messages_are_scanned(self):
        self.assertIsNotNone(public_readiness)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def git(*args):
                subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
            git('init','-q'); git('config','user.name','Synthetic'); git('config','user.email','synthetic@example.invalid')
            (root/'README.md').write_text('Safe synthetic source')
            git('add','.'); git('commit','-qm','ghp_'+'x'*35)
            git('tag','-a','v-test','-m','github_pat_'+'y'*35)
            report=public_readiness.inspect(root)
            self.assertFalse(report['ok'])
            self.assertEqual({item['path'] for item in report['findings']}, {'<commit-metadata>','<tag-metadata>'})
            self.assertNotIn('x'*35, str(report))


if __name__=='__main__': unittest.main()
