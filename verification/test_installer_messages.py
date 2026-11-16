import _sandbox
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_release as installer  # noqa: E402

APP = installer.APP_NAME
EXE = installer.EXE_NAME
NAMED = re.compile(re.escape(APP) + r'-[0-9A-Za-z.\-旧版]{1,20}备份(?:-\d{8}-\d{6})?\.exe')


class BackupNamesInMessages(unittest.TestCase):
    """final-packaged-1: installer messages name the backup that really exists, never a fixed 1.5.1 name."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='installer-messages-'))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.target = self.work / APP
        self.target.mkdir()
        self.stage = self.work / 'stage'
        self.stage.mkdir()
        self.version = None                     # what the old program's own self-test reports

    # -- helpers ------------------------------------------------------------------------------------------
    def run_main(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = installer.main([*arguments, '--target', str(self.target),
                                   '--instance-name', 'Local\\YtDlpDesktop.Test.' + uuid.uuid4().hex[:12]])
        return code, output.getvalue()

    def install(self, *extra):
        info = {'version': '1.6.0', 'files': 2, 'size': 4, 'exe_sha256': '0' * 64}

        def copy_new_program(_stage, target, _info):
            (target / installer.NEW_INTERNAL).mkdir()
            (target / installer.NEW_INTERNAL / 'lib.bin').write_bytes(b'new')
            (target / installer.NEW_EXE).write_bytes(b'MZ new ' + os.urandom(16))

        gate = {'self_test_seconds': 1.0, 'download': {'files': ['fixture [fixture].wav']}, 'time_to_window': 1.0}
        with patch.object(installer, 'check_stage', return_value=info), \
                patch.object(installer, 'load_gate', return_value=None), \
                patch.object(installer, 'refuse_if_running'), \
                patch.object(installer, 'copy_new_program', side_effect=copy_new_program), \
                patch.object(installer, 'program_version', side_effect=lambda *_: self.version), \
                patch.object(installer, 'post_install_gate', return_value=gate):
            return self.run_main('--stage', str(self.stage), *extra)

    def assert_names_exist(self, text):
        for name in NAMED.findall(text):
            self.assertTrue((self.target / name).is_file(), f'the message names {name}, which does not exist:\n{text}')

    def onefile_owner_layout(self):
        (self.target / EXE).write_bytes(b'MZ old onefile ' + os.urandom(64))
        (self.target / f'{APP}-1.5.0备份.exe').write_bytes(b'MZ 1.5.0 ' + os.urandom(64))

    # -- tests --------------------------------------------------------------------------------------------
    def test_without_old_version_every_message_names_the_real_backup(self):
        self.onefile_owner_layout()
        code, text = self.install()
        self.assertEqual(code, 0, text)
        receipt = json.loads((self.target / installer.RECEIPT).read_text(encoding='utf-8'))
        self.assertEqual(receipt['previous']['exe'], f'{APP}-旧版备份.exe')
        self.assert_names_exist(text)

        code, text = self.install()             # onedir install: the closing text names the kept backup
        self.assertEqual(code, 0, text)
        closing = [line for line in text.splitlines() if '可删除' in line and installer.PREVIOUS in line]
        self.assertEqual(len(closing), 1, text)
        self.assertIn(f'{APP}-旧版备份.exe 仍可直接双击使用', closing[0])
        self.assertNotIn('1.5.0备份', closing[0])
        self.assertNotIn('1.5.1备份', text)
        self.assert_names_exist(text)

        for _ in range(2):
            code, text = self.run_main('--rollback')
            self.assertEqual(code, 0, text)
            self.assert_names_exist(text)
        code, text = self.run_main('--rollback')  # no receipt left: only the backup that is really there
        self.assertEqual(code, 1, text)
        self.assertIn('没有安装记录', text)
        self.assertIn(f'也可以直接双击程序目录里的 {APP}-1.5.0备份.exe 使用旧版', text)
        self.assertNotIn('1.5.1备份', text)
        self.assert_names_exist(text)

    def test_with_old_version_the_normal_owner_flow_still_names_the_1_5_1_backup(self):
        self.onefile_owner_layout()
        self.assertEqual(self.install('--old-version', '1.5.1')[0], 0)
        code, text = self.install('--old-version', '1.5.1')
        self.assertEqual(code, 0, text)
        self.assertIn(f'{APP}-1.5.1备份.exe 仍可直接双击使用', text)
        self.assert_names_exist(text)

    def test_a_timestamped_backup_is_named_as_it_is(self):
        self.onefile_owner_layout()
        (self.target / f'{APP}-1.5.1备份.exe').write_bytes(b'MZ an older copy')
        self.install('--old-version', '1.5.1')
        receipt = json.loads((self.target / installer.RECEIPT).read_text(encoding='utf-8'))
        stamped = receipt['previous']['exe']
        self.assertRegex(stamped, r'-1\.5\.1备份-\d{8}-\d{6}\.exe$')
        code, text = self.install('--old-version', '1.5.1')
        self.assertEqual(code, 0, text)
        self.assertIn(f'{stamped} 仍可直接双击使用', text)

    def test_no_backup_at_all_leaves_the_sentence_out(self):
        self.version = None
        self.assertEqual(self.install()[0], 0)  # layout none: no old program, no backup
        code, text = self.install()
        self.assertEqual(code, 0, text)
        closing = [line for line in text.splitlines() if '可删除' in line and installer.PREVIOUS in line]
        self.assertEqual(len(closing), 1, text)
        self.assertNotIn('备份', closing[0])
        self.assertTrue(closing[0].endswith('（删除后回滚命令无法再回到上一次安装）。'), closing[0])
        for _ in range(2):
            self.assertEqual(self.run_main('--rollback')[0], 0)
        code, text = self.run_main('--rollback')
        self.assertEqual(code, 1, text)
        self.assertNotIn('备份', text)
        self.assertIn('没有安装记录 install-receipt.json，无法回滚；未做任何更改。', text)

    def test_missing_files_refusal_names_only_an_existing_backup(self):
        self.onefile_owner_layout()
        self.assertEqual(self.install()[0], 0)
        (self.target / f'{APP}-旧版备份.exe').unlink()   # the owner deleted the recorded backup
        code, text = self.run_main('--rollback')
        self.assertEqual(code, 1, text)
        self.assertIn(f'安装记录提到的文件不存在：{APP}-旧版备份.exe；未做任何更改。', text)
        self.assertIn(f'脚本无法再回滚；{APP}-1.5.0备份.exe 仍可直接双击使用。', text)
        self.assertNotIn('1.5.1备份', text)

        (self.target / f'{APP}-1.5.0备份.exe').unlink()
        code, text = self.run_main('--rollback')
        self.assertEqual(code, 1, text)
        self.assertIn('（如果旧程序已被手动删除，脚本无法再回滚。）', text)

    def test_onedir_missing_previous_names_the_chained_backup(self):
        self.onefile_owner_layout()
        self.install('--old-version', '1.5.1')
        self.install('--old-version', '1.5.1')
        shutil.rmtree(self.target / installer.PREVIOUS)   # the owner deleted _previous
        code, text = self.run_main('--rollback')
        self.assertEqual(code, 1, text)
        self.assertIn('安装记录提到的文件不存在', text)
        self.assertIn(f'脚本无法再回滚；{APP}-1.5.0备份.exe、{APP}-1.5.1备份.exe 仍可直接双击使用。', text)
        self.assert_names_exist(text)

    def test_backups_to_name_prefers_the_receipt_and_ignores_other_files(self):
        (self.target / f'{APP}-旧版备份.exe').write_bytes(b'x')
        (self.target / f'{APP}-1.5.0备份.exe').write_bytes(b'y')
        (self.target / 'notes 备份.exe').write_bytes(b'z')
        (self.target / f'{APP}-1.4.0备份.exe').mkdir()          # a folder with the name is not a backup
        receipt = {'installed': '1.6.0', 'previous': {'layout': 'onefile', 'version': '旧版',
                                                      'exe': f'{APP}-旧版备份.exe', 'internal': None,
                                                      'receipt': None, 'exe_sha256': '0' * 64}}
        self.assertEqual(installer.backups_to_name(self.target, receipt), f'{APP}-旧版备份.exe')
        self.assertEqual(installer.backups_to_name(self.target),
                         f'{APP}-1.5.0备份.exe、{APP}-旧版备份.exe')
        self.assertEqual(installer.backups_to_name(self.target, {'broken': True}),
                         f'{APP}-1.5.0备份.exe、{APP}-旧版备份.exe')
        self.assertEqual(installer.backups_to_name(self.work / 'missing'), '')


if __name__ == '__main__':
    unittest.main()
