"""Owner-preserving product updates, using synthetic roots and release assets only."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import product_update as updater
except ImportError:
    updater = None
import ledger_store
import onboarding


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ProductUpdateFixture:
    def setUp(self):
        self.assertIsNotNone(updater, 'owner-preserving updater must be implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'owner'; self.root.mkdir()
        python=self.root/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python')
        python.parent.mkdir(parents=True); python.write_bytes(b'fake environment boundary')
        location=patch.object(updater,'desktop_location',side_effect=lambda profile:self.base/profile/'project.json')
        location.start(); self.addCleanup(location.stop)
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/shared.py').write_bytes(b'old code')
        for name in ('AGENTS.md', 'PROJECT_MEMORY.md', 'docs/记账规则.md', 'local/个人规则.md', 'sra/original.csv'):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'owner secret')
        self.assets = self.base / 'release'; self.assets.mkdir()
        self.installs = []

    def adopt(self, profile='personal'):
        return updater.adopt(self.root, profile=profile, apply=True)

    def release(self, profile='personal', version='0.4.1-preview.1', source=None, native_version='0.4.1', **changes):
        source = source or {'scripts/shared.py': b'new code', 'scripts/new.py': b'new tool',
                            'docs/记账规则.md': b'new shared instructions', 'AGENTS.md': b'new shared entry'}
        source = dict(source)
        source.setdefault('release.json', json.dumps({'version':version,'tag':'v'+version,'channel':'private-preview'}).encode())
        with zipfile.ZipFile(self.assets / 'source.zip', 'w') as z:
            for name, data in source.items(): z.writestr(zipfile.ZipInfo(name), data)
        identifier = {'personal':'com.account.probe', 'preview':'com.account.preview'}[profile]
        files = {'bin/ledger_cli.exe':b'helper', 'bin/bill_inbox_cli.exe':b'inbox', 'bin/sync_diagnose.exe':b'diagnose',
                 'install/Account.exe':b'installer'}
        inner = dict(format=1, version=version, nativeVersion=native_version, platform='windows-x64', profile=profile,
                     identifier=identifier, commit='a'*40, files={name:sha(data) for name,data in files.items()})
        with zipfile.ZipFile(self.assets / 'bundle.zip', 'w') as z:
            z.writestr(zipfile.ZipInfo('manifest.json'), json.dumps(inner))
            for name,data in files.items(): z.writestr(zipfile.ZipInfo(name),data)
        manifest = dict(format=1, version=version, tag='v'+version, channel='private-preview', commit='a'*40,
                        nativeVersion=native_version, ledgerSchema=1, snapshotSchema=2, notesSha256=sha(b'notes'),
                        source={'asset':'source.zip','sha256':sha((self.assets/'source.zip').read_bytes())},
                        profiles={profile:dict(identifier=identifier,desktopConfig='AccountDesktop' if profile=='personal' else 'AccountPreview',
                            platforms={'windows-x64':{'asset':'bundle.zip','sha256':sha((self.assets/'bundle.zip').read_bytes())}})})
        manifest.update(changes)
        (self.assets/'release-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        return manifest

    def update(self, **kwargs):
        return updater.update(self.root, release_dir=self.assets, target='windows-x64',
                              installer=lambda path:self.installs.append(Path(path)), **kwargs)

class ProductUpdateTests(ProductUpdateFixture, unittest.TestCase):
    def test_preview_with_independent_native_version_preserves_owner_on_update(self):
        self.adopt()
        self.release(version='0.4.0-preview.2',native_version='0.4.2')
        self.assertTrue(self.update(apply=True)['ok'])
        self.assertEqual((self.root/'local/个人规则.md').read_bytes(),b'owner secret')

    def test_adopt_defaults_to_readonly_and_requires_explicit_owner_profile(self):
        with self.assertRaises(ValueError): updater.adopt(self.root)
        report = updater.adopt(self.root, profile='personal')
        self.assertTrue(report['dryRun']); self.assertFalse((self.root/'.runtime').exists())
        self.adopt()
        state = json.loads((self.root/'.runtime/product/state.json').read_text())
        self.assertEqual(state['profile'],'personal')
        self.assertEqual(state['managed']['scripts/shared.py'],sha(b'old code'))
        self.assertNotIn('AGENTS.md',state['managed'])
        with self.assertRaises(ValueError): updater.adopt(self.root,profile='preview',apply=True)

    def test_preflight_and_apply_preserve_owner_files_and_repeated_update_is_noop(self):
        self.adopt(); self.release()
        before = {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = self.update()
        self.assertTrue(report['dryRun']); self.assertEqual(self.installs,[])
        self.assertEqual(before,{p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        result = self.update(apply=True)
        self.assertTrue(result['ok']); self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'new code')
        for name in ('AGENTS.md','PROJECT_MEMORY.md','docs/记账规则.md','local/个人规则.md','sra/original.csv'):
            self.assertEqual((self.root/name).read_bytes(),b'owner secret')
        self.assertTrue(Path(result['releaseDocs']).joinpath('docs/记账规则.md').is_file())
        self.assertEqual(len(self.installs),1)
        self.assertTrue(self.update(apply=True)['unchanged']); self.assertEqual(len(self.installs),1)

    def test_modification_deletion_and_unmanaged_addition_conflict_before_mutation(self):
        for scenario in ('modified','deleted','addition'):
            with self.subTest(scenario=scenario):
                (self.root/'scripts/shared.py').write_bytes(b'old code')
                (self.root/'scripts/new.py').unlink(missing_ok=True)
                self.adopt(); self.release()
                if scenario=='modified': (self.root/'scripts/shared.py').write_bytes(b'owner change')
                elif scenario=='deleted': (self.root/'scripts/shared.py').unlink()
                else: (self.root/'scripts/new.py').write_bytes(b'foreign')
                with self.assertRaisesRegex(ValueError,'conflict|冲突'): self.update(apply=True)
                self.assertFalse((self.root/'.runtime/bin').exists()); self.assertEqual(self.installs,[])

    def test_rejects_corrupt_assets_protected_source_paths_and_identity_mismatch(self):
        self.adopt()
        for source, changes in (({'../escape':b'x'},{}),({'data/account.sqlite3':b'x'},{}),
                                ({'scripts/Shared.py':b'x','scripts/shared.py':b'y'},{}),
                                ({'scripts/shared.py':b'x'}, {'channel':'candidate'}),
                                ({'scripts/shared.py':b'x'}, {'ledgerSchema':2})):
            self.release(source=source,**changes)
            with self.assertRaises(ValueError): self.update(apply=True)
        self.release(); (self.assets/'bundle.zip').write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.update(apply=True)
        self.release(profile='preview')
        with self.assertRaises(ValueError): self.update(apply=True)
        self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'old code')
        self.assertEqual(self.installs,[])

    def test_installer_failure_restores_code_helpers_and_publish_config_but_reports_uncertain_binary(self):
        self.adopt(); self.release()
        helpers=self.root/'.runtime/helpers.json'; helpers.write_bytes(b'{"old":true}')
        gate=self.root/'.runtime/ledger/publish.json'; gate.parent.mkdir(); gate.write_bytes(b'{"enabled":true,"publisher":"old","extra":7}')
        def fail(path): raise RuntimeError('synthetic installer failed')
        result=updater.update(self.root,release_dir=self.assets,target='windows-x64',apply=True,installer=fail)
        self.assertFalse(result['ok']); self.assertEqual(result['binaryInstall'],'uncertain-needs-retry')
        self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'old code')
        self.assertFalse((self.root/'scripts/new.py').exists())
        self.assertEqual(helpers.read_bytes(),b'{"old":true}')
        self.assertEqual(gate.read_bytes(),b'{"enabled":true,"publisher":"old","extra":7}')
        self.assertTrue(Path(result['recovery']).is_dir())

    def test_real_sqlite_backup_and_business_hash_preserved(self):
        data=self.root/'data'; data.mkdir()
        db=data/'account.sqlite3'
        onboarding._new_ledger(db,onboarding.parameters('2026-10',{pool:'0' for pool in ledger_store.account.POOLS}))
        (data/'source.json').write_text('{"kind":"sqlite","path":"account.sqlite3"}')
        before=ledger_store.source_hash(db)
        self.adopt(); self.release()
        report=self.update(apply=True)
        self.assertEqual(ledger_store.source_hash(db),before)
        self.assertEqual(ledger_store.source_hash(Path(report['ledgerBackup'])),before)
        self.assertTrue(ledger_store.verify(db)['ok'])

    def test_semantic_selection_ignores_drafts_and_nonpreview_tags(self):
        candidates=[{'tag_name':'v0.4.9-preview.9','draft':False,'prerelease':True},
                    {'tag_name':'v0.4.10-preview.2','draft':False,'prerelease':True},
                    {'tag_name':'v9.0.0-preview.1','draft':True,'prerelease':True},
                    {'tag_name':'v99.0.0','draft':False,'prerelease':False}]
        self.assertEqual(updater.select_release(candidates)['tag_name'],'v0.4.10-preview.2')

    def test_personal_remote_route_stops_before_network_and_requires_local_package(self):
        with patch.object(updater,'gh_json') as network:
            with self.assertRaisesRegex(ValueError,'本地|release-dir'):
                with updater.remote_release('windows-x64','personal'):pass
            network.assert_not_called()

    def test_personal_local_update_tracks_own_version_independently_of_source(self):
        self.adopt();self.release()
        manifest_path=self.assets/'release-manifest.json'
        manifest=json.loads(manifest_path.read_text());source_version=manifest['version']
        bundle=self.assets/'bundle.zip'
        with zipfile.ZipFile(bundle) as archive:contents={n:archive.read(n) for n in archive.namelist()}
        inner=json.loads(contents['manifest.json']);inner.update(version='0.3.16',nativeVersion='0.3.16',distribution='personal-local',sourceVersion=source_version)
        contents['manifest.json']=json.dumps(inner).encode()
        with zipfile.ZipFile(bundle,'w') as archive:
            for name,data in contents.items():archive.writestr(name,data)
        manifest.update(version='0.3.16',nativeVersion='0.3.16',sourceVersion=source_version,
                        distribution='personal-local',channel='personal-local',tag=None)
        manifest['profiles']['personal']['platforms']['windows-x64']['sha256']=sha(bundle.read_bytes())
        manifest_path.write_text(json.dumps(manifest))
        state_path=self.root/updater.STATE
        state=json.loads(state_path.read_text());state['version']='0.4.0-preview.3'
        state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'原生版本.*versionCode'):
            self.update(apply=True)
        self.assertEqual(self.installs,[])
        state['version']=None;state_path.write_text(json.dumps(state))
        result=self.update(apply=True)
        self.assertTrue(result['ok'],result);self.assertEqual(updater.status(self.root)['version'],'0.3.16')
        state=json.loads((self.root/updater.STATE).read_text());self.assertEqual(state['sourceVersion'],source_version)
        self.assertEqual(json.loads((self.root/'release.json').read_text())['version'],source_version)

    def test_two_owners_keep_independent_profile_and_binding(self):
        self.adopt('personal'); self.release('personal'); self.update(apply=True)
        other=self.base/'friend'; other.mkdir();
        python=other/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python'); python.parent.mkdir(parents=True); python.write_bytes(b'fake environment boundary')
        (other/'scripts').mkdir(); (other/'scripts/shared.py').write_bytes(b'old code')
        updater.adopt(other,profile='preview',apply=True)
        self.release('preview')
        updater.update(other,release_dir=self.assets,target='windows-x64',apply=True,installer=lambda path:None)
        self.assertEqual(updater.status(self.root)['profile'],'personal')
        self.assertEqual(updater.status(other)['profile'],'preview')



class ProductUpdateHardeningTests(ProductUpdateFixture, unittest.TestCase):
    def test_bundle_inner_identity_and_commit_must_match_outer(self):
        self.adopt(); self.release()
        bundle=self.assets/'bundle.zip'
        with zipfile.ZipFile(bundle) as z: contents={name:z.read(name) for name in z.namelist()}
        for field,bad in (('identifier','com.account.preview'),('commit','b'*40)):
            with self.subTest(field=field):
                self.release()
                with zipfile.ZipFile(bundle) as z: contents={name:z.read(name) for name in z.namelist()}
                inner=json.loads(contents['manifest.json']); inner[field]=bad
                contents['manifest.json']=json.dumps(inner).encode()
                with zipfile.ZipFile(bundle,'w') as z:
                    for name,data in contents.items(): z.writestr(name,data)
                manifest=json.loads((self.assets/'release-manifest.json').read_text())
                manifest['profiles']['personal']['platforms']['windows-x64']['sha256']=sha(bundle.read_bytes())
                (self.assets/'release-manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError): self.update(apply=True)
                self.assertFalse((self.root/'.runtime/bin').exists())

    def test_source_product_version_must_match_manifest_and_do_not_accept_personal_runtime_files(self):
        self.adopt()
        for source in ({'scripts/shared.py':b'new','release.json':b'{"version":"99.0.0-preview.1"}'},
                       {'scripts/shared.py':b'new','app/.env.local':b'private'},
                       {'scripts/shared.py':b'new','app/build/private.txt':b'private'}):
            self.release(source=source)
            with self.assertRaises(ValueError): self.update(apply=True)

    def test_publisher_reference_changes_alone_and_profile_binding_preserves_other_keys(self):
        self.adopt(); self.release()
        gate=self.root/'.runtime/ledger/publish.json'; gate.parent.mkdir()
        gate.write_text('{"enabled":true,"publisher":"old","extra":7}')
        config=self.base/'AccountDesktop/project.json'; config.parent.mkdir()
        config.write_text(json.dumps({'projectRoot':str(self.root),'pythonExecutable':'old','extra':9}))
        python=self.root/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python')
        python.parent.mkdir(parents=True,exist_ok=True); python.write_bytes(b'python marker')
        result=self.update(apply=True,desktop_config=config)
        self.assertTrue(result['ok'])
        settings=json.loads(gate.read_text()); self.assertIs(settings['enabled'],True); self.assertEqual(settings['extra'],7)
        self.assertIn('ledger_cli',settings['publisher'])
        self.assertEqual(json.loads(config.read_text())['extra'],9)
        other=self.base/'AccountPreview/project.json'; self.assertFalse(other.exists())

    def test_adoption_does_not_capture_build_or_dependency_cache(self):
        for name in ('app/build/huge.dat','app/.gradle/huge.dat','app/node_modules/pkg/index.js'):
            path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'cache')
        self.adopt()
        state=json.loads((self.root/'.runtime/product/state.json').read_text())
        self.assertEqual(set(state['managed']),{'scripts/shared.py'})

class ProductUpdateDependencyTests(ProductUpdateFixture, unittest.TestCase):
    def test_dependency_changes_require_explicit_prepare_option_before_any_mutation(self):
        self.adopt(); self.release(source={'scripts/shared.py':b'new','requirements.lock':b'new locked deps'})
        with self.assertRaisesRegex(ValueError,'prepare-python'): self.update(apply=True)
        self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'old code')
        self.assertFalse((self.root/'.runtime/bin').exists())

    def test_same_version_detects_tampered_installed_helpers(self):
        self.adopt(); self.release(); self.update(apply=True)
        helpers=json.loads((self.root/'.runtime/helpers.json').read_text())
        path=self.root/helpers['helpers']['ledger_cli']; path.write_bytes(b'tampered')
        with self.assertRaises(ValueError): self.update(apply=True)
        self.assertEqual(len(self.installs),1)

    def test_existing_versioned_helper_collision_fails_before_source_change(self):
        self.adopt(); self.release()
        helper=self.root/'.runtime/bin/0.4.1-preview.1-windows-x64/bin/ledger_cli.exe'
        helper.parent.mkdir(parents=True); helper.write_bytes(b'foreign')
        with self.assertRaises(ValueError): self.update(apply=True)
        self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'old code')
        self.assertFalse((self.root/'.runtime/product/recovery').exists())

    def test_updater_refuses_active_venv_python_environment_preparation(self):
        from unittest.mock import patch
        self.adopt(); self.release()
        active=self.root/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python')
        with patch.object(updater.sys,'executable',str(active)):
            with self.assertRaisesRegex(ValueError,'venv|解释器'): self.update(apply=True,prepare_environment=True)

class ProductUpdateRecoveryTests(ProductUpdateFixture, unittest.TestCase):
    def test_active_or_interrupted_lock_prevents_apply(self):
        self.adopt(); self.release()
        lock=self.root/'.runtime/product/update.lock'; lock.write_text('{"pid":0}')
        with self.assertRaisesRegex(ValueError,'lock|更新'): self.update(apply=True)
        self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'old code')
        lock.unlink()
        pending=self.root/'.runtime/product/pending.json'; pending.write_text('{"phase":"prepared"}')
        with self.assertRaisesRegex(ValueError,'pending|恢复'): self.update(apply=True)
        self.assertEqual(self.installs,[])

    def test_failed_update_clears_lock_for_safe_retry_after_program_rollback(self):
        self.adopt(); self.release()
        def fail(path): raise RuntimeError('installer boundary failure')
        failed=updater.update(self.root,release_dir=self.assets,target='windows-x64',apply=True,installer=fail)
        self.assertFalse(failed['ok'])
        self.assertFalse((self.root/'.runtime/product/update.lock').exists())
        self.assertFalse((self.root/'.runtime/product/pending.json').exists())
        self.assertTrue(self.update(apply=True)['ok'])

    def test_zip_rejects_directory_case_alias_file_parent_collision_and_device_entry(self):
        import stat
        self.adopt()
        for source in ({'app/A/x.ts':b'x','app/a/y.ts':b'y'}, {'app/src':b'x','app/src/x.ts':b'y'}):
            self.release(source=source)
            with self.assertRaises(ValueError): self.update(apply=True)
        self.release()
        archive=self.assets/'source.zip'
        with zipfile.ZipFile(archive,'a') as z:
            info=zipfile.ZipInfo('scripts/device'); info.create_system=3; info.external_attr=(stat.S_IFCHR|0o600)<<16
            z.writestr(info,b'not a regular file')
        manifest=json.loads((self.assets/'release-manifest.json').read_text())
        manifest['source']['sha256']=sha(archive.read_bytes())
        (self.assets/'release-manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): self.update(apply=True)
        self.assertFalse((self.root/'.runtime/bin').exists())


class ProductUpdateEnvironmentTests(ProductUpdateFixture, unittest.TestCase):
    def test_missing_environment_check_plans_but_apply_requires_explicit_preparation(self):
        self.adopt(); self.release()
        python=self.root/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python')
        python.unlink()
        report=self.update()
        self.assertTrue(report['requiresEnvironment'])
        with self.assertRaisesRegex(ValueError,'prepare-python'): self.update(apply=True)
        self.assertFalse((self.root/'.runtime/bin').exists())


class ProductUpdateOwnerSnapshotTests(ProductUpdateFixture, unittest.TestCase):
    def test_installer_owner_mutation_is_reported_without_restoring_personal_change(self):
        self.adopt(); self.release()
        rules=self.root/'local/个人规则.md'
        def concurrent_owner_change(path): rules.write_bytes(b'new owner confirmation')
        report=updater.update(self.root,release_dir=self.assets,target='windows-x64',apply=True,installer=concurrent_owner_change)
        self.assertFalse(report['ok'])
        self.assertFalse(report['protectedPreserved'])
        self.assertEqual(report['protectedDeltaCount'],1)
        self.assertEqual(rules.read_bytes(),b'new owner confirmation')
        self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'old code')
        serialized=json.dumps(report,ensure_ascii=False)
        self.assertNotIn('个人规则',serialized)
        self.assertNotIn(sha(b'owner secret'),serialized)

    def test_owner_snapshot_covers_source_rule_docs_originals_workbook_and_new_files(self):
        self.adopt(); self.release()
        for name in ('data/source.json','local/more.md','sra/another.csv','docs/owner.md','owner.xlsx','PROJECT_MEMORY.md'):
            path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'owner original')
        # Source manifest needs a real corresponding DB, so isolate source fingerprint coverage from update setup.
        before=updater.protected_snapshot(self.root)
        for name in ('data/source.json','local/more.md','sra/another.csv','docs/owner.md','owner.xlsx','PROJECT_MEMORY.md'):
            (self.root/name).write_bytes(b'owner changed')
        (self.root/'local/new.md').write_bytes(b'new owner file')
        after=updater.protected_snapshot(self.root)
        self.assertEqual(updater.protected_delta(before,after),7)

    def test_runtime_boundary_owner_change_stops_before_installer(self):
        self.adopt(); self.release()
        rules=self.root/'local/个人规则.md'
        bootstrap=updater.trusted_bootstrap()
        def environment(root):
            rules.write_bytes(b'owner changed while preparing environment')
            return root/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python')
        with patch.object(bootstrap,'prepare_python',side_effect=environment), patch.object(updater,'trusted_bootstrap',return_value=bootstrap):
            report=self.update(apply=True,prepare_environment=True)
        self.assertFalse(report['ok']); self.assertFalse(report['protectedPreserved'])
        self.assertEqual(report['protectedDeltaCount'],1)
        self.assertEqual(self.installs,[])
        self.assertEqual(rules.read_bytes(),b'owner changed while preparing environment')


class ProductUpdateDisplaySettingsTests(ProductUpdateFixture, unittest.TestCase):
    def test_adoption_preserves_explicit_legacy_start_without_executing_old_code(self):
        onboarding.initialize(self.root,'2025-09',dict.fromkeys(__import__('account').POOLS,0),apply=True)
        (self.root/'scripts/dashboard_data.py').write_text("raise AssertionError('never execute legacy source')\nTRACKING_START_MONTH = '2026-09'\n",encoding='utf-8')
        before=ledger_store.source_hash(self.root/'data/account.sqlite3')
        report=updater.adopt(self.root,profile='personal')
        self.assertFalse((self.root/'local/display.json').exists())
        self.adopt()
        self.assertEqual(json.loads((self.root/'local/display.json').read_text()),{'trackingStartMonth':'2026-09'})
        self.assertEqual(ledger_store.source_hash(self.root/'data/account.sqlite3'),before)
        managed=updater.read_json(self.root/updater.STATE)['managed']
        (self.root/'local/display.json').unlink()
        self.adopt()  # resume an adoption recorded by an earlier updater
        self.assertEqual(json.loads((self.root/'local/display.json').read_text()),{'trackingStartMonth':'2026-09'})
        self.assertEqual(updater.read_json(self.root/updater.STATE)['managed'],managed)

    def test_existing_display_choice_is_not_overwritten_or_shared_with_preview(self):
        (self.root/'scripts/dashboard_data.py').write_text("TRACKING_START_MONTH = '2026-09'\n",encoding='utf-8')
        config=self.root/'local/display.json'; config.write_text('{"trackingStartMonth":"2026-10"}')
        self.adopt()
        self.assertEqual(json.loads(config.read_text())['trackingStartMonth'],'2026-10')
        config.unlink()
        self.root.joinpath(updater.STATE).unlink()
        updater.adopt(self.root,profile='preview',apply=True)
        self.assertFalse(config.exists())

    def test_concurrent_owner_choice_wins_legacy_config_capture(self):
        onboarding.initialize(self.root,'2025-09',dict.fromkeys(__import__('account').POOLS,0),apply=True)
        (self.root/'scripts/dashboard_data.py').write_text("TRACKING_START_MONTH = '2026-09'\n",encoding='utf-8')
        config=self.root/'local/display.json'
        parse=updater.ast.parse
        def choose_after_preflight(text):
            config.write_text('{"trackingStartMonth":"2026-10"}')
            return parse(text)
        with patch.object(updater.ast,'parse',side_effect=choose_after_preflight):
            result=self.adopt()
        self.assertEqual(json.loads(config.read_text())['trackingStartMonth'],'2026-10')
        self.assertFalse(result['display']['changed'])


class ProductUpdateEnvironmentBoundaryTests(ProductUpdateFixture, unittest.TestCase):
    def test_redirected_venv_or_runtime_environment_paths_fail_before_preparation(self):
        self.adopt(); self.release()
        original=updater.redirected
        for relative in ('.venv','.venv/Lib/site-packages','.runtime/bootstrap','.runtime/python','.runtime/uv-cache'):
            path=self.root/relative
            if not path.exists(): path.mkdir(parents=True)
            with self.subTest(relative=relative), patch.object(updater,'redirected',side_effect=lambda candidate: candidate==path or original(candidate)):
                with self.assertRaisesRegex(ValueError,'环境|重定向'): self.update(apply=True,prepare_environment=True)
        self.assertEqual(self.installs,[])
        self.assertEqual((self.root/'scripts/shared.py').read_bytes(),b'old code')


class ProductUpdateOfficialSkillTests(ProductUpdateFixture, unittest.TestCase):
    entry='.agents/skills/account-maintenance/SKILL.md'
    reference='.agents/skills/account-maintenance/references/bookkeeping.md'

    def skill_source(self, text=b'official v1'):
        return {'scripts/shared.py':b'new code', self.entry:b'# Account\n[Workflow](references/bookkeeping.md)\n'+text,
                self.reference:b'official reference '+text,
                '.agents/skills/other/SKILL.md':b'foreign upstream skill',
                'docs/owner.md':b'upstream generic docs'}

    def write_owner(self, name, data):
        path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_syncs_only_official_skill_with_hashes_and_readonly_preflight(self):
        self.write_owner('.agents/skills/other/SKILL.md',b'owner skill')
        self.write_owner('docs/owner.md',b'owner docs')
        self.adopt(); self.release(source=self.skill_source())
        before=self.files(); preflight=self.update()
        self.assertEqual(self.files(),before)
        self.assertEqual(preflight['officialSkill']['state'],'pending')
        self.assertEqual(preflight['officialSkill']['path'],str(self.root/self.entry))
        self.assertIn(str(self.root/self.entry),preflight['next'])
        self.assertIn('必须',preflight['next'])
        report=self.update(apply=True)
        self.assertTrue(report['ok'],report)
        self.assertEqual(report['officialSkill']['state'],'synchronized')
        self.assertEqual(report['officialSkill']['version'],'0.4.1-preview.1')
        for name in (self.entry,self.reference): self.assertEqual((self.root/name).read_bytes(),self.skill_source()[name])
        state=updater.read_json(self.root/updater.STATE)
        self.assertEqual(state['officialSkill']['files'],{name:sha(self.skill_source()[name]) for name in (self.entry,self.reference)})
        self.assertNotIn(self.entry,state['managed'])
        self.assertEqual((self.root/'.agents/skills/other/SKILL.md').read_bytes(),b'owner skill')
        self.assertEqual((self.root/'docs/owner.md').read_bytes(),b'owner docs')
        before=self.files(); self.assertTrue(self.update(apply=True)['unchanged']); self.assertEqual(self.files(),before)
        self.assertEqual(updater.status(self.root)['officialSkill']['state'],'synchronized')
        self.assertIn(str(self.root/self.entry),updater.status(self.root)['next'])
        self.assertEqual(len(self.installs),1)

    def test_modified_or_deleted_tracked_skill_conflicts_and_status_reports_change(self):
        for name in (self.entry,self.reference):
            with self.subTest(name=name):
                self.adopt(); self.release(source=self.skill_source()); self.update(apply=True)
                previous=(self.root/name).read_bytes(); (self.root/name).write_bytes(b'owner modification')
                self.assertEqual(updater.status(self.root)['officialSkill']['state'],'modified')
                self.release(version='0.4.2-preview.1',native_version='0.4.2',source=self.skill_source(b'v2'))
                before=self.files()
                with self.assertRaisesRegex(ValueError,'conflict|冲突'): self.update(apply=True)
                self.assertEqual(self.files(),before)
                (self.root/name).write_bytes(previous)

    def test_legacy_baseline_accepts_identical_new_bytes_but_preserves_unknown_skill(self):
        source=self.skill_source()
        self.adopt()
        for name in (self.entry,self.reference): self.write_owner(name,source[name])
        self.release(source=source)
        self.assertTrue(self.update(apply=True)['ok'])
        state=updater.read_json(self.root/updater.STATE); state.pop('officialSkill')
        (self.root/updater.STATE).write_text(json.dumps(state))
        self.write_owner(self.entry,b'owner unknown skill')
        self.release(version='0.4.2-preview.1',native_version='0.4.2',source=self.skill_source(b'v2'))
        before=self.files()
        with self.assertRaisesRegex(ValueError,'conflict|冲突'): self.update(apply=True)
        self.assertEqual(self.files(),before)

    def test_legacy_baseline_can_use_verified_archived_previous_release(self):
        self.adopt(); self.release(source=self.skill_source())
        self.update(apply=True)
        state=updater.read_json(self.root/updater.STATE); state.pop('officialSkill')
        (self.root/updater.STATE).write_text(json.dumps(state))
        self.release(version='0.4.2-preview.1',native_version='0.4.2',source=self.skill_source(b'v2'))
        report=self.update(apply=True)
        self.assertTrue(report['ok'],report)
        self.assertEqual((self.root/self.entry).read_bytes(),self.skill_source(b'v2')[self.entry])

    def test_unverified_legacy_staged_files_cannot_authorize_overwriting(self):
        self.adopt(); self.release(source=self.skill_source()); self.update(apply=True)
        state=updater.read_json(self.root/updater.STATE); state.pop('officialSkill')
        (self.root/updater.STATE).write_text(json.dumps(state))
        self.write_owner(self.entry,b'owner change')
        staged=self.root/'.runtime/product/releases/0.4.1-preview.1/source'/self.entry
        staged.write_bytes(b'owner change')
        self.release(version='0.4.2-preview.1',native_version='0.4.2',source=self.skill_source(b'v2'))
        with self.assertRaisesRegex(ValueError,'conflict|冲突'): self.update(apply=True)
        self.assertEqual((self.root/self.entry).read_bytes(),b'owner change')

    def test_failed_installer_restores_skill_references_and_baseline(self):
        self.adopt(); self.release(source=self.skill_source()); self.update(apply=True)
        old_state=(self.root/updater.STATE).read_bytes()
        self.release(version='0.4.2-preview.1',native_version='0.4.2',source=self.skill_source(b'v2'))
        def fail(path): raise RuntimeError('installer failed')
        result=updater.update(self.root,release_dir=self.assets,target='windows-x64',apply=True,installer=fail)
        self.assertFalse(result['ok'])
        self.assertEqual((self.root/updater.STATE).read_bytes(),old_state)
        for name in (self.entry,self.reference): self.assertEqual((self.root/name).read_bytes(),self.skill_source()[name])
        self.assertEqual(updater.status(self.root)['officialSkill']['state'],'synchronized')

    def test_old_source_without_skill_is_explicitly_unsupported(self):
        self.adopt(); self.release()
        self.assertEqual(self.update()['officialSkill']['state'],'unsupported')
        self.assertEqual(self.update(apply=True)['officialSkill']['state'],'unsupported')
        self.assertEqual(updater.status(self.root)['officialSkill']['state'],'unsupported')

    def test_missing_reference_or_entry_stops_before_mutation(self):
        self.adopt()
        for missing in (self.entry,self.reference):
            with self.subTest(missing=missing):
                source=self.skill_source(); source.pop(missing); self.release(source=source)
                before=self.files()
                with self.assertRaisesRegex(ValueError,'Skill|skill|引用|reference'): self.update(apply=True)
                self.assertEqual(self.files(),before)

    def test_redirected_official_skill_parent_stops_before_mutation(self):
        self.adopt(); self.release(source=self.skill_source())
        original=updater.redirected
        with patch.object(updater,'redirected',side_effect=lambda path:path==self.root/'.agents' or original(path)):
            with self.assertRaisesRegex(ValueError,'链接|junction|重定向'): self.update(apply=True)
        self.assertEqual(self.installs,[])

    def test_same_version_legacy_baseline_migration_does_not_reinstall_app(self):
        self.adopt(); self.release(source=self.skill_source()); self.update(apply=True)
        state=updater.read_json(self.root/updater.STATE); state.pop('officialSkill')
        (self.root/updater.STATE).write_text(json.dumps(state))
        before=self.files(); self.update()
        self.assertEqual(self.files(),before)
        report=self.update(apply=True)
        self.assertTrue(report['ok'],report)
        self.assertEqual(report['binaryInstall'],'not-attempted')
        self.assertEqual(len(self.installs),1)
        self.assertEqual(updater.status(self.root)['officialSkill']['state'],'synchronized')

    def test_same_version_adds_previously_unmanaged_missing_skill_without_reinstall(self):
        self.adopt(); self.release(source=self.skill_source()); self.update(apply=True)
        state=updater.read_json(self.root/updater.STATE); state.pop('officialSkill')
        (self.root/updater.STATE).write_text(json.dumps(state))
        for name in (self.entry,self.reference): (self.root/name).unlink()
        report=self.update(apply=True)
        self.assertTrue(report['ok'],report)
        self.assertEqual(report['officialSkill']['state'],'synchronized')
        self.assertEqual(report['binaryInstall'],'not-attempted')
        self.assertEqual(len(self.installs),1)
        for name in (self.entry,self.reference): self.assertEqual((self.root/name).read_bytes(),self.skill_source()[name])

    def test_installer_skill_mutation_cannot_report_success(self):
        self.adopt(); self.release(source=self.skill_source())
        def mutate(path): (self.root/self.reference).write_bytes(b'changed during install')
        result=updater.update(self.root,release_dir=self.assets,target='windows-x64',apply=True,installer=mutate)
        self.assertFalse(result['ok'])
        self.assertFalse((self.root/self.entry).exists())

    def test_removed_reference_is_deleted_and_restored_if_install_fails(self):
        self.adopt(); old=self.skill_source(); self.release(source=old); self.update(apply=True)
        new={'scripts/shared.py':b'new code',self.entry:b'# Account v2; no reference required\n'}
        self.release(version='0.4.2-preview.1',native_version='0.4.2',source=new)
        self.assertIn(self.reference,self.update()['removed'])
        def fail(path): raise RuntimeError('installer failed')
        result=updater.update(self.root,release_dir=self.assets,target='windows-x64',apply=True,installer=fail)
        self.assertFalse(result['ok']); self.assertEqual((self.root/self.reference).read_bytes(),old[self.reference])
        self.assertTrue(self.update(apply=True)['ok']); self.assertFalse((self.root/self.reference).exists())

    def test_later_source_without_skill_retains_previous_skill_but_reports_unsupported(self):
        self.adopt(); old=self.skill_source(); self.release(source=old); self.update(apply=True)
        self.release(version='0.4.2-preview.1',native_version='0.4.2')
        report=self.update(apply=True)
        self.assertTrue(report['ok'],report); self.assertEqual(report['officialSkill']['state'],'unsupported')
        for name in (self.entry,self.reference): self.assertEqual((self.root/name).read_bytes(),old[name])
        self.assertEqual(updater.status(self.root)['officialSkill']['state'],'unsupported')

    def test_legacy_migration_without_archive_refuses_unproven_old_bytes(self):
        self.adopt(); self.release(source=self.skill_source()); self.update(apply=True)
        state=updater.read_json(self.root/updater.STATE); state.pop('officialSkill')
        (self.root/updater.STATE).write_text(json.dumps(state))
        (self.root/'.runtime/product/releases/0.4.1-preview.1/source.zip').unlink()
        self.release(version='0.4.2-preview.1',native_version='0.4.2',source=self.skill_source(b'v2'))
        before=self.files()
        with self.assertRaisesRegex(ValueError,'conflict|冲突'): self.update(apply=True)
        self.assertEqual(self.files(),before)

    def test_identical_new_skill_bytes_do_not_require_a_trusted_previous_archive(self):
        self.adopt(); self.release(source=self.skill_source()); self.update(apply=True)
        state=updater.read_json(self.root/updater.STATE); state.pop('officialSkill')
        (self.root/updater.STATE).write_text(json.dumps(state))
        (self.root/'.runtime/product/releases/0.4.1-preview.1/source.zip').write_bytes(b'untrusted old archive')
        source=self.skill_source(b'v2')
        for name in (self.entry,self.reference): self.write_owner(name,source[name])
        self.release(version='0.4.2-preview.1',native_version='0.4.2',source=source)
        self.assertTrue(self.update(apply=True)['ok'])

    def test_reference_dependency_cannot_escape_to_other_skill_or_missing_generic_doc(self):
        self.adopt()
        for target in ('../../other/SKILL.md','../../../../docs/missing.md','file:///secret.md'):
            with self.subTest(target=target):
                source=self.skill_source(); source[self.reference]=('[Dependency]('+target+')').encode()
                self.release(source=source); before=self.files()
                with self.assertRaisesRegex(ValueError,'Skill|引用'): self.update(apply=True)
                self.assertEqual(self.files(),before)

    def test_status_reports_release_docs_and_recovery_guards_readonly(self):
        self.adopt(); self.release(); report=self.update(apply=True)
        state=updater.status(self.root)
        self.assertEqual(state['releaseDocs'],report['releaseDocs'])
        self.assertFalse(state['pending']); self.assertFalse(state['updateLocked'])
        (self.root/'.runtime/product/pending.json').write_text('{}')
        self.assertTrue(updater.status(self.root)['pending'])


if __name__=='__main__': unittest.main()
