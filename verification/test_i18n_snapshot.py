# tier: all
"""1.7.0 language adaptation: the snapshot assertions in both languages (I18N.md 11.5).

    python -X utf8 -B test_i18n_snapshot.py

snapshot_ui.run(..., language=...) for every page and library tab at 1000x740, 1120x810 and
2560x1392, light and dark, states idle, active, bot and filled: assertions (1) to (8) of 1.6.0 plus
(9) no text cut without an ellipsis, (9b) nothing ends outside its card, (10) the sidebar texts and
(11) the task title in the active scene, (12) the subscription strip and the settings cards.

Chinese must give the 1.6.0 results (no failure); English runs since the L2 merge of L1-A and L1-B
(the UI_PENDING skip flag was removed there, I18N.md 13).

(1.8.0 WP-6, MESSAGES.md 7.6) The English runs add assertion (13): no Chinese outside the user data of
the snapshot fixtures on any captured page, tab or state.  Its self-tests: the text rule (an app text
is flagged, user data, the language names and the naming hint's brackets are not), USER_DATA holds only
fixture texts that are no app text, and a run with one English catalog text put back to Chinese fails
with a (13) message (a Chinese run never checks (13)).
No captures here (shots=False); the PNGs come from the command line:
    snapshot_ui.py --language both --appearance both --state idle|active|bot|filled --out DIR
"""
import _sandbox
import unittest
import unittest.mock

import snapshot_ui

STATES = ('idle', 'active', 'bot', 'filled')


class SnapshotCase(unittest.TestCase):
    language = None

    def check(self, state):
        failures = []
        for appearance in snapshot_ui.APPEARANCES:
            found = snapshot_ui.run(pages=list(snapshot_ui.PAGES), sizes=snapshot_ui.SIZES, shots=False,
                                    state=state, appearance=appearance, language=self.language)
            failures += [f'[{self.language} {appearance} {state}] {failure}' for failure in found]
        for failure in failures:
            print('FAIL', failure, flush=True)
        self.assertEqual(failures, [])


class ChineseSnapshotTests(SnapshotCase):
    """Chinese: the 1.6.0 results, also under the new assertions (9) to (12)."""
    language = 'zh'

    def test_idle(self):
        self.check('idle')

    def test_active(self):
        self.check('active')

    def test_bot(self):
        self.check('bot')

    def test_filled(self):
        self.check('filled')


class EnglishSnapshotTests(SnapshotCase):
    language = 'en'

    def test_idle(self):
        self.check('idle')

    def test_active(self):
        self.check('active')

    def test_bot(self):
        self.check('bot')

    def test_filled(self):
        self.check('filled')


class ChineseOutsideUserDataTests(unittest.TestCase):
    """(13) self-tests."""

    def test_the_text_rule(self):
        users = snapshot_ui.user_data()
        for text in ('Downloaded: 0 · Last error: The page is larger than 8 MB.',
                     '徐秉龙 - 双子 【Lyrics MV】「每日歌曲推荐 #63」 and others (3 links)',
                     'Subscription: 城市夜谈', 'Language / 语言', '简体中文', 'e.g. 【MV】 or 『Live』',
                     'File saved: C:\\x\\白羊.m4a'):
            self.assertEqual(snapshot_ui.chinese_left(text, users), '', text)
        for text, left in (('媒体直链', '媒体直链'), ('徐秉龙 - 双子 【Lyrics MV】 等 3 条', '等条'), ('Subscription: 订阅', '订阅'),
                           ('第 1 页', '页')):
            self.assertEqual(snapshot_ui.chinese_left(text, users), left, text)

    def test_user_data_holds_only_fixture_texts_that_are_no_app_text(self):
        self.assertIn('城市夜谈', snapshot_ui.user_data())
        for entry in ('媒体直链', '订阅：城市夜谈'):
            with self.subTest(entry=entry), \
                    unittest.mock.patch.object(snapshot_ui, 'USER_DATA', snapshot_ui.USER_DATA + (entry,)), \
                    self.assertRaises(AssertionError):
                snapshot_ui.user_data()

    def test_a_run_finds_a_chinese_text_on_an_english_page(self):
        import i18n
        saved = i18n.CATALOG['en']['download.title.ready']
        i18n.CATALOG['en']['download.title.ready'] = i18n.CATALOG['zh']['download.title.ready']
        try:
            english = snapshot_ui.run(pages=['download'], sizes=('1000x740',), shots=False, language='en')
            chinese = snapshot_ui.run(pages=['download'], sizes=('1000x740',), shots=False, language='zh')
        finally:
            i18n.CATALOG['en']['download.title.ready'] = saved
        found = [failure for failure in english if '(13)' in failure]
        self.assertTrue(found, english)
        self.assertTrue(all(i18n.CATALOG['zh']['download.title.ready'] in failure for failure in found), found)
        self.assertFalse([failure for failure in chinese if '(13)' in failure])


class FlagTests(unittest.TestCase):
    def test_the_runs_are_in_the_language_they_name(self):
        """A run in English really is English for the app it builds, and restores the language."""
        import i18n
        seen = []
        original = snapshot_ui._run

        def spy(*args):
            seen.append(i18n.language())
            return []
        snapshot_ui._run = spy
        try:
            before = i18n.language()
            self.assertEqual(snapshot_ui.run(pages=['logs'], sizes=('1000x740',), shots=False, language='en'), [])
            self.assertEqual(snapshot_ui.run(pages=['logs'], sizes=('1000x740',), shots=False, language='zh'), [])
            self.assertEqual(snapshot_ui.run(pages=['logs'], sizes=('1000x740',), shots=False), [])
            self.assertEqual(seen, ['en', 'zh', before])
            self.assertEqual(i18n.language(), before)
            with self.assertRaises(ValueError):
                snapshot_ui.run(language='fr')
        finally:
            snapshot_ui._run = original
        self.assertEqual(snapshot_ui.shot_name('download-1000x740.png', 'dark', 'en'), 'download-1000x740-dark-en.png')
        self.assertEqual(snapshot_ui.shot_name('download-1000x740.png', 'light', 'zh'), 'download-1000x740.png')
        self.assertEqual(snapshot_ui.shot_name('download-1000x740.png', 'dark'), 'download-1000x740-dark.png')


if __name__ == '__main__':
    unittest.main()
