"""1.7.0 language adaptation: the labeled widgets of ui_theme (I18N.md 7, WP-L0).

    python -X utf8 -B test_i18n_widgets.py

option_menu(labels=...), Tabs(labels=...), segmented() and menu_width(): the widget shows a label,
the variable / get() / the callbacks keep the stored value or the tab id.  Without labels the
1.6.0 widgets are unchanged.  One hidden Tk root; no app module.
"""
import _sandbox  # noqa: F401
import tkinter
import unittest

import customtkinter as ctk

import ui_theme

ui_theme.enable_dpi_awareness()
ctk.set_appearance_mode('light')
ui_theme.apply_theme()


def shown(menu):
    return menu._text_label.cget('text')


class LabeledWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        for job in cls.root.tk.splitlist(cls.root.tk.call('after', 'info')):
            cls.root.tk.call('after', 'cancel', job)
        cls.root.destroy()

    def test_option_menu_without_labels_is_the_1_6_0_widget(self):
        variable = tkinter.StringVar(self.root, 'b')
        menu = ui_theme.option_menu(self.root, ('a', 'b'), variable=variable, width=120)
        self.assertIs(type(menu), ui_theme.AppOptionMenu)
        self.assertEqual((menu.get(), shown(menu), menu.cget('values')), ('b', 'b', ['a', 'b']))
        self.assertEqual((menu.cget('height'), menu.cget('width'), menu.cget('dynamic_resizing')), (36, 120, False))

    def test_option_menu_with_labels_shows_labels_and_stores_values(self):
        variable = tkinter.StringVar(self.root, 'audio_mp3')
        chosen = []
        values = ['audio_best', 'audio_mp3']
        menu = ui_theme.option_menu(self.root, values, labels=['Audio · Original best', 'Audio · MP3 320K'],
                                    variable=variable, command=chosen.append, width=200)
        self.assertIsInstance(menu, ui_theme.AppOptionMenu)
        self.assertIsInstance(menu, ui_theme.LabeledOptionMenu)
        self.assertEqual(menu.cget('values'), ['Audio · Original best', 'Audio · MP3 320K'])
        self.assertEqual(menu.stored_values, values)
        self.assertEqual((menu.get(), shown(menu)), ('audio_mp3', 'Audio · MP3 320K'))
        self.assertIs(menu.cget('variable'), variable)
        self.assertEqual((menu.cget('height'), menu.cget('width')), (36, 200))
        menu._dropdown_callback('Audio · Original best')            # what a choice in the drop-down does
        self.assertEqual((variable.get(), menu.get(), shown(menu)),
                         ('audio_best', 'audio_best', 'Audio · Original best'))
        self.assertEqual(chosen, ['audio_best'])
        variable.set('audio_mp3')                              # a write to the variable is followed
        self.assertEqual((menu.get(), shown(menu)), ('audio_mp3', 'Audio · MP3 320K'))
        menu.set('audio_best')
        self.assertEqual((variable.get(), shown(menu)), ('audio_best', 'Audio · Original best'))
        menu.set('Audio · MP3 320K')                                 # a label is accepted too
        self.assertEqual(variable.get(), 'audio_mp3')
        variable.set('audio_flac')                                   # a value the menu does not list
        self.assertEqual((menu.get(), shown(menu)), ('audio_flac', 'audio_flac'))
        self.assertEqual(chosen, ['audio_best'], 'only a choice in the menu calls command')
        menu.destroy()
        variable.set('audio_best')                               # no trace left behind

    def test_option_menu_with_a_dict_and_numbers(self):
        variable = tkinter.IntVar(self.root, 3600)
        labels = {0: 'Manual only', 900: 'Every 15 minutes', 3600: 'Hourly', 86400: 'Daily'}
        menu = ui_theme.option_menu(self.root, list(labels), labels=labels, variable=variable)
        self.assertEqual((menu.get(), shown(menu)), (3600, 'Hourly'))
        menu._dropdown_callback('Daily')
        self.assertEqual(variable.get(), 86400)
        bare = ui_theme.option_menu(self.root, ['title', 'clean'], labels={'title': 'Title (recommended)'})
        self.assertEqual(bare.cget('values'), ['Title (recommended)', 'clean'])
        self.assertEqual((bare.get(), shown(bare)), ('title', 'Title (recommended)'))
        with self.assertRaises(ValueError):
            ui_theme.option_menu(self.root, ['a', 'b'], labels=['only one'])

    def test_menu_width_fits_the_widest_label(self):
        narrow = ui_theme.menu_width(self.root, ['Best'])
        wide = ui_theme.menu_width(self.root, ['Best', 'MP4 · most compatible (H.264)'])
        self.assertGreater(wide, narrow)
        self.assertEqual(ui_theme.menu_width(self.root, {'a': 'Best'}), narrow)
        scale = ctk.ScalingTracker.get_widget_scaling(self.root)
        font = ui_theme._measure_font(self.root, ui_theme.font('body'))
        text = font.measure('MP4 · most compatible (H.264)') / scale
        self.assertGreaterEqual(wide, text + ui_theme.CONTROL_H)
        self.assertLess(wide, text + ui_theme.CONTROL_H + 40)
        self.assertGreaterEqual(ui_theme.menu_width(self.root, []), ui_theme.CONTROL_H)

    def test_tabs_with_labels_keep_the_ids(self):
        clicked = []
        ids = ['任务历史与续采', '增量订阅', '统一资料归档']
        labels = {'任务历史与续采': 'History & Resume', '增量订阅': 'Subscriptions', '统一资料归档': 'Archive'}
        tabs = ui_theme.tabs(self.root, ids, labels=labels, command=clicked.append)
        self.assertEqual(tabs._bar.cget('values'), ['History & Resume', 'Subscriptions', 'Archive'])
        self.assertEqual(tabs.get(), '任务历史与续采')
        self.assertEqual(tabs._bar.get(), 'History & Resume')
        tabs.set('增量订阅')
        self.assertEqual((tabs.get(), tabs._bar.get()), ('增量订阅', 'Subscriptions'))
        tabs._clicked('Archive')                     # what a click on the bar hands over
        self.assertEqual((tabs.get(), clicked), ('统一资料归档', ['统一资料归档']))
        tabs._clicked('任务历史与续采')               # an id is accepted as it is
        self.assertEqual(clicked[-1], '任务历史与续采')
        self.assertIs(tabs.tab('增量订阅'), tabs.add('增量订阅'))
        tabs.add('新')
        self.assertEqual(tabs._bar.cget('values')[-1], '新', 'a tab without a label shows its id')
        with self.assertRaises(ValueError):
            tabs.set('Archive')                      # labels are not ids
        listed = ui_theme.Tabs(self.root, ['a', 'b'], labels=['A', 'B'])
        self.assertEqual((listed._bar.cget('values'), listed.get()), (['A', 'B'], 'a'))
        plain = ui_theme.tabs(self.root, ['一', '二'])
        self.assertEqual((plain._bar.cget('values'), plain.get()), (['一', '二'], '一'))

    def test_segmented_shows_labels_and_hands_over_values(self):
        chosen = []
        bar = ui_theme.segmented(self.root, ['system', 'zh', 'en'], ['System', '简体中文', 'English'],
                                 command=chosen.append)
        self.assertIsInstance(bar, ctk.CTkSegmentedButton)
        self.assertEqual(bar.cget('values'), ['System', '简体中文', 'English'])
        self.assertEqual(bar.stored_values, ['system', 'zh', 'en'])
        self.assertEqual(bar.cget('height'), 32)
        self.assertIs(bar.cget('font'), ui_theme.font('body'))
        bar.set('zh')
        self.assertEqual((bar.get(), super(ui_theme.LabeledSegmentedButton, bar).get()), ('zh', '简体中文'))
        self.assertEqual(chosen, [], 'set() is not a click')
        bar._buttons_dict['English'].invoke()        # a click on a segment
        self.assertEqual((bar.get(), chosen), ('en', ['en']))
        bar.set('System')                            # a label is accepted too
        self.assertEqual(bar.get(), 'system')
        appearance = ui_theme.segmented(self.root, ['light', 'dark', 'system'],
                                        {'light': '浅色', 'dark': '深色', 'system': '跟随系统'})
        appearance.set('dark')
        self.assertEqual((appearance.get(), appearance.cget('values')), ('dark', ['浅色', '深色', '跟随系统']))


if __name__ == '__main__':
    unittest.main()
