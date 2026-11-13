"""Prepare one build identity in an isolated checkout; default is a read-only plan."""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
try:
    import product_version
    import personal_version
except ModuleNotFoundError:
    from scripts import product_version, personal_version


def inactive_android_build_sources(root, profiles, selected):
    """Tauri's buildSrc classes are in the default Kotlin package despite their paths."""
    source = root/'app/src-tauri/gen/android/buildSrc/src/main/java'
    paths = {}
    for profile, config in profiles.items():
        files = [source/Path(*config['identifier'].split('.'))/'kotlin'/name
                 for name in ['BuildTask.kt', 'RustPlugin.kt']]
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Android 生成源码路径必须留在隔离构建目录内。')
        paths[profile] = files
    # The first prepare runs before init. Keep its only usable build plugin until
    # init has generated the complete active pair; the second prepare removes duplicates.
    if not all(path.is_file() for path in paths[selected]): return []
    return [path for profile, files in paths.items() if profile != selected for path in files if path.is_file()]


def android_gradle_text(text, identifier, native_version, version_code):
    text = re.sub(r'((?:namespace|applicationId) = ")com\.account\.(?:preview|probe)(")',
                  lambda match: match[1]+identifier+match[2], text)
    text = re.sub(r'(?m)^(\s*)versionCode = .*$', lambda match: match[1]+f'versionCode = {version_code}', text)
    return re.sub(r'(?m)^(\s*)versionName = .*$', lambda match: match[1]+f'versionName = "{native_version}"', text)


def android_display_strings(text):
    """Keep the launcher label independent from the historical installer name."""
    resource=ET.fromstring(text)
    if resource.tag!='resources': raise ValueError('Android 名称资源格式无效。')
    for name in ('app_name','main_activity_title'):
        item=resource.find(f"string[@name='{name}']")
        if item is None: item=ET.SubElement(resource,'string',{'name':name})
        item.text='Account'
    ET.indent(resource,space='    ')
    return ET.tostring(resource,encoding='unicode')+'\n'


def prepare(root, profile, platform, apply=False, isolated_build=False, personal_version_file=None):
    root = Path(root).resolve(); data = product_version.load(root)
    if profile not in data['profiles']: raise ValueError('未知构建档案。')
    selected = data['profiles'][profile]
    if platform not in selected['platforms']: raise ValueError('此档案不支持该平台。')
    if profile == 'personal':
        if apply and not isolated_build: raise ValueError('个人版本注入须显式指定隔离构建 --isolated-build。')
        if (root/'data/account.sqlite3').exists(): raise ValueError('含账本的项目不得作为个人构建目录。')
        data = personal_version.build_data(root, personal_version_file)
    remove_generated = inactive_android_build_sources(root, data['profiles'], profile) if platform == 'android-arm64' else []
    if apply and remove_generated:
        if not isolated_build: raise ValueError('清理重复 Android 生成源码须显式指定隔离构建 --isolated-build。')
        if (root/'data/account.sqlite3').exists(): raise ValueError('含账本的项目不得清理生成源码；请使用隔离构建副本。')
    conf_path = root/'app/src-tauri/tauri.conf.json'
    conf = json.loads(conf_path.read_text(encoding='utf-8'))
    conf.update(identifier=selected['identifier'], productName=selected['productName'], version=data['nativeVersion'])
    bridge_path = root/'app/src-tauri/src/desktop_bridge.rs'
    content, count = re.subn(r'Account(?:Preview|Desktop)/project\.json', selected['desktopConfig']+'/project.json',
                             bridge_path.read_text(encoding='utf-8'))
    if count != 1: raise ValueError('桌面绑定路径无法唯一定位。')
    if apply:
        display_profile = root/'app/src/delivery-profile.ts'
        if not display_profile.is_file(): raise ValueError('缺少交付展示配置。')
        generated_files = product_version.generated(root, data)
        display_profile.write_text('// Generated for isolated delivery builds.\nexport const showIncome = '+('false' if profile == 'personal' else 'true')+';\n', encoding='utf-8')
        for name, text in generated_files.items():
            (root/name).write_text(text, encoding='utf-8', newline='\n')
        conf_path.write_text(json.dumps(conf, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
        bridge_path.write_text(content, encoding='utf-8', newline='\n')
        if platform == 'android-arm64':
            # Preserve the project's activity/SDK/privacy customizations across tauri init.
            # Only used inside the explicit build checkout; no device/package data is read.
            android = root/'app/src-tauri/gen/android/app'
            strings=android/'src/main/res/values/strings.xml'
            strings.parent.mkdir(parents=True,exist_ok=True)
            strings.write_text(android_display_strings(strings.read_text(encoding='utf-8') if strings.exists() else '<resources/>'),encoding='utf-8',newline='\n')
            saved = root/'.runtime/build-profile/android'; saved.mkdir(parents=True, exist_ok=True)
            for name in ['build.gradle.kts', 'src/main/AndroidManifest.xml']:
                path = android/name; backup = saved/Path(name).name
                if path.exists() and not backup.exists(): backup.write_bytes(path.read_bytes())
                if backup.exists():
                    text = backup.read_text(encoding='utf-8')
                    if name.endswith('.kts'):
                        text = android_gradle_text(text, selected['identifier'], data['nativeVersion'], data['androidVersionCode'])
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
            for path in remove_generated: path.unlink()
    return {'ok': True, 'applied': apply, 'profile': profile, 'platform': platform,
            'version': data['version'], 'nativeVersion': data['nativeVersion'],
            'androidVersionCode': data['androidVersionCode'],
            'identifier': selected['identifier'], 'desktopConfig': selected['desktopConfig'],
            'removeGenerated': [path.relative_to(root).as_posix() for path in remove_generated],
            'android': '运行 tauri android init 重新生成 Java 包与 Gradle applicationId。' if platform == 'android-arm64' else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=product_version.ROOT)
    parser.add_argument('--profile', choices=['preview', 'personal'], default='preview')
    parser.add_argument('--platform', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--personal-version-file', type=Path)
    parser.add_argument('--isolated-build', action='store_true',
                        help='Confirm this is an isolated source build checkout; permits removing inactive generated buildSrc classes.')
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.profile, args.platform, args.apply, args.isolated_build, args.personal_version_file), ensure_ascii=False))


if __name__ == '__main__': main()
