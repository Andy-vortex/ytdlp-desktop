"""1.7.0 merge of fix-core and fix-ui (docs/1.7.0/handoff/fix-merge.md): the cross-team requests.

R1  恢复内置引擎: when updater.restore_bundled() raises (MSG_RESTORE, the updated engine is still in
    use), the window shows the warning, refreshes the engine labels and says nothing else; the
    success path is as in 1.6.0.
R2  on_queued uses the additive 'items' of the queued event (fix-core C4): a link queued in two
    formats gets a row per format; a 1.6.0 payload without 'items' keeps the job's base options.
R3  The merged library.py: one queued event with the same link in two formats keeps both in the
    record (core), while a public method on the window's thread is still the short UI_RETRIED
    wrapper (ui).
Stand-in objects only: no window, no engine, no network.
"""
import _sandbox
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import engine
import library
import messages
import ui_download
import ui_settings
import updater
from engine import Options
from ui_download import DownloadUI
from ui_settings import SettingsUI


def settings_window():
    calls = []
    window = types.SimpleNamespace(busy=False, update_status=mock.Mock(), calls=calls)
    window.refresh_engine_labels = lambda: calls.append('labels')
    window.append_log = lambda text: calls.append(('log', text))
    return window


class RestoreEngineTests(unittest.TestCase):
    def test_a_failed_restore_shows_the_warning_and_refreshes_the_labels_only(self):
        window = settings_window()
        # (1.8.0) what restore_bundled raises: the message of MSG_RESTORE (C-F46, MESSAGES.md 3.3)
        error = updater.UpdateError(messages.msg('core.update.restore_failed'))
        with mock.patch.object(ui_settings.messagebox, 'askyesno', return_value=True), \
                mock.patch.object(ui_settings.messagebox, 'showwarning') as warning, \
                mock.patch.object(updater, 'restore_bundled', side_effect=error):
            SettingsUI.restore_engine(window)
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[:2], ('恢复未完成', updater.MSG_RESTORE))
        self.assertEqual(window.calls, ['labels'])
        window.update_status.configure.assert_not_called()

    def test_a_restore_that_worked_says_so_as_before(self):
        window = settings_window()
        with mock.patch.object(ui_settings.messagebox, 'askyesno', return_value=True), \
                mock.patch.object(ui_settings.messagebox, 'showwarning') as warning, \
                mock.patch.object(updater, 'restore_bundled', return_value=True), \
                mock.patch.object(ui_settings, 'engine_version', return_value='2026.10.21'):
            SettingsUI.restore_engine(window)
        warning.assert_not_called()
        self.assertEqual(window.calls, ['labels', ('log', '已恢复内置引擎：yt-dlp 2026.10.21')])
        self.assertEqual(window.update_status.configure.call_args.kwargs['text'], '已改用内置引擎。')

    def test_no_is_still_no(self):
        window = settings_window()
        with mock.patch.object(ui_settings.messagebox, 'askyesno', return_value=False), \
                mock.patch.object(updater, 'restore_bundled') as restore:
            SettingsUI.restore_engine(window)
        restore.assert_not_called()
        self.assertEqual(window.calls, [])


class QueuedRowsTests(unittest.TestCase):
    def window(self, rows, base):
        appended = []
        window = types.SimpleNamespace(job_options=[base], appended=appended, totals=0)
        window._tail_start = lambda: rows
        window.append_rows = lambda urls, options=None, overrides=None: appended.append((list(urls), options))

        def totals():
            window.totals += 1
        window.totals_changed = totals
        window._queued_options = lambda entry, own: DownloadUI._queued_options(window, entry, own)
        return window

    def test_a_link_queued_in_two_formats_gets_a_row_per_format(self):
        base = Options(output=Path(tempfile.gettempdir()), mode=engine.MODES[0], proxy='http://p:1', cookies='c.txt')
        mp3 = [key for key in engine.MODES if key != base.mode][0]
        other = Options(output=base.output, mode=mp3).to_dict()
        window = self.window(1, base)
        DownloadUI.on_queued(window, {'urls': ['https://a', 'https://a', 'https://b'], 'first_index': 2, 'total': 4,
                                      'items': [{'url': 'https://a', 'format': base.mode},
                                                {'url': 'https://a', 'format': mp3, 'options': other},
                                                {'url': 'https://b', 'format': mp3, 'options': other}]})
        self.assertEqual([urls for urls, _ in window.appended], [['https://a'], ['https://a', 'https://b']])
        self.assertIs(window.appended[0][1], base)
        second = window.appended[1][1]
        self.assertEqual(second.mode, mp3)
        self.assertEqual((second.proxy, second.cookies), ('http://p:1', 'c.txt'))   # session values of the job
        self.assertEqual(window.totals, 1)

    def test_only_the_missing_rows_are_appended(self):
        base = Options(output=Path(tempfile.gettempdir()))
        mp3 = [key for key in engine.MODES if key != base.mode][0]
        other = Options(output=base.output, mode=mp3).to_dict()
        window = self.window(2, base)        # the window appended the first link itself
        DownloadUI.on_queued(window, {'urls': ['https://a', 'https://a'], 'first_index': 2, 'total': 3,
                                      'items': [{'url': 'https://a', 'format': base.mode},
                                                {'url': 'https://a', 'format': mp3, 'options': other}]})
        self.assertEqual(len(window.appended), 1)
        self.assertEqual(window.appended[0][0], ['https://a'])
        self.assertEqual(window.appended[0][1].mode, mp3)

    def test_a_1_6_0_payload_and_unusable_options_keep_the_base(self):
        base = Options(output=Path(tempfile.gettempdir()))
        window = self.window(0, base)
        DownloadUI.on_queued(window, {'urls': ['https://a', 'https://b'], 'first_index': 1, 'total': 2})
        self.assertEqual(window.appended, [(['https://a', 'https://b'], base)])
        window = self.window(0, base)
        DownloadUI.on_queued(window, {'urls': ['https://a'], 'first_index': 1, 'total': 1,
                                      'items': [{'url': 'https://a', 'format': 'x', 'options': {'mode': 'nope'}}]})
        self.assertEqual(window.appended, [(['https://a'], base)])


class MergedLibraryTests(unittest.TestCase):
    def test_both_teams_changes_are_in_the_merged_library(self):
        folder = tempfile.mkdtemp(prefix='merge-lib-')
        store = library.Library(os.path.join(folder, 'library.sqlite3'))
        base = Options(output=Path(folder))
        mp3 = [key for key in engine.MODES if key != base.mode][0]
        job = store.create_job('download', 'q', dict(base.to_dict(), urls=['https://base']))
        store.download_event(job, 'queued', {
            'urls': ['https://a', 'https://a'], 'first_index': 2, 'total': 3,
            'overrides': {'https://a': Options(output=base.output, mode=mp3).to_dict()},
            'items': [{'url': 'https://a', 'format': base.mode},
                      {'url': 'https://a', 'format': mp3,
                       'options': Options(output=base.output, mode=mp3).to_dict()}]})
        downloads = library.job_downloads(store.job(job)['data'])
        self.assertEqual([entry['url'] for entry in downloads], ['https://base', 'https://a', 'https://a'])
        self.assertEqual(downloads[0]['format'], downloads[1]['format'])
        self.assertNotEqual(downloads[1]['format'], downloads[2]['format'])
        # The UI team's wrapper is still around every public method that opens the database.
        for name in library.UI_RETRIED:
            self.assertTrue(callable(getattr(library.Library, name)), name)
        self.assertIn('download_event', library.UI_RETRIED)
        self.assertIn('history_count', library.UI_RETRIED)


if __name__ == '__main__':
    unittest.main()
