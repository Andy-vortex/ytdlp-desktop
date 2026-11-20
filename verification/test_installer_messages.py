# quick
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



class Killed(BaseException):
    """Stands in for the process being killed: nothing after it runs (not even an except Exception)."""


def die(*_args, **_kwargs):
    raise Killed


class FolderInstallSafety(unittest.TestCase):
    """(2.0.0 final review, simplicity 3.2) The folder-layout paths that had no test: a failed post-install gate rolls
    back to the previous program, a run killed half way through the renames is undone by the next run, and an
    install whose gate never finished is rolled back before the next install.  Each one ends with the previous
    program's exact files in place (PLAN section 3, item 7)."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='installer-safety-'))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.target = self.work / APP
        self.target.mkdir()
        self.stage = self.work / 'stage'
        self.stage.mkdir()

    def tree(self, folder=None):
        """{relative path: bytes} of every file under ``folder`` (default: the target)."""
        folder = folder or self.target
        return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob('*')) if path.is_file()}

    def install(self, version, gate=None, **patches):
        info = {'version': version, 'files': 2, 'size': 4, 'exe_sha256': '0' * 64}

        def copy_new_program(_stage, target, _info):
            (target / installer.NEW_INTERNAL).mkdir()
            (target / installer.NEW_INTERNAL / 'lib.bin').write_bytes(f'lib {version}'.encode())
            (target / installer.NEW_EXE).write_bytes(f'MZ {version} '.encode() + os.urandom(16))

        results = {'self_test_seconds': 1.0, 'download': {'files': ['fixture [fixture].wav']}, 'time_to_window': 1.0}
        gate_patch = {'side_effect': gate} if gate is not None else {'return_value': results}
        output = io.StringIO()
        try:
            with patch.object(installer, 'check_stage', return_value=info), \
                    patch.object(installer, 'load_gate', return_value=None), \
                    patch.object(installer, 'refuse_if_running'), \
                    patch.object(installer, 'copy_new_program', side_effect=copy_new_program), \
                    patch.object(installer, 'program_version', return_value=None), \
                    patch.object(installer, 'post_install_gate', **gate_patch), \
                    contextlib.ExitStack() as stack, contextlib.redirect_stdout(output):
                for name, value in patches.items():
                    stack.enter_context(patch.object(installer, name, value))
                code = installer.main(['--stage', str(self.stage), '--target', str(self.target),
                                       '--instance-name', 'Local\\YtDlpDesktop.Test.' + uuid.uuid4().hex[:12]])
        finally:
            self.last_output = output.getvalue()
        return code, output.getvalue()

    def receipt(self):
        return json.loads((self.target / installer.RECEIPT).read_text(encoding='utf-8'))

    def installed_twice(self):
        """2.0.0 over nothing, then 2.0.1 over it: a folder build with a kept previous folder build."""
        for version in ('2.0.0', '2.0.1'):
            code, text = self.install(version)
            self.assertEqual(code, 0, text)
        self.assertEqual(self.receipt()['previous']['layout'], 'onedir')
        return self.tree()

    def test_a_failed_gate_rolls_back_to_the_previous_program(self):
        before = self.installed_twice()
        code, text = self.install('2.0.2', gate=RuntimeError('the self-test window never opened'))
        self.assertEqual(code, 2, text)
        self.assertIn('安装后自检失败：the self-test window never opened', text)
        self.assertIn('已自动回滚到安装前的状态。', text)
        rolled = '_rolledback-2.0.2'
        self.assertTrue((self.target / rolled / EXE).read_bytes().startswith(b'MZ 2.0.2 '),
                        'the unchecked program is set aside')
        # the active program is exactly 2.0.1 again (its receipt is back too; the 2.0.0 kept behind it was let go
        # when 2.0.2 was committed: one previous program is kept, PLAN section 3, item 7)
        def program(tree):
            return {name: data for name, data in tree.items()
                    if not name.startswith((rolled, installer.PREVIOUS, 'install-receipt'))}
        self.assertEqual(program(self.tree()), program(before))
        self.assertEqual(set(program(before)), {EXE, str(Path(installer.INTERNAL, 'lib.bin'))})
        self.assertEqual(self.receipt()['installed'], '2.0.1')
        self.assertIs(self.receipt()['checked'], True)
        self.assertFalse((self.target / installer.JOURNAL).exists())
        for leftover in (installer.NEW_INTERNAL, installer.NEW_EXE, installer.PENDING_RECEIPT):
            self.assertFalse((self.target / leftover).exists(), leftover)
        # an interrupt (Ctrl+C) during the gate rolls back the same way and says so
        code, text = self.install('2.0.3', gate=KeyboardInterrupt())
        self.assertEqual(code, 130, text)
        self.assertIn('已自动回滚到安装前的状态。', text)
        self.assertEqual(self.receipt()['installed'], '2.0.1')

    def test_a_run_killed_during_the_renames_is_undone_by_the_next_run(self):
        before = self.installed_twice()
        real_save = installer.save_journal

        def save_then_die(target, data):
            real_save(target, data)
            if data['direction'] == 'forward' and data['done'] == 4:
                raise Killed                    # the old program is half moved aside; the new one not yet in

        with self.assertRaises(Killed):
            self.install('2.0.2', save_journal=save_then_die, reverse=die)
        self.assertTrue((self.target / installer.JOURNAL).is_file(), 'the journal is left behind')
        self.assertFalse((self.target / EXE).exists(), 'killed in the middle: no program in place')
        # the next run first undoes what the killed run did: exactly the files of 2.0.1 again, nothing left over
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            installer.recover(self.target, dry_run=False)
        self.assertIn('已撤销上次中断的安装', output.getvalue())
        self.assertEqual(self.tree(), before)
        # and an install run does the same before it starts, then installs
        with self.assertRaises(Killed):
            self.install('2.0.2', save_journal=save_then_die, reverse=die)
        code, text = self.install('2.0.3')
        self.assertEqual(code, 0, text)
        self.assertIn('已撤销上次中断的安装', text)
        self.assertEqual(self.receipt()['installed'], '2.0.3')
        kept = self.tree(self.target / installer.PREVIOUS)
        self.assertEqual(kept[EXE], before[EXE], 'the program set aside is 2.0.1, as it was')
        self.assertEqual(kept[str(Path(installer.INTERNAL, 'lib.bin'))], b'lib 2.0.1')

    def test_an_install_whose_gate_never_finished_is_rolled_back_first(self):
        before = self.installed_twice()
        with self.assertRaises(Killed):         # killed during the gate: 2.0.2 is active, its receipt unchecked
            self.install('2.0.2', gate=Killed(), abort_install=die)
        self.assertEqual(self.receipt()['installed'], '2.0.2')
        self.assertIs(self.receipt()['checked'], False)
        self.assertEqual(installer.unchecked_install(self.target), '2.0.2')
        code, text = self.install('2.0.3')
        self.assertEqual(code, 0, text)
        self.assertIn('程序目录里的 2.0.2 已经启用，但上次安装没有完成安装后自检', text)
        self.assertIn('已回滚上次未经自检的安装。', text)
        receipt = self.receipt()
        self.assertEqual((receipt['installed'], receipt['checked']), ('2.0.3', True))
        self.assertEqual(receipt['previous']['version'], '2.0.1', 'the unchecked 2.0.2 is not kept as the previous')
        kept = self.tree(self.target / installer.PREVIOUS)
        self.assertEqual(kept[EXE], before[EXE])
        self.assertTrue((self.target / '_rolledback-2.0.2' / EXE).read_bytes().startswith(b'MZ 2.0.2 '))


if __name__ == '__main__':
    unittest.main()
