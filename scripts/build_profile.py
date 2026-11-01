"""Prepare one build identity in an isolated checkout; default is a read-only plan."""
import argparse
import json
from pathlib import Path
import re
try:
    import product_version
except ModuleNotFoundError:
    from scripts import product_version


def prepare(root, profile, platform, apply=False):
    root = Path(root); data = product_version.load(root)
    if profile not in data['profiles']: raise ValueError('未知构建档案。')
    selected = data['profiles'][profile]
    if platform not in selected['platforms']: raise ValueError('此档案不支持该平台。')
    conf_path = root/'app/src-tauri/tauri.conf.json'
    conf = json.loads(conf_path.read_text(encoding='utf-8'))
    conf.update(identifier=selected['identifier'], productName=selected['productName'], version=data['nativeVersion'])
    bridge_path = root/'app/src-tauri/src/desktop_bridge.rs'
    content, count = re.subn(r'Account(?:Preview|Desktop)/project\.json', selected['desktopConfig']+'/project.json',
                             bridge_path.read_text(encoding='utf-8'))
    if count != 1: raise ValueError('桌面绑定路径无法唯一定位。')
    if apply:
        conf_path.write_text(json.dumps(conf, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
        bridge_path.write_text(content, encoding='utf-8', newline='\n')
        if platform == 'android-arm64':
            # Preserve the project's activity/SDK/privacy customizations across tauri init.
            # Only used inside the explicit build checkout; no device/package data is read.
            android = root/'app/src-tauri/gen/android/app'
            saved = root/'.runtime/build-profile/android'; saved.mkdir(parents=True, exist_ok=True)
            for name in ['build.gradle.kts', 'src/main/AndroidManifest.xml']:
                path = android/name; backup = saved/Path(name).name
                if path.exists() and not backup.exists(): backup.write_bytes(path.read_bytes())
                if backup.exists():
                    text = backup.read_text(encoding='utf-8')
                    if name.endswith('.kts'):
                        text = re.sub(r'((?:namespace|applicationId) = ")com\.account\.(?:preview|probe)(")',
                                      lambda match: match[1]+selected['identifier']+match[2], text)
                        text = re.sub(r'(?m)^(\s*)versionCode = .*$',
                                      lambda match: match[1]+f"versionCode = {data['androidVersionCode']}", text)
                        text = re.sub(r'(?m)^(\s*)versionName = .*$',
                                      lambda match: match[1]+f"versionName = \"{data['nativeVersion']}\"", text)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8', newline='\n')
            java = android/'src/main/java'
            activity_backup = saved/'MainActivity.kt'
            activities = [java/Path(*item['identifier'].split('.'))/'MainActivity.kt' for item in data['profiles'].values()]
            if not activity_backup.exists():
                existing = [p for p in activities if p.is_file()]
                if len(existing) != 1: raise ValueError('无法唯一定位 Android 自定义 MainActivity。')
                activity_backup.write_bytes(existing[0].read_bytes())
            activity = activity_backup.read_text(encoding='utf-8')
            activity, count = re.subn(r'(?m)^package com\.account\.(?:preview|probe)$',
                                      'package '+selected['identifier'], activity)
            if count != 1: raise ValueError('Android activity 包名无法唯一定位。')
            target = java/Path(*selected['identifier'].split('.'))/'MainActivity.kt'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(activity, encoding='utf-8', newline='\n')
            for path in activities:
                if path != target and path.exists(): path.unlink()
    return {'ok': True, 'applied': apply, 'profile': profile, 'platform': platform,
            'identifier': selected['identifier'], 'desktopConfig': selected['desktopConfig'],
            'android': '运行 tauri android init 重新生成 Java 包与 Gradle applicationId。' if platform == 'android-arm64' else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=product_version.ROOT)
    parser.add_argument('--profile', choices=['preview', 'personal'], default='preview')
    parser.add_argument('--platform', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.profile, args.platform, args.apply), ensure_ascii=False))


if __name__ == '__main__': main()
