"""Build the public Android catalog from GitHub Release API pages."""
import argparse
import json
from pathlib import Path
import re
import sys

try:
    from .product_version import version_key
except ImportError:
    from product_version import version_key


REPOSITORY = 'Milozpace/Account-Preview'
ASSET_BASE = f'https://github.com/{REPOSITORY}/releases/download'
MAX_ZIP_BYTES = 150 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_PER_CHANNEL = 100


def catalog_asset(assets, tag, name, limit):
    matches = [asset for asset in assets if asset['name'] == name]
    if len(matches) != 1:
        return None
    asset, = matches
    size = asset.get('size')
    url = f'{ASSET_BASE}/{tag}/{name}'
    if (asset.get('state') != 'uploaded' or type(size) is not int
            or not 0 < size <= limit or asset.get('browser_download_url') != url):
        return None
    public = {'name': name, 'size': size, 'browser_download_url': url}
    digest = asset.get('digest')
    if digest is not None:
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            return None
        public['digest'] = digest
    return public


def build_index(pages):
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ValueError('Expected paginated GitHub Release results.')
    records = {}
    for page in pages:
        for release in page:
            if release.get('draft') is not False:
                continue
            tag = release.get('tag_name', '')
            if not isinstance(tag, str) or not tag.startswith('v'):
                continue
            version = tag[1:]
            try:
                key = version_key(version)
            except ValueError:
                continue
            prerelease = key[3] != 4
            if release.get('prerelease') is not prerelease:
                continue
            assets = release['assets']
            manifest = catalog_asset(assets, tag, 'release-manifest.json', MAX_MANIFEST_BYTES)
            android = catalog_asset(assets, tag, f'Account-Preview-{version}-android-arm64.zip', MAX_ZIP_BYTES)
            if manifest is None or android is None:
                continue
            record = {'tag_name': tag, 'draft': False, 'prerelease': prerelease, 'assets': [manifest, android]}
            if key in records and records[key] != record:
                raise ValueError(f'Conflicting release entries for {tag}.')
            records[key] = record
    ordered = [records[key] for key in sorted(records, reverse=True)]
    # A long series of previews must not evict the latest stable entry.
    stable = [record for record in ordered if not record['prerelease']][:MAX_PER_CHANNEL]
    preview = [record for record in ordered if record['prerelease']][:MAX_PER_CHANNEL]
    return {'format': 1, 'releases': stable + preview}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    index = build_index(json.load(sys.stdin))
    args.output.write_text(json.dumps(index, separators=(',', ':')) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
