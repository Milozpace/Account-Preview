import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from scripts import verify_android_apk, release

class AndroidUpdateChannelTests(unittest.TestCase):
    def test_public_delivery_rejects_test_marker_inside_apk(self):
        with tempfile.TemporaryDirectory() as folder:
            apk=Path(folder)/'synthetic.apk'
            with zipfile.ZipFile(apk,'w') as archive:
                archive.writestr('assets/account-update-test.json','{"updateTestOnly":true}')
            self.assertTrue(release.is_update_test_apk(apk.read_bytes()))
            with self.assertRaises(ValueError): verify_android_apk.check_update_channel(apk)
            self.assertTrue(verify_android_apk.check_update_channel(apk,allow_test=True))
            with self.assertRaisesRegex(ValueError,'测试通道'):
                release.bundle('android-arm64','.',apk,Path(folder)/'public.zip',root=Path(folder))

    def test_release_bundle_verifier_rejects_marked_component_and_nested_apk(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);apk=folder/'synthetic.apk'
            with zipfile.ZipFile(apk,'w') as archive: archive.writestr('assets/account-update-test.json','{}')
            data=dict(version='0.4.1',nativeVersion='0.4.8',androidVersionCode=4008,profiles={'preview':{'identifier':'com.account.preview'}})
            manifest=dict(format=1,version='0.4.1',nativeVersion='0.4.8',versionPolicy='unified',androidVersionCode=4008,
                platform='android-arm64',profile='preview',identifier='com.account.preview',commit='a'*40,
                files={'install/update.apk':hashlib.sha256(apk.read_bytes()).hexdigest()})
            for marked in (False,True):
                outer=folder/f'component-{marked}.zip'
                with zipfile.ZipFile(outer,'w') as archive:
                    archive.writestr('manifest.json',json.dumps({**manifest,**({'updateTestOnly':True} if marked else {})}))
                    archive.writestr('install/update.apk',apk.read_bytes())
                with self.assertRaisesRegex(ValueError,'测试'):
                    release.verify_bundle(outer,data,'preview','android-arm64','a'*40)

    def test_regular_apk_and_old_synthetic_installer_fixtures_are_not_test_builds(self):
        self.assertFalse(release.is_update_test_apk(b'synthetic installer'))
        with tempfile.TemporaryDirectory() as folder:
            apk=Path(folder)/'synthetic.apk'
            with zipfile.ZipFile(apk,'w') as archive: archive.writestr('assets/app.js','synthetic')
            self.assertFalse(release.is_update_test_apk(apk.read_bytes()))
            self.assertFalse(verify_android_apk.check_update_channel(apk))
