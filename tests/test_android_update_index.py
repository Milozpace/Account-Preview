import unittest

from scripts.android_update_index import ASSET_BASE, build_index


def release(version, *, draft=False, prerelease=False):
    tag = 'v' + version
    names = ['release-manifest.json', f'Account-Preview-{version}-android-arm64.zip', 'SHA256SUMS']
    return {
        'tag_name': tag, 'draft': draft, 'prerelease': prerelease, 'body': 'Full release notes stay on the Release.',
        'assets': [{'name': name, 'size': 100, 'state': 'uploaded',
                    'browser_download_url': f'{ASSET_BASE}/{tag}/{name}', 'digest': 'sha256:' + 'a' * 64}
                   for name in names],
    }


class AndroidUpdateIndexTests(unittest.TestCase):
    def test_includes_only_published_matching_versions_and_required_assets(self):
        stable = release('0.4.1')
        preview = release('0.4.2-rc.1', prerelease=True)
        draft = release('0.4.3', draft=True)
        mislabeled = release('0.4.4-rc.1')
        incomplete = release('0.4.5')
        incomplete['assets'].pop(1)
        foreign = release('0.4.6')
        foreign['assets'][1]['browser_download_url'] = 'https://example.com/bundle.zip'
        pending = release('0.4.7')
        pending['assets'][1]['state'] = 'new'
        index = build_index([[draft, preview, mislabeled], [incomplete, stable, foreign, pending]])
        self.assertEqual(index['format'], 1)
        self.assertEqual([record['tag_name'] for record in index['releases']], ['v0.4.1', 'v0.4.2-rc.1'])
        self.assertFalse(index['releases'][0]['prerelease'])
        self.assertTrue(index['releases'][1]['prerelease'])
        self.assertNotIn('body', index['releases'][0])
        self.assertEqual(index['releases'][0]['assets'][0]['digest'], 'sha256:' + 'a' * 64)
        self.assertEqual(len(index['releases'][0]['assets']), 2)

    def test_catalog_keeps_current_stable_when_there_are_many_prereleases(self):
        stable = release('0.4.1')
        previews = [release(f'0.4.2-rc.{sequence}', prerelease=True) for sequence in range(1, 120)]
        index = build_index([previews, [stable]])
        tags = [record['tag_name'] for record in index['releases']]
        self.assertEqual(tags[:3], ['v0.4.1', 'v0.4.2-rc.119', 'v0.4.2-rc.118'])
        self.assertEqual(len(tags), 101)

    def test_identical_page_overlap_is_idempotent_and_conflicting_assets_fail(self):
        data = release('0.4.1')
        self.assertEqual(build_index([[data]]), build_index([[data], [data]]))
        changed = release('0.4.1')
        changed['assets'][1]['size'] = 101
        with self.assertRaisesRegex(ValueError, 'Conflicting release entries'):
            build_index([[data], [changed]])
