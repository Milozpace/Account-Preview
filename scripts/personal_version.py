"""Read legacy owner-local version baselines; new builds use release.json for both profiles."""
import argparse
import json
from pathlib import Path
try:
    import product_version
except ModuleNotFoundError:
    from scripts import product_version


def validate(value, previous=None):
    if not isinstance(value, dict) or set(value) != {'version', 'androidVersionCode'}:
        raise ValueError('个人版本须包含 version 与 androidVersionCode。')
    version = product_version.native_key(value['version'])
    code = value['androidVersionCode']
    if type(code) is not int or not 0 < code <= 2_100_000_000:
        raise ValueError('个人 Android versionCode 无效。')
    if previous:
        validate(previous)
        old = product_version.native_key(previous['version'])
        if version < old or (version == old and code != previous['androidVersionCode']):
            raise ValueError('个人版本不可降级或复用为不同安装版本。')
        if version > old and code <= previous['androidVersionCode']:
            raise ValueError('个人新版本与 Android versionCode 均须递增。')
    return value


def location(root, path=None):
    return Path(path).resolve() if path else Path(root).resolve()/'local/personal-version.json'


def load(root, path=None):
    file = location(root, path)
    if not file.is_file():
        raise ValueError('缺少指定的旧个人版本基线配置。')
    return validate(json.loads(file.read_text(encoding='utf-8')))


def build_data(root, path=None):
    source = product_version.load(root)
    baseline = location(root, path)
    if path is not None or baseline.exists():
        personal = load(root, path)
        if (product_version.version_key(source['version']) <= product_version.version_key(personal['version'])
                or product_version.native_key(source['nativeVersion']) <= product_version.native_key(personal['version'])
                or source['androidVersionCode'] <= personal['androidVersionCode']):
            raise ValueError('统一产品及安装版本必须超过旧个人基线并严格递增；拒绝降级。')
    return {**source, 'versionPolicy': 'unified',
            'sourceVersion': source['version'], 'tag': None,
            'channel': 'personal-local', 'distribution': 'personal-local'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=product_version.ROOT)
    parser.add_argument('--file', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('show')
    setter = commands.add_parser('set')
    setter.add_argument('--version', required=True)
    setter.add_argument('--android-version-code', type=int)
    setter.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    file = location(args.root, args.file)
    if args.command == 'show':
        value = load(args.root, file)
    else:
        old = load(args.root, file) if file.exists() else None
        major, minor, patch = product_version.native_key(args.version)
        default = max(major*1_000_000 + minor*1_000 + patch,
                      old['androidVersionCode'] + int(args.version != old['version']) if old else 1)
        value = validate({'version': args.version, 'androidVersionCode':
                          args.android_version_code if args.android_version_code is not None else default}, old)
        if args.apply:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'ok': True, 'file': str(file), **value}, ensure_ascii=False))


if __name__ == '__main__': main()
