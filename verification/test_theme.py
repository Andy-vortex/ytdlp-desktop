"""Tests of ui_theme, the design system (WP3).

    python -X utf8 -B test_theme.py

Imports ui_theme only, never an app module and never _sandbox: nothing here reads or writes
application state. Exactly one Tk root is alive at any time: customtkinter draws every image
into the default root, so two live roots cannot both show icons.
"""
import ast
import ctypes
import gc
import json
import os
import sys
import time
import tkinter
import types
import unittest
import weakref
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from unittest import mock

sys.dont_write_bytecode = True         # never leave a __pycache__ in src/
SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))

import customtkinter as ctk  # noqa: E402

import ui_theme  # noqa: E402

APP_MODULES = ('engine', 'paths', 'settings', 'studio', 'progress')
LOADED_WITH_THEME = [name for name in APP_MODULES if name in sys.modules]

ui_theme.enable_dpi_awareness()         # before the first Tk root of this process
ctk.set_appearance_mode('light')
ui_theme.apply_theme()

ICON_CODES = {name: value for name, value in vars(ui_theme).items()
              if name.startswith('ICON_') and isinstance(value, int) and value >= 0xE000}
TOKENS = {name: value for name, value in vars(ui_theme).items()
          if name.isupper() and isinstance(value, tuple) and len(value) == 2
          and all(isinstance(part, str) and part.startswith('#') for part in value)}
LONG = ('每条下载之间随机等待 10–20 秒，遇到网站限流时暂停队列并逐步延长重试间隔；失败的链接会保存下来，'
        '下次打开仍可重试。关闭后下载更快，但批量下载时更容易被网站临时限制。')


def hidden_root():
    """A root that never appears: enough for everything that needs no real layout."""
    root = ctk.CTk()
    root.withdraw()
    return root


def laid_out_root(size):
    """A mapped but invisible root (no frame, no taskbar button, alpha 0) for geometry tests."""
    root = ctk.CTk()
    root.overrideredirect(True)
    root.attributes('-alpha', 0.0)
    root.geometry(size + '+0+0')
    return root


def close(root):
    """Destroy a root together with its pending timers.

    customtkinter leaves `after` scripts behind (DPI polling, title bar icon). They would
    fire while a later root runs its event loop and print `invalid command name`.
    """
    for job in root.tk.splitlist(root.tk.call('after', 'info')):
        root.tk.call('after', 'cancel', job)    # the raw call: each widget deletes its own commands
    root.destroy()


def settle(root, rounds=6):
    for _ in range(rounds):
        root.update()


def scaling(widget):
    return ctk.ScalingTracker.get_widget_scaling(widget)


def release_size(root, timeout=3.0):
    """Wait until a window can be resized again after a scaling change.

    customtkinter pins a window to its current size (minimum = maximum) when the scaling
    changes and lifts that one second later. A test that resizes the root afterwards would
    get the old size.
    """
    end = time.monotonic() + timeout
    while tkinter.Wm.wm_minsize(root) == tkinter.Wm.wm_maxsize(root):
        if time.monotonic() > end:
            raise AssertionError('the window size is still pinned after a scaling change')
        root.update()
        time.sleep(0.02)


def exact_font(root, description):
    """A Tk font for a description such as ('Microsoft YaHei UI', -26, 'bold'), at exactly that pixel size.

    tkinter's Font(font=description) converts the size to whole points and comes out smaller.
    """
    parts = root.tk.splitlist(description) if isinstance(description, str) else tuple(description)
    styles = ' '.join(str(part) for part in parts[2:]).split()
    return tkfont.Font(root=root, family=parts[0], size=int(parts[1]), weight='bold' if 'bold' in styles else 'normal')


class A_TokensTest(unittest.TestCase):
    """Runs without any Tk root."""

    def test_every_token_is_a_light_dark_pair_of_hex_colours(self):
        self.assertEqual(len(TOKENS), 49)       # (WP7) + SEGMENT_ON, ACCENT_DISABLED_TEXT, SWITCH_KNOB_DISABLED
        for name, value in TOKENS.items():
            for part in value:
                self.assertRegex(part, r'^#[0-9A-F]{6}$', name)
        for name in ('BG', 'SURFACE', 'SURFACE_ALT', 'ROW', 'STRIPE', 'LINE', 'INK', 'MUTED', 'FAINT', 'ACCENT',
                     'ACCENT_HOVER', 'ACCENT_TEXT', 'ACCENT_SOFT', 'ACCENT_SOFT_HOVER', 'ACCENT_DISABLED',
                     'DISABLED_SOFT', 'OK', 'OK_SOFT', 'WARN', 'WARN_SOFT', 'DANGER', 'DANGER_SOFT',
                     'DANGER_SOFT_HOVER', 'IDLE_SOFT', 'SELECT', 'MENU', 'MENU_BUTTON', 'MENU_BUTTON_HOVER',
                     'CHECK_BORDER', 'SCROLL', 'SCROLL_HOVER', 'TRACK', 'SWITCH_TRACK', 'SWITCH_ON',
                     'SWITCH_KNOB_OFF', 'SIDE', 'SIDE_HOVER', 'SIDE_ACTIVE', 'SIDE_CARD', 'SIDE_TEXT', 'SIDE_MUTED',
                     'SIDE_OK', 'SIDE_INFO', 'SIDE_WARN', 'SIDE_DANGER', 'WHITE'):
            self.assertIn(name, TOKENS)

    def test_light_values_are_the_specified_ones(self):
        expected = {'BG': '#F4F7FC', 'SURFACE': '#FFFFFF', 'INK': '#18253B', 'MUTED': '#5B6780', 'ACCENT': '#3268EB',
                    'ACCENT_TEXT': '#2557D0', 'OK': '#0B6B4C', 'WARN': '#8F5408', 'DANGER': '#B93026',
                    'SIDE': '#111F35', 'SELECT': '#DCE7FF', 'SWITCH_KNOB_OFF': '#8A97AD'}
        for name, value in expected.items():
            self.assertEqual(TOKENS[name][0], value, name)

    def test_contrast_formula(self):
        self.assertAlmostEqual(ui_theme.contrast('#000000', '#FFFFFF'), 21.0, places=2)
        self.assertAlmostEqual(ui_theme.contrast('#FFFFFF', '#FFFFFF'), 1.0, places=5)
        measured = {('INK', 'SURFACE'): 15.4, ('MUTED', 'BG'): 5.29, ('MUTED', 'SURFACE_ALT'): 5.39,
                    ('MUTED', 'IDLE_SOFT'): 5.06, ('ACCENT_TEXT', 'ACCENT_SOFT'): 5.63,
                    ('ACCENT_TEXT', 'ACCENT_SOFT_HOVER'): 5.14, ('WHITE', 'ACCENT'): 4.87, ('OK', 'OK_SOFT'): 5.85,
                    ('WARN', 'WARN_SOFT'): 5.60, ('DANGER', 'DANGER_SOFT'): 5.23,
                    ('DANGER', 'DANGER_SOFT_HOVER'): 4.54, ('SIDE_MUTED', 'SIDE_CARD'): 5.12}
        for (fg, bg), value in measured.items():
            self.assertAlmostEqual(ui_theme.contrast(TOKENS[fg], TOKENS[bg]), value, delta=0.05, msg=f'{fg}/{bg}')
        self.assertAlmostEqual(ui_theme.contrast(ui_theme.INK, ui_theme.SURFACE, dark=True), 13.25, delta=0.05)  # WP7 palette

    def test_every_text_pair_reaches_4_5_in_light_mode(self):
        required = [('INK', 'SURFACE'), ('MUTED', 'BG'), ('MUTED', 'SURFACE_ALT'), ('MUTED', 'IDLE_SOFT'),
                    ('ACCENT_TEXT', 'ACCENT_SOFT'), ('ACCENT_TEXT', 'ACCENT_SOFT_HOVER'), ('WHITE', 'ACCENT'),
                    ('OK', 'OK_SOFT'), ('WARN', 'WARN_SOFT'), ('DANGER', 'DANGER_SOFT'),
                    ('DANGER', 'DANGER_SOFT_HOVER'), ('SIDE_MUTED', 'SIDE_CARD'), ('INK', 'BG'), ('INK', 'ROW'),
                    ('INK', 'SELECT'), ('MUTED', 'SURFACE'), ('MUTED', 'ROW'), ('ACCENT_TEXT', 'SURFACE'),
                    ('WHITE', 'ACCENT_HOVER'), ('SIDE_TEXT', 'SIDE'), ('SIDE_MUTED', 'SIDE')]
        pairs = [(fg, bg) for fg, bg in ui_theme.TEXT_PAIRS]
        for fg, bg in required:
            self.assertIn((TOKENS[fg], TOKENS[bg]), pairs, f'{fg}/{bg} missing from TEXT_PAIRS')
        for fg, bg in pairs:
            self.assertGreaterEqual(ui_theme.contrast(fg[0], bg[0]), 4.5, f'{fg[0]} on {bg[0]}')
        # Every chip tone and every button kind is covered by a pair.
        for tone in ('idle', 'info', 'ok', 'warn', 'danger'):
            self.assertIn(ui_theme.tone_colors(tone), pairs, tone)

    def test_constants(self):
        self.assertEqual(ui_theme.FAMILY, 'Microsoft YaHei UI')
        self.assertEqual(ui_theme.SIZES, {'caption': 12, 'body': 13, 'heading': 15, 'title': 22, 'brand': 17})
        self.assertEqual(ui_theme.LABEL_H, {'caption': 18, 'body': 22, 'heading': 22, 'title': 32, 'brand': 24})
        self.assertEqual((ui_theme.PAGE_PAD, ui_theme.CARD_PAD, ui_theme.GAP), (24, 16, 12))
        self.assertEqual((ui_theme.RADIUS_CARD, ui_theme.RADIUS, ui_theme.CONTROL_H, ui_theme.CONTROL_H_SM), (12, 8, 36, 28))
        self.assertEqual((ui_theme.SIDEBAR_W, ui_theme.ROW_H, ui_theme.PAGE_MAX), (184, 52, 1080))
        self.assertTrue(48 <= ui_theme.WHEEL_STEP <= 64)
        self.assertEqual({name: hex(value) for name, value in ICON_CODES.items()},
                         {'ICON_DOWNLOAD': '0xe896', 'ICON_SEARCH': '0xe721', 'ICON_LIBRARY': '0xe8f1',
                          'ICON_LOG': '0xe9f9', 'ICON_SETTINGS': '0xe713', 'ICON_DONE': '0xe930',
                          'ICON_CLOCK': '0xe823', 'ICON_ERROR': '0xe783', 'ICON_PAUSE': '0xe769',
                          'ICON_FOLDER': '0xe8b7', 'ICON_SKIP': '0xe72a',
                          # WP4 fix round 1 (visual-3): filled stop square, empty circle for "not started"
                          'ICON_STOP': '0xe73b', 'ICON_PENDING': '0xea3a'})

    def test_tone_colors(self):
        self.assertEqual(ui_theme.tone_colors('idle'), (ui_theme.MUTED, ui_theme.IDLE_SOFT))
        self.assertEqual(ui_theme.tone_colors('info'), (ui_theme.ACCENT_TEXT, ui_theme.ACCENT_SOFT))
        self.assertEqual(ui_theme.tone_colors('danger'), (ui_theme.DANGER, ui_theme.DANGER_SOFT))
        with self.assertRaises(ValueError):
            ui_theme.tone_colors('purple')

    def test_no_app_module_is_imported(self):
        self.assertEqual(LOADED_WITH_THEME, [])
        for name in APP_MODULES:
            self.assertNotIn(name, sys.modules)

    def test_module_imports_only_stdlib_tkinter_customtkinter_and_pil(self):
        tree = ast.parse((SRC / 'ui_theme.py').read_text(encoding='utf-8'))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                roots.add(node.module.split('.')[0])
        foreign = roots - set(sys.stdlib_module_names) - {'customtkinter', 'PIL'}
        self.assertEqual(foreign, set())
        self.assertIn('PIL', roots)

    def test_public_names_do_not_shadow_tkinter_methods(self):
        taken = set(dir(tkinter.Misc)) | set(dir(tkinter.Wm))
        for name in ('enable_dpi_awareness', 'apply_theme', 'font', 'label', 'button', 'card', 'chip', 'tone_colors',
                     'resolve', 'on_appearance', 'off_appearance', 'icon', 'entry', 'option_menu', 'checkbox', 'switch', 'textbox',
                     'scroll_area', 'tabs', 'table', 'empty_state', 'setting_row', 'fit', 'full_text',
                     'wrap_to_width', 'apply_ttk_styles', 'set_page_cap', 'contrast', 'theme_patch'):
            self.assertTrue(callable(getattr(ui_theme, name)), name)
            self.assertNotIn(name, taken, name)

    def test_class_members_do_not_shadow_their_base_classes(self):
        members = {ui_theme.Tabs: ('add', 'tab', 'set', 'get'), ui_theme.Chip: ('set',),
                   ui_theme.AppTable: ('fill', 'restripe', 'set_empty', 'rescale', 'recolor'),
                   ui_theme.ScrollArea: ('canvas', 'bar_visible'), ui_theme.AppButton: ('kind',)}
        for cls, names in members.items():
            base = cls.__mro__[1]
            for name in names:
                self.assertIn(name, vars(cls), f'{cls.__name__}.{name}')
                self.assertFalse(hasattr(base, name), f'{cls.__name__}.{name} shadows {base.__name__}.{name}')

    def test_break_units(self):
        units = ui_theme._break_units('等待 10–20 秒，遇到 cookies.txt。完')
        self.assertEqual(units, ['等', '待 ', '10–20 ', '秒，', '遇', '到 ', 'cookies.txt。', '完'])
        self.assertEqual(ui_theme._break_units('已登录（建议小号）的'), ['已', '登', '录', '（建', '议', '小', '号）', '的'])
        self.assertEqual(''.join(ui_theme._break_units(LONG)), LONG)

    def test_wrap_text_fills_lines_and_keeps_words_whole(self):
        def width_of(text):             # 10 px for a wide character, 6 for anything else
            return sum(10 if ord(char) >= 0x2E80 else 6 for char in text)
        wrapped = ui_theme._wrap_text(LONG, width_of, 300)
        lines = wrapped.split('\n')
        self.assertEqual(wrapped.replace('\n', '').replace(' ', ''), LONG.replace(' ', ''))
        self.assertGreater(len(lines), 2)
        for line in lines:
            self.assertLessEqual(width_of(line), 300)
            self.assertNotIn(line[0], '，。；、')
        for line in lines[:-1]:          # every line but the last is nearly full
            self.assertGreater(width_of(line), 300 - 40)
        self.assertNotIn('10–\n', wrapped)
        self.assertNotIn('1\n0', wrapped)
        # A word wider than the line is cut by characters; explicit newlines survive.
        self.assertEqual(ui_theme._wrap_text('abcdefghij', width_of, 24), 'abcd\nefgh\nij')
        self.assertEqual(ui_theme._wrap_text('一二\n三', width_of, 300), '一二\n三')
        self.assertEqual(ui_theme._wrap_text('', width_of, 300), '')


class B_ThemeTest(unittest.TestCase):
    def test_apply_theme_twice_is_harmless_and_patches_existing_keys_only(self):
        stock = json.loads((Path(ctk.__file__).parent / 'assets' / 'themes' / 'blue.json').read_text(encoding='utf-8'))
        patch = ui_theme.theme_patch()
        for widget, values in patch.items():
            self.assertIn(widget, stock)
            keys = stock[widget].get('Windows', stock[widget])
            for key in values:
                self.assertIn(key, keys, f'{widget}.{key}')
        ui_theme.apply_theme()
        first = ctk.ThemeManager.theme
        snapshot = json.dumps(first, sort_keys=True)
        ui_theme.apply_theme()
        self.assertIs(ctk.ThemeManager.theme, first)
        self.assertEqual(json.dumps(ctk.ThemeManager.theme, sort_keys=True), snapshot)
        for widget, values in patch.items():
            for key, value in values.items():
                self.assertEqual(ctk.ThemeManager.theme[widget][key], value, f'{widget}.{key}')
        theme = ctk.ThemeManager.theme
        self.assertEqual(theme['CTk']['fg_color'], list(ui_theme.BG))
        self.assertEqual(theme['CTkFont']['family'], ui_theme.FAMILY)
        self.assertEqual(theme['CTkFont']['size'], 13)
        self.assertEqual(theme['CTkSegmentedButton']['selected_hover_color'], list(ui_theme.SEGMENT_ON))     # (WP7)
        self.assertEqual(ui_theme.SEGMENT_ON[0], ui_theme.SURFACE[0])           # (WP7) light mode unchanged
        self.assertEqual(theme['CTkOptionMenu']['text_color_disabled'], list(ui_theme.FAINT))
        self.assertEqual(theme['CTkEntry']['border_width'], 1)

    def test_no_stock_blue_or_grey_is_left_in_the_theme(self):
        text = json.dumps(ctk.ThemeManager.theme)
        for stock in ('#3B8ED0', '#36719F', '#979DA2', '#939BA2', '#1F6AA5'):
            self.assertNotIn(stock, text)

    def test_apply_theme_recovers_after_another_theme_was_loaded(self):
        ctk.set_default_color_theme('blue')
        self.assertNotEqual(ctk.ThemeManager.theme['CTk']['fg_color'], list(ui_theme.BG))
        ui_theme.apply_theme()
        self.assertEqual(ctk.ThemeManager.theme['CTk']['fg_color'], list(ui_theme.BG))

    def test_apply_theme_leaves_the_appearance_mode_alone(self):
        self.assertEqual(ctk.get_appearance_mode(), 'Light')
        ui_theme.apply_theme()
        self.assertEqual(ctk.get_appearance_mode(), 'Light')

    def test_resolve(self):
        self.assertEqual(ui_theme.resolve(ui_theme.DANGER), '#B93026')
        self.assertEqual(ui_theme.resolve(list(ui_theme.DANGER)), '#B93026')
        self.assertEqual(ui_theme.resolve('#123456'), '#123456')
        self.assertEqual(ui_theme.resolve('transparent'), 'transparent')

    def test_enable_dpi_awareness_is_repeatable(self):
        ui_theme.enable_dpi_awareness()
        ui_theme.enable_dpi_awareness()
        if not sys.platform.startswith('win'):
            self.skipTest('Windows only')
        awareness = ctypes.c_int(-1)
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
        self.assertEqual(awareness.value, 2, 'the process is not per-monitor DPI aware')


class C_WidgetTest(unittest.TestCase):
    """Factories in a root that is never shown."""

    @classmethod
    def setUpClass(cls):
        cls.root = hidden_root()
        cls.scale = scaling(cls.root)

    @classmethod
    def tearDownClass(cls):
        ctk.set_appearance_mode('light')
        close(cls.root)

    def test_tk_saw_the_real_dpi(self):
        if not sys.platform.startswith('win'):
            self.skipTest('Windows only')
        # Awareness was set before this root existed, so Tk's own scaling follows the system DPI.
        system = ctypes.windll.user32.GetDpiForSystem() / 72
        self.assertAlmostEqual(float(self.root.tk.call('tk', 'scaling')), system, delta=0.03)

    # ---- fonts and labels
    def test_font_roles(self):
        for role, size in ui_theme.SIZES.items():
            font = ui_theme.font(role)
            self.assertIs(font, ui_theme.font(role))
            self.assertEqual(font.cget('size'), size)
            self.assertEqual(font.cget('family'), ui_theme.FAMILY)
            self.assertEqual(font.cget('weight'), 'bold' if role in ('heading', 'title', 'brand') else 'normal')
        self.assertIs(ui_theme.font(), ui_theme.font('body'))

    def test_label_defaults_and_overrides(self):
        plain = ui_theme.label(self.root, '文字')
        self.assertEqual(plain.cget('text'), '文字')
        self.assertIs(plain.cget('font'), ui_theme.font('body'))
        self.assertEqual(plain.cget('height'), 22)
        self.assertEqual(plain.cget('text_color'), ui_theme.INK)
        caption = ui_theme.label(self.root, 'x', role='caption')
        self.assertEqual((caption.cget('height'), caption.cget('font')), (18, ui_theme.font('caption')))
        title = ui_theme.label(self.root, 'x', role='title')
        self.assertEqual(title.cget('height'), 32)
        legacy = ui_theme.label(self.root, 'x', font=ui_theme.font('caption'), text_color='#123456', height=30, anchor='w')
        self.assertEqual((legacy.cget('text_color'), legacy.cget('height'), legacy.cget('anchor')), ('#123456', 30, 'w'))
        self.assertEqual(ui_theme.label(self.root).cget('text'), '')

    def test_card(self):
        card = ui_theme.card(self.root)
        self.assertEqual((card.cget('fg_color'), card.cget('corner_radius'), card.cget('border_width'), card.cget('border_color')),
                         (ui_theme.SURFACE, 12, 1, ui_theme.LINE))
        self.assertEqual(ui_theme.card(self.root, corner_radius=4, fg_color=ui_theme.ROW).cget('corner_radius'), 4)

    # ---- buttons
    def test_button_kinds_and_sizes(self):
        looks = {'primary': (ui_theme.ACCENT, ui_theme.ACCENT_HOVER, ui_theme.WHITE),
                 'soft': (ui_theme.ACCENT_SOFT, ui_theme.ACCENT_SOFT_HOVER, ui_theme.ACCENT_TEXT),
                 'ghost': ('transparent', ui_theme.ACCENT_SOFT, ui_theme.ACCENT_TEXT),
                 'danger': (ui_theme.DANGER_SOFT, ui_theme.DANGER_SOFT_HOVER, ui_theme.DANGER)}
        for kind, (fill, hover, ink) in looks.items():
            button = ui_theme.button(self.root, '按钮', None, kind=kind)
            self.assertIsInstance(button, ui_theme.AppButton)
            self.assertIsInstance(button, ctk.CTkButton)
            self.assertEqual(button.kind, kind)
            self.assertEqual((button.cget('fg_color'), button.cget('hover_color'), button.cget('text_color')), (fill, hover, ink))
            self.assertEqual((button.cget('height'), button.cget('corner_radius'), button.cget('width')), (36, 8, 72))
            self.assertIs(button.cget('font'), ui_theme.font('body'))
        small = ui_theme.button(self.root, '小', None, size='sm')
        self.assertEqual(small.cget('height'), 28)
        self.assertIs(small.cget('font'), ui_theme.font('caption'))
        self.assertEqual(ui_theme.button(self.root, '默认').kind, 'soft')
        self.assertEqual(ui_theme.button(self.root, '旧式', None, primary=True, width=129).kind, 'primary')
        self.assertEqual(ui_theme.button(self.root, '旧式', None, primary=True, width=129).cget('width'), 129)
        with self.assertRaises(ValueError):
            ui_theme.button(self.root, 'x', kind='loud')

    def test_button_disabled_look(self):
        calls = []
        fills = {'primary': ui_theme.ACCENT_DISABLED, 'soft': ui_theme.DISABLED_SOFT, 'ghost': 'transparent',
                 'danger': ui_theme.DISABLED_SOFT}
        for kind, disabled_fill in fills.items():
            born = ui_theme.button(self.root, '生来禁用', lambda: calls.append('born'), kind=kind, state='disabled')
            later = ui_theme.button(self.root, '后来禁用', lambda: calls.append('later'), kind=kind)
            enabled_fill = later.cget('fg_color')
            later.configure(state='disabled')
            for button in (born, later):
                self.assertEqual(button.cget('state'), 'disabled')
                self.assertEqual(button.cget('fg_color'), disabled_fill, kind)
                # (WP7) the disabled primary text is white in light mode and dimmed in dark mode
                self.assertEqual(button.cget('text_color_disabled'),
                                 ui_theme.ACCENT_DISABLED_TEXT if kind == 'primary' else ui_theme.FAINT)
                button.invoke()
            self.assertEqual(calls, [])
            for button in (born, later):
                button.configure(state='normal')
                self.assertEqual(button.cget('fg_color'), enabled_fill, kind)
                button.invoke()
            self.assertEqual(calls, ['born', 'later'])
            calls.clear()
        self.assertNotEqual(ui_theme.ACCENT_DISABLED, ui_theme.ACCENT)

    def test_button_text_and_state_in_one_call(self):
        button = ui_theme.button(self.root, '开始下载', None, kind='primary')
        button.configure(text='加入队列', state='disabled')
        self.assertEqual((button.cget('text'), button.cget('state'), button.cget('fg_color')),
                         ('加入队列', 'disabled', ui_theme.ACCENT_DISABLED))
        button.configure(state='normal', text='开始下载')
        self.assertEqual((button.cget('text'), button.cget('fg_color')), ('开始下载', ui_theme.ACCENT))
        # Other options pass through untouched, and an explicit fill becomes the enabled look.
        button.configure(width=150, image=None)
        self.assertEqual(button.cget('width'), 150)
        button.configure(fg_color=ui_theme.OK)
        button.configure(state='disabled')
        self.assertEqual(button.cget('fg_color'), ui_theme.ACCENT_DISABLED)
        button.configure(state='normal')
        self.assertEqual(button.cget('fg_color'), ui_theme.OK)

    # ---- chips
    def test_chip(self):
        chip = ui_theme.chip(self.root, '等待中')
        self.assertIsInstance(chip, ui_theme.Chip)
        self.assertEqual((chip.tone, chip.cget('text_color'), chip.cget('fg_color')), ('idle', ui_theme.MUTED, ui_theme.IDLE_SOFT))
        self.assertEqual((chip.cget('height'), chip.cget('width'), chip.cget('corner_radius')), (22, 64, 11))
        recolours = []
        plain = chip.configure

        def counting(require_redraw=False, **kwargs):
            if 'fg_color' in kwargs or 'text_color' in kwargs:
                recolours.append(kwargs)
            return plain(require_redraw=require_redraw, **kwargs)
        chip.configure = counting
        chip.set('12%', 'info')
        self.assertEqual((chip.cget('text'), chip.tone, chip.cget('text_color'), chip.cget('fg_color')),
                         ('12%', 'info', ui_theme.ACCENT_TEXT, ui_theme.ACCENT_SOFT))
        for percent in range(13, 60):
            chip.set(f'{percent}%', 'info')
        chip.set('60%')
        self.assertEqual(chip.cget('text'), '60%')
        self.assertEqual(len(recolours), 1)
        chip.set('已完成', 'ok')
        self.assertEqual((chip.cget('text'), chip.cget('text_color'), chip.cget('fg_color')), ('已完成', ui_theme.OK, ui_theme.OK_SOFT))
        self.assertEqual(len(recolours), 2)
        for tone in ('warn', 'danger', 'idle'):
            chip.set(tone, tone)
            self.assertEqual((chip.cget('text_color'), chip.cget('fg_color')), ui_theme.tone_colors(tone))

    # ---- inputs
    def test_entry_placeholder_follows_a_textvariable(self):
        variable = ctk.StringVar(self.root, '')
        entry = ui_theme.entry(self.root, placeholder='p', textvariable=variable)
        self.assertIsInstance(entry, ctk.CTkEntry)
        self.assertEqual((entry.cget('height'), entry.cget('font')), (36, ui_theme.font('body')))
        hint = entry.placeholder_label
        self.assertEqual(hint.cget('text'), 'p')
        self.assertEqual(hint.cget('text_color'), ui_theme.FAINT)
        self.assertEqual(hint.winfo_manager(), 'place')
        variable.set('x')
        self.assertEqual(hint.winfo_manager(), '')
        self.assertEqual(entry.get(), 'x')
        variable.set('')
        self.assertEqual(hint.winfo_manager(), 'place')
        variable.set('again')
        entry.destroy()
        variable.set('')                    # the trace of the destroyed entry is gone

    def test_entry_placeholder_without_a_textvariable(self):
        entry = ui_theme.entry(self.root, placeholder='筛选')
        hint = entry.placeholder_label
        self.assertEqual(hint.winfo_manager(), 'place')
        entry.insert(0, 'abc')
        self.assertEqual(hint.winfo_manager(), '')
        entry.delete(0, 'end')
        self.assertEqual(hint.winfo_manager(), 'place')
        entry._entry.insert(0, 'k')         # typed text reaches the Tk entry without insert()
        self.assertEqual(hint.winfo_manager(), 'place')
        entry._hint_later()                 # what <KeyRelease> and <FocusOut> are bound to
        self.root.update()
        self.assertEqual(hint.winfo_manager(), '')
        entry.delete(0, 'end')
        self.assertEqual(hint.winfo_manager(), 'place')
        self.assertEqual(entry._hint_clicked(), 'break')
        variable = ctk.StringVar(self.root, 'later')
        entry.configure(textvariable=variable)
        self.assertEqual(hint.winfo_manager(), '')
        variable.set('')
        self.assertEqual(hint.winfo_manager(), 'place')

    def test_entry_rejects_placeholder_text_with_a_textvariable(self):
        variable = ctk.StringVar(self.root, '')
        with self.assertRaises(ValueError):
            ui_theme.entry(self.root, placeholder_text='p', textvariable=variable)
        with self.assertRaises(ValueError):
            ui_theme.entry(self.root, placeholder='a', placeholder_text='b')
        plain = ui_theme.entry(self.root)
        self.assertIsNone(plain.placeholder_label)
        stock = ui_theme.entry(self.root, placeholder_text='stock')
        self.assertIsNone(stock.placeholder_label)
        self.assertEqual(stock.cget('placeholder_text'), 'stock')

    def test_entry_disabled_look(self):
        variable = ctk.StringVar(self.root, 'D:/下载文件')
        entry = ui_theme.entry(self.root, textvariable=variable)
        self.assertEqual(entry.cget('text_color'), ui_theme.INK)
        entry.configure(state='disabled')
        self.assertEqual(entry.cget('text_color'), ui_theme.MUTED)
        entry.configure(state='readonly')
        self.assertEqual(entry.cget('text_color'), ui_theme.INK)
        entry.configure(state='normal')
        self.assertEqual(entry.cget('text_color'), ui_theme.INK)
        born = ui_theme.entry(self.root, state='disabled')
        self.assertEqual((born.cget('state'), born.cget('text_color')), ('disabled', ui_theme.MUTED))

    def test_option_menu_checkbox_textbox(self):
        menu = ui_theme.option_menu(self.root, ('a', 'b'), width=120)
        self.assertIsInstance(menu, ctk.CTkOptionMenu)
        self.assertEqual((menu.cget('height'), menu.cget('width'), menu.cget('dynamic_resizing')), (36, 120, False))
        self.assertIs(menu.cget('font'), ui_theme.font('body'))
        self.assertEqual(menu.cget('fg_color'), list(ui_theme.MENU))
        box = ui_theme.checkbox(self.root, '整个播放列表')
        self.assertIsInstance(box, ctk.CTkCheckBox)
        self.assertEqual((box.cget('checkbox_width'), box.cget('checkbox_height'), box.cget('text')), (18, 18, '整个播放列表'))
        self.assertIs(box.cget('font'), ui_theme.font('body'))
        text = ui_theme.textbox(self.root, height=72, wrap='none')
        self.assertIsInstance(text, ctk.CTkTextbox)
        self.assertIs(text.cget('font'), ui_theme.font('body'))
        text.insert('1.0', '错误行\n')
        text.tag_config('x', foreground=ui_theme.resolve(ui_theme.DANGER))
        text.tag_add('x', '1.0', '1.end')
        self.assertEqual(str(text.tag_cget('x', 'foreground')), '#B93026')
        with self.assertRaises(tkinter.TclError):       # why resolve() exists
            text.tag_config('y', foreground=ui_theme.DANGER)

    def test_checkbox_disabled_look_and_hover(self):
        variable = ctk.BooleanVar(self.root, True)
        box = ui_theme.checkbox(self.root, '内嵌字幕', variable=variable)
        self.assertEqual((box.cget('fg_color'), box.cget('border_color')), (ui_theme.ACCENT, ui_theme.CHECK_BORDER))
        box.configure(state='disabled')
        self.assertEqual((box.cget('fg_color'), box.cget('border_color')), (ui_theme.ACCENT_DISABLED, ui_theme.SCROLL))
        box.configure(state='normal')
        self.assertEqual((box.cget('fg_color'), box.cget('border_color')), (ui_theme.ACCENT, ui_theme.CHECK_BORDER))
        born = ui_theme.checkbox(self.root, 'x', state='disabled')
        self.assertEqual(born.cget('fg_color'), ui_theme.ACCENT_DISABLED)
        box._on_enter()
        self.assertEqual(box.cget('hover_color'), ui_theme.ACCENT_HOVER)
        variable.set(False)
        box._on_enter()
        self.assertEqual(box.cget('hover_color'), ui_theme.ACCENT_SOFT)

    def test_switch_knob_follows_the_state(self):
        variable = ctk.BooleanVar(self.root, False)
        switch = ui_theme.switch(self.root, '稳健模式', variable=variable)
        self.assertIsInstance(switch, ui_theme.AppSwitch)
        self.assertEqual((switch.cget('switch_width'), switch.cget('switch_height')), (38, 20))
        self.assertEqual(switch.cget('button_color'), ui_theme.SWITCH_KNOB_OFF)
        variable.set(True)
        self.assertEqual(switch.cget('button_color'), ui_theme.ACCENT)
        self.assertEqual(switch.get(), 1)
        variable.set(False)
        self.assertEqual(switch.cget('button_color'), ui_theme.SWITCH_KNOB_OFF)
        switch.toggle()
        self.assertEqual((variable.get(), switch.cget('button_color')), (True, ui_theme.ACCENT))
        switch.deselect()
        self.assertEqual((variable.get(), switch.cget('button_color')), (False, ui_theme.SWITCH_KNOB_OFF))
        switch.select()
        self.assertEqual(switch.cget('button_color'), ui_theme.ACCENT)
        self.assertNotEqual(ui_theme.resolve(switch.cget('button_color')), ui_theme.resolve(ui_theme.SURFACE))
        born_on = ui_theme.switch(self.root, variable=ctk.BooleanVar(self.root, True))
        self.assertEqual(born_on.cget('button_color'), ui_theme.ACCENT)
        bare = ui_theme.switch(self.root)
        self.assertEqual((bare.cget('text'), bare.cget('button_color'), bare.cget('width')), ('', ui_theme.SWITCH_KNOB_OFF, 38))
        other = ctk.BooleanVar(self.root, True)
        bare.configure(variable=other)
        self.assertEqual(bare.cget('button_color'), ui_theme.ACCENT)

    def test_switch_disabled_look(self):
        variable = ctk.BooleanVar(self.root, True)
        switch = ui_theme.switch(self.root, '稳健模式', variable=variable)
        switch.configure(state='disabled')
        self.assertEqual(switch.cget('button_color'), ui_theme.SWITCH_KNOB_DISABLED)
        self.assertEqual(ui_theme.SWITCH_KNOB_DISABLED[0], ui_theme.ACCENT_DISABLED[0])  # (WP7) light look as before
        variable.set(False)
        self.assertEqual(switch.cget('button_color'), ui_theme.CHECK_BORDER)
        switch.configure(state='normal')
        self.assertEqual(switch.cget('button_color'), ui_theme.SWITCH_KNOB_OFF)
        self.assertEqual(ui_theme.switch(self.root, state='disabled').cget('button_color'), ui_theme.CHECK_BORDER)

    # ---- icons
    def test_icon_for_every_code(self):
        if ui_theme.icon_font_path() is None:
            self.skipTest('no Segoe icon font installed: icon() returns None and callers show text only')
        self.assertEqual(len(ICON_CODES), 13)
        for name, code in ICON_CODES.items():
            image = ui_theme.icon(code)
            self.assertIsInstance(image, ctk.CTkImage, name)
            self.assertEqual(image.cget('size'), (16, 16))
            light, dark = image.cget('light_image'), image.cget('dark_image')
            self.assertEqual(light.size, (ui_theme.ICON_PX, ui_theme.ICON_PX))
            box = light.getchannel('A').getbbox()
            self.assertIsNotNone(box, name)
            self.assertGreater((box[2] - box[0]) * (box[3] - box[1]), 400, name)
            self.assertNotEqual(light.tobytes(), dark.tobytes(), name)
        self.assertIs(ui_theme.icon(ui_theme.ICON_DONE, 'ok'), ui_theme.icon(ui_theme.ICON_DONE, 'ok'))
        self.assertIsNot(ui_theme.icon(ui_theme.ICON_DONE, 'ok'), ui_theme.icon(ui_theme.ICON_DONE, 'ok', 28))
        self.assertEqual(ui_theme.icon(ui_theme.ICON_DONE, 'ok', 28).cget('size'), (28, 28))
        for tone in ('ink', 'muted', 'faint', 'accent', 'ok', 'warn', 'danger', 'white', 'side', 'idle', 'info'):
            self.assertIsNotNone(ui_theme.icon(ui_theme.ICON_CLOCK, tone), tone)
        # The glyph carries the tone colour, also in its transparent pixels (no dark fringe).
        red = ui_theme.icon(ui_theme.ICON_ERROR, 'danger').cget('light_image')
        self.assertEqual({color for _, color in red.convert('RGB').getcolors()}, {(0xB9, 0x30, 0x26)})

    def test_both_icon_fonts_have_every_glyph(self):
        from PIL import ImageFont
        folder = ui_theme.icon_font_path()
        if folder is None:
            self.skipTest('no Segoe icon font installed')
        for file in ui_theme.ICON_FONT_FILES:
            path = folder.parent / file
            if not path.is_file():
                continue
            glyphs = ImageFont.truetype(str(path), 53)
            missing = bytes(glyphs.getmask('\uffff'))
            for name, code in ICON_CODES.items():
                mask = glyphs.getmask(chr(code))
                self.assertIsNotNone(mask.getbbox(), f'{file} {name}')
                self.assertNotEqual(bytes(mask), missing, f'{file} {name}')

    def test_icon_is_none_without_a_font_or_glyph(self):
        saved = dict(ui_theme._icon_pictures), dict(ui_theme._icon_images)
        try:
            with mock.patch.object(ui_theme, 'ICON_FONT_FILES', ('no-such-icon-font.ttf',)), \
                    mock.patch.object(ui_theme, '_icon_warned', False):
                ui_theme._icon_pictures.clear()
                ui_theme._icon_images.clear()
                self.assertIsNone(ui_theme.icon_font_path())
                with self.assertLogs('YtDlpDesktop.ui', 'WARNING'):
                    self.assertIsNone(ui_theme.icon(ui_theme.ICON_DOWNLOAD))
                self.assertIsNone(ui_theme.icon(ui_theme.ICON_DONE, 'ok', 28))
                empty = ui_theme.empty_state(self.root, '没有图标', ui_theme.ICON_DOWNLOAD)
                self.assertIsNone(empty.icon_label)
                self.assertEqual(empty.hint_label.cget('text'), '没有图标')
            with mock.patch.object(ui_theme, '_icon_warned', True):
                ui_theme._icon_pictures.clear()
                ui_theme._icon_images.clear()
                self.assertIsNone(ui_theme.icon(0xFFFF))           # not a glyph
                self.assertIsNone(ui_theme.icon(ui_theme.ICON_DONE, 'no-such-tone'))
        finally:
            ui_theme._icon_pictures.clear()
            ui_theme._icon_pictures.update(saved[0])
            ui_theme._icon_images.clear()
            ui_theme._icon_images.update(saved[1])

    # ---- appearance
    def test_on_appearance_and_recolouring(self):
        calls = []
        table = ui_theme.table(self.root, [('a', 'A', 100)])
        table.fill([(1, ('x',)), (2, ('y',))])
        style = ttk.Style(self.root)

        def failing():
            raise RuntimeError('boom')

        def recording():
            calls.append(ui_theme.resolve(ui_theme.DANGER))
        box = ui_theme.textbox(self.root)

        def bad_colour():               # a Tk error of a live widget is a bug, not a closed window
            box.tag_config('bad', foreground=ui_theme.DANGER)
        registered = len(ui_theme._appearance_callbacks)
        ui_theme.on_appearance(failing)
        ui_theme.on_appearance(recording)
        ui_theme.on_appearance(bad_colour)
        try:
            with self.assertLogs('YtDlpDesktop.ui', 'ERROR') as logs:
                ctk.set_appearance_mode('dark')
            self.assertEqual(calls, ['#FF9B8F'])
            self.assertIn('boom', '\n'.join(logs.output))
            self.assertIn('bad_colour', '\n'.join(logs.output))
            self.assertEqual(len(logs.records), 2)
            self.assertEqual(ui_theme.resolve(ui_theme.DANGER), '#FF9B8F')
            self.assertEqual(str(table.tag_configure('odd', 'background')).upper(), ui_theme.STRIPE[1])
            self.assertEqual(str(style.lookup('App.Treeview', 'background')).upper(), ui_theme.SURFACE[1])
            self.assertEqual(str(style.lookup('App.Treeview.Heading', 'foreground')).upper(), ui_theme.MUTED[1])
            with self.assertLogs('YtDlpDesktop.ui', 'ERROR'):
                ctk.set_appearance_mode('light')
            self.assertEqual(calls, ['#FF9B8F', '#B93026'])
            self.assertEqual(str(table.tag_configure('odd', 'background')).upper(), ui_theme.STRIPE[0])
            self.assertEqual(str(style.lookup('App.Treeview', 'background')).upper(), ui_theme.SURFACE[0])
            # Callbacks that fail stay registered (logged and skipped); off_appearance removes one.
            self.assertEqual(len(ui_theme._appearance_callbacks), registered + 3)
            ui_theme.off_appearance(recording)
            ui_theme.off_appearance(recording)          # not registered any more: ignored
            with self.assertLogs('YtDlpDesktop.ui', 'ERROR'):
                ctk.set_appearance_mode('dark')
            self.assertEqual(calls, ['#FF9B8F', '#B93026'])
        finally:
            for callback in (failing, recording, bad_colour):
                ui_theme.off_appearance(callback)
            ctk.set_appearance_mode('light')
        self.assertEqual(len(ui_theme._appearance_callbacks), registered)

    def test_appearance_callback_of_a_widget_method(self):
        class Panel(ctk.CTkFrame):
            calls = 0

            def recolour(self):
                self.calls += 1

        panel = Panel(self.root)
        registered = len(ui_theme._appearance_callbacks)
        ui_theme.on_appearance(panel.recolour)
        ui_theme.on_appearance(panel.recolour)
        try:
            ctk.set_appearance_mode('dark')
            self.assertEqual(panel.calls, 2)            # registered twice, called twice
            ui_theme.off_appearance(panel.recolour)     # a method is found although it is held weakly
            self.assertEqual(len(ui_theme._appearance_callbacks), registered)
            ctk.set_appearance_mode('light')
            self.assertEqual(panel.calls, 2)
            # A widget destroyed while its window lives on is not called any more, without a log line.
            ui_theme.on_appearance(panel.recolour)
            panel.destroy()
            with self.assertNoLogs('YtDlpDesktop.ui', 'INFO'):
                ctk.set_appearance_mode('dark')
            self.assertEqual(panel.calls, 2)
            self.assertEqual(len(ui_theme._appearance_callbacks), registered)
        finally:
            ui_theme.off_appearance(panel.recolour)
            ctk.set_appearance_mode('light')

    def test_appearance_method_of_a_live_window_survives_a_transient_tk_error(self):
        # WP4 follow-up of a WP3 minor: one "invalid command name" from a method of a window that
        # still exists (a child being rebuilt) must not unregister the window for good.
        class Panel(ctk.CTkFrame):
            calls = 0

            def recolour(self):
                self.calls += 1
                if self.calls == 1:
                    raise tkinter.TclError('invalid command name ".!panel.!label"')

        panel = Panel(self.root)
        registered = len(ui_theme._appearance_callbacks)
        ui_theme.on_appearance(panel.recolour)
        try:
            with self.assertLogs('YtDlpDesktop.ui', 'ERROR'):
                ctk.set_appearance_mode('dark')
            self.assertEqual(panel.calls, 1)
            self.assertEqual(len(ui_theme._appearance_callbacks), registered + 1)     # still registered
            ctk.set_appearance_mode('light')
            self.assertEqual(panel.calls, 2)
        finally:
            ui_theme.off_appearance(panel.recolour)
            ctk.set_appearance_mode('light')
            panel.destroy()
        self.assertEqual(len(ui_theme._appearance_callbacks), registered)

    # ---- tabs
    def test_tabs_api(self):
        clicked = []
        tabs = ui_theme.tabs(self.root, ['一', '二', '三'], command=clicked.append)
        self.assertIsInstance(tabs, ui_theme.Tabs)
        self.assertIsInstance(tabs, ctk.CTkFrame)
        self.assertEqual((tabs.cget('fg_color'), tabs.cget('corner_radius'), tabs.cget('border_width')), (ui_theme.SURFACE, 12, 1))
        self.assertEqual(tabs.get(), '一')
        first = tabs.tab('一')
        self.assertIs(tabs.add('一'), first)
        self.assertEqual(first.cget('fg_color'), 'transparent')
        info = first.grid_info()
        self.assertGreater(int(info['padx']), 0)
        self.assertEqual((int(info['row']), int(info['column']), info['sticky']), (1, 0, 'nesw'))
        self.assertEqual(int(info['padx']), int(6 * self.scale))
        tabs.set('二')
        self.assertEqual(tabs.get(), '二')
        self.assertEqual(clicked, [])
        tabs._clicked('三')                 # what a click on the bar does
        self.assertEqual((tabs.get(), clicked), ('三', ['三']))
        fourth = tabs.add('四')
        self.assertIs(tabs.tab('四'), fourth)
        self.assertEqual(tabs.get(), '三')
        tabs.set('四')
        self.assertEqual(tabs.get(), '四')
        with self.assertRaises(ValueError):
            tabs.set('无')
        with self.assertRaises(ValueError):
            tabs.tab('无')
        self.assertEqual(ui_theme.tabs(self.root, []).get(), '')

    # ---- tables
    def test_table_style_and_size(self):
        table = ui_theme.table(self.root, [('kind', '类型', 80, False), ('title', '任务', 360), ('time', '时间', 40)], empty='暂无')
        self.assertIsInstance(table, ttk.Treeview)
        self.assertEqual(int(table.cget('height')), 4)
        self.assertEqual(int(ui_theme.table(self.root, [('a', 'A', 100)], height=7).cget('height')), 7)
        self.assertEqual(str(table.cget('selectmode')), 'browse')
        self.assertEqual(str(ui_theme.table(self.root, [('a', 'A', 100)], selectmode='extended').cget('selectmode')), 'extended')
        self.assertEqual(str(table.cget('style')), 'App.Treeview')
        self.assertIsInstance(table.container, ctk.CTkFrame)
        self.assertIs(table.master, table.container)
        style = ttk.Style(self.root)
        self.assertEqual(style.theme_use(), 'clam')
        scale = self.scale
        for name in ('App.Treeview', 'Collect.Treeview'):
            self.assertEqual(int(style.lookup(name, 'rowheight')), round(32 * scale))
            self.assertEqual(tuple(self.root.tk.splitlist(style.lookup(name, 'font'))), (ui_theme.FAMILY, str(-round(12 * scale))))
            tree_line = exact_font(self.root, style.lookup(name, 'font')).metrics('linespace')
            body_line = exact_font(self.root, (ui_theme.FAMILY, -round(13 * scale))).metrics('linespace')
            self.assertGreaterEqual(tree_line, 0.85 * body_line)
            self.assertLessEqual(tree_line, body_line)          # the caption size, not larger than body text
            self.assertEqual(str(style.lookup(name, 'background')).upper(), '#FFFFFF')
            self.assertEqual(str(style.lookup(name, 'background', ['selected'])).upper(), ui_theme.SELECT[0])
            self.assertEqual(str(style.lookup(name + '.Heading', 'background')).upper(), ui_theme.SURFACE_ALT[0])
        self.assertEqual(int(table.column('kind', 'width')), round(80 * scale))
        self.assertEqual(int(table.column('title', 'width')), round(360 * scale))
        self.assertEqual(int(table.column('time', 'minwidth')), round(40 * scale))
        self.assertEqual(int(table.column('title', 'minwidth')), round(56 * scale))
        self.assertFalse(int(table.column('kind', 'stretch')))
        self.assertTrue(int(table.column('title', 'stretch')))
        self.assertEqual(str(table.column('title', 'anchor')), 'w')
        self.assertEqual(str(table.heading('title', 'anchor')), 'w')
        self.assertEqual(table.heading('title', 'text'), '任务')

    def test_table_fill_stripes_hint_and_selection(self):
        table = ui_theme.table(self.root, [('a', 'A', 100), ('b', 'B', 100)], selectmode='extended', empty='暂无内容')
        hint = table.empty_label
        self.assertEqual((hint.cget('text'), hint.cget('fg_color')), ('暂无内容', ui_theme.SURFACE))
        # The hint carries instructions: readable like the caption of empty_state(), not FAINT.
        self.assertEqual(hint.cget('text_color'), ui_theme.MUTED)
        self.assertIn((hint.cget('text_color'), hint.cget('fg_color')), ui_theme.TEXT_PAIRS)
        self.assertGreaterEqual(ui_theme.contrast(hint.cget('text_color'), hint.cget('fg_color')), 4.5)
        self.assertLess(ui_theme.contrast(ui_theme.FAINT, ui_theme.SURFACE), 4.5)
        self.assertEqual(hint.winfo_manager(), 'place')
        table.fill([(10, ('x', 1)), ('b', ('y', 2)), (30, ('z', 3)), (None, ('auto', 4))])
        children = table.get_children()
        self.assertEqual(children[:3], ('10', 'b', '30'))
        self.assertEqual(len(children), 4)
        self.assertEqual(hint.winfo_manager(), '')
        tags = [tuple(table.tk.splitlist(table.item(iid, 'tags'))) for iid in children]
        self.assertEqual(tags, [(), ('odd',), (), ('odd',)])
        self.assertEqual(str(table.tag_configure('odd', 'background')).upper(), ui_theme.STRIPE[0])
        self.assertEqual(table.item('b', 'values')[0], 'y')
        table.selection_set(('b', '30'))
        table.focus('b')
        table.fill([(30, ('z', 3)), ('b', ('y', 2)), (50, ('n', 5))])
        self.assertEqual(table.get_children(), ('30', 'b', '50'))
        self.assertEqual(set(table.selection()), {'b', '30'})
        self.assertEqual(table.focus(), 'b')
        table.fill([(50, ('n', 5))])
        self.assertEqual(table.selection(), ())
        table.fill(iter([]))
        self.assertEqual((table.get_children(), hint.winfo_manager()), ((), 'place'))
        table.set_empty('没有匹配的结果')
        self.assertEqual((hint.cget('text'), hint.winfo_manager()), ('没有匹配的结果', 'place'))
        table.set_empty('')
        self.assertEqual(hint.winfo_manager(), '')

    def test_table_restripe_keeps_iids_and_other_tags(self):
        table = ui_theme.table(self.root, [('a', 'A', 100)], empty='空')
        for iid in ('r1', 'r2', 'r3'):
            table.insert('', 'end', iid=iid, values=(iid,), tags=('mine',))
        table.restripe()
        self.assertEqual(table.get_children(), ('r1', 'r2', 'r3'))
        tags = [tuple(table.tk.splitlist(table.item(iid, 'tags'))) for iid in table.get_children()]
        self.assertEqual(tags, [('mine',), ('mine', 'odd'), ('mine',)])
        self.assertEqual(table.empty_label.winfo_manager(), '')
        table.delete('r1')
        table.restripe()
        tags = [tuple(table.tk.splitlist(table.item(iid, 'tags'))) for iid in table.get_children()]
        self.assertEqual(tags, [('mine',), ('mine', 'odd')])
        table.delete(*table.get_children())
        table.restripe()
        self.assertEqual(table.empty_label.winfo_manager(), 'place')

    def test_table_scrollbar_keeps_the_newest_view_report(self):
        table = ui_theme.table(self.root, [('a', 'A', 100)])
        bar = table._bar
        self.assertEqual(bar.winfo_manager(), '')
        table._scrolled('0.0', '0.25')
        self.assertEqual(bar.winfo_manager(), 'grid')
        table._scrolled('0.0', '1.0')
        self.assertEqual(bar.winfo_manager(), '')
        # CTkScrollbar.set() runs Tk's pending idle tasks, and Tk can report a newer view
        # through the same callback meanwhile. The older report must not undo the newer one.
        plain = bar.set
        nested = []

        def reporting(newer):
            def set_and_report(first, last):
                plain(first, last)
                if not nested:
                    nested.append((first, last))
                    table._scrolled(*newer)
            return set_and_report
        bar.set = reporting(('0.0', '0.25'))
        table._scrolled('0.0', '1.0')                   # hidden by the older report, shown by the newer
        self.assertEqual(nested, [('0.0', '1.0')])
        self.assertEqual(bar.winfo_manager(), 'grid')
        self.assertEqual(bar.get(), (0.0, 0.25))
        nested.clear()
        bar.set = reporting(('0.0', '1.0'))
        table._scrolled('0.0', '0.5')                   # and the other way round
        self.assertEqual(nested, [('0.0', '0.5')])
        self.assertEqual(bar.winfo_manager(), '')
        bar.set = plain
        # The grid is the truth: a bar gridded behind the table's back goes at the next check.
        bar.grid(row=0, column=1, sticky='ns')
        table.rescale()
        self.assertEqual(bar.winfo_manager(), '')
        self.assertIsNone(bar._last_geometry_manager_call)     # nothing customtkinter could repeat

    def test_apply_ttk_styles_is_repeatable(self):
        callbacks = len(ctk.ScalingTracker.window_widgets_dict[self.root])
        trackers = ctk.AppearanceModeTracker.callback_list.count(ui_theme._appearance_changed)
        ui_theme.apply_ttk_styles(self.root)
        ui_theme.apply_ttk_styles(self.root)
        self.assertIn(self.root, ui_theme._styled_roots)
        self.assertEqual(ctk.AppearanceModeTracker.callback_list.count(ui_theme._appearance_changed), 1)
        self.assertLessEqual(len(ctk.ScalingTracker.window_widgets_dict[self.root]) - callbacks, 1)
        self.assertLessEqual(trackers, 1)

    def test_tables_follow_a_scaling_change(self):
        table = ui_theme.table(self.root, [('a', 'A', 100)])
        style = ttk.Style(self.root)
        scale = self.scale
        try:
            ctk.set_widget_scaling(1.5)
            now = scaling(self.root)
            self.assertAlmostEqual(now, scale * 1.5)
            self.assertEqual(int(style.lookup('App.Treeview', 'rowheight')), round(32 * now))
            self.assertEqual(int(table.column('a', 'width')), round(100 * now))
        finally:
            ctk.set_widget_scaling(1.0)
        self.assertEqual(int(style.lookup('App.Treeview', 'rowheight')), round(32 * scale))
        self.assertEqual(int(table.column('a', 'width')), round(100 * scale))

    # ---- composed pieces and text helpers
    def test_empty_state_and_setting_row(self):
        empty = ui_theme.empty_state(self.root, '你的下载任务会显示在这里', ui_theme.ICON_DOWNLOAD)
        self.assertEqual(empty.cget('fg_color'), 'transparent')
        self.assertEqual((empty.hint_label.cget('text'), empty.hint_label.cget('text_color')), ('你的下载任务会显示在这里', ui_theme.MUTED))
        if ui_theme.icon_font_path() is not None:
            self.assertIsNotNone(empty.icon_label)
        self.assertIsNone(ui_theme.empty_state(self.root, '无图标').icon_label)
        made = []

        def control(parent):
            made.append(parent)
            return ui_theme.switch(parent)
        row = ui_theme.setting_row(self.root, '稳健模式（推荐）', LONG, control)
        self.assertEqual(row.cget('fg_color'), 'transparent')
        self.assertEqual(made, [row.control_area])
        self.assertIsInstance(row.control, ui_theme.AppSwitch)
        self.assertEqual(row.control.winfo_manager(), 'pack')
        self.assertEqual((row.title_label.cget('text'), row.title_label.cget('font')), ('稳健模式（推荐）', ui_theme.font('body')))
        description = row.description_label
        self.assertEqual((description.cget('text'), description.cget('text_color'), description.cget('font')),
                         (LONG, ui_theme.MUTED, ui_theme.font('caption')))
        self.assertEqual((description.cget('anchor'), str(description.cget('justify'))), ('w', 'left'))
        self.assertEqual(description.grid_info()['sticky'], 'ew')
        self.assertEqual(int(row.grid_columnconfigure(0)['weight']), 1)
        self.assertEqual(int(row.control_area.grid_info()['column']), 1)
        bare = ui_theme.setting_row(self.root, '只有标题')
        self.assertIsNone(bare.control)
        self.assertEqual(bare.description_label.winfo_manager(), '')

    def test_fit_keeps_the_full_text_without_a_width(self):
        label = ui_theme.label(self.root, anchor='w')
        long = '徐秉龍、沈以誠 - 白羊『青春一記荒唐 亦然學著瘋狂。』【動態歌詞Lyrics】' * 3
        self.assertLessEqual(label.winfo_width(), 1)
        ui_theme.fit(label, long)
        self.assertEqual(label.cget('text'), long)
        self.assertEqual(ui_theme.full_text(label), long)
        ui_theme.fit(label, '短')
        self.assertEqual((label.cget('text'), ui_theme.full_text(label)), ('短', '短'))
        ui_theme.fit(label, None)
        self.assertEqual(ui_theme.full_text(label), '')
        # A fitted label follows a direct configure(text=...) as well.
        label.configure(text='下载完成')
        self.assertEqual((label.cget('text'), ui_theme.full_text(label)), ('下载完成', '下载完成'))
        label.configure(text=long, text_color=ui_theme.MUTED)
        self.assertEqual((label.cget('text'), ui_theme.full_text(label), label.cget('text_color')),
                         (long, long, ui_theme.MUTED))
        label.configure(text='两行\n文字')
        self.assertEqual((label.cget('text'), ui_theme.full_text(label)), ('两行 文字', '两行\n文字'))
        label.configure(text_color=ui_theme.INK, anchor='e')        # other options pass through
        self.assertEqual((label.cget('text'), label.cget('anchor')), ('两行 文字', 'e'))
        with self.assertRaises(ValueError):
            label.configure(no_such_option=1)
        plain = ui_theme.label(self.root, '未管理')
        self.assertEqual(ui_theme.full_text(plain), '未管理')
        plain.configure(text='仍未管理')
        self.assertEqual(ui_theme.full_text(plain), '仍未管理')

    def test_measure_font_matches_what_tk_draws(self):
        text = '正在下载 2 / 3 · 徐秉龍 - 白羊 Lyrics MV 【動態歌詞】'
        for role in ui_theme.SIZES:
            sample = ui_theme.label(self.root, text, role=role)
            drawn = sample._label.winfo_reqwidth()          # the Tk label has no padding or border
            measured = ui_theme._measure_font(sample, sample.cget('font')).measure(text)
            self.assertEqual(measured, drawn, role)
        plain = ui_theme.label(self.root, text, font=(ui_theme.FAMILY, 14, 'bold'))
        self.assertEqual(ui_theme._measure_font(plain, plain.cget('font')).measure(text), plain._label.winfo_reqwidth())
        self.assertIs(ui_theme._measure_font(plain, plain.cget('font')), ui_theme._measure_font(plain, (ui_theme.FAMILY, 14, 'bold')))

    def test_ellipsize(self):
        def measure(text):
            return 10 * len(text)
        self.assertEqual(ui_theme._ellipsize('abcdef', measure, 60), 'abcdef')
        self.assertEqual(ui_theme._ellipsize('abcdefgh', measure, 60), 'abcde…')
        self.assertEqual(ui_theme._ellipsize('ab cdefgh', measure, 40), 'ab…')
        self.assertEqual(ui_theme._ellipsize('abcdef', measure, 5), '…')

    def test_cut_cell(self):
        # 1.6.0 WP5 fix round 1: table cells cut at the end or in the middle (links).
        def width_of(text):
            return 10 * len(text)
        self.assertEqual(ui_theme._cut_cell('abcdef', width_of, 60), 'abcdef')
        self.assertEqual(ui_theme._cut_cell('abcdefgh', width_of, 60), 'abcde…')
        self.assertEqual(ui_theme._cut_cell('ab cdefgh', width_of, 40), 'ab…')
        self.assertEqual(ui_theme._cut_cell('abcdef', width_of, 5), '…')
        url = 'https://media.example.org/season-2026/episode-0017.mp4'
        cut = ui_theme._cut_cell(url, width_of, 400, middle=True)
        self.assertLessEqual(width_of(cut), 400)
        self.assertEqual(cut.count('…'), 1)
        head, tail = cut.split('…')
        self.assertTrue(url.startswith(head) and url.endswith(tail))
        self.assertTrue(tail.endswith('episode-0017.mp4'))      # the distinguishing end stays
        self.assertTrue(head.startswith('https://media'))
        self.assertEqual(ui_theme._cut_cell(url, width_of, 2000, middle=True), url)
        # Rows that share a long end: the segment where they differ stays visible.
        rows = [f'https://media.example.org/podcast/season-2026/episode-{n:03d}-the-very-long-slug.m4a'
                for n in range(1, 41)]
        common = (len(os.path.commonprefix(rows)), len(os.path.commonprefix([row[::-1] for row in rows])))
        cuts = [ui_theme._cut_cell(row, width_of, 400, middle=True, common=common) for row in rows]
        self.assertEqual(len(set(cuts)), 40)
        self.assertTrue(all(width_of(cut) <= 400 for cut in cuts))
        self.assertEqual(cuts[11], 'https://media.e…/episode-012-the-very-l…')


class D_LayoutTest(unittest.TestCase):
    """Geometry needs a mapped window; this one is invisible and takes no focus."""

    @classmethod
    def setUpClass(cls):
        cls.root = laid_out_root('1400x520')
        cls.root.grid_columnconfigure(0, weight=1)
        cls.root.grid_rowconfigure(0, weight=1)
        cls.scale = scaling(cls.root)
        settle(cls.root, 2)

    @classmethod
    def tearDownClass(cls):
        close(cls.root)

    def setUp(self):
        self.stage = ctk.CTkFrame(self.root, fg_color='transparent', corner_radius=0)
        self.stage.grid(row=0, column=0, sticky='nsew')

    def tearDown(self):
        self.stage.destroy()

    def box(self, width, height=200):
        """A frame of fixed logical size inside the stage."""
        frame = ctk.CTkFrame(self.stage, width=width, height=height, fg_color=ui_theme.SURFACE, corner_radius=0)
        frame.grid(row=0, column=0, sticky='nw')
        frame.grid_propagate(False)
        return frame

    def test_factory_heights(self):
        holder = ctk.CTkFrame(self.stage, fg_color='transparent')
        holder.grid(row=0, column=0, sticky='nw')
        variable = ctk.StringVar(self.root, '')
        widgets = {
            'button_md': (ui_theme.button(holder, '开始下载', None, kind='primary'), 36),
            'button_sm': (ui_theme.button(holder, '粘贴', None, kind='ghost', size='sm'), 28),
            'entry': (ui_theme.entry(holder, placeholder='p', textvariable=variable), 36),
            'option_menu': (ui_theme.option_menu(holder, ['a']), 36),
            'chip': (ui_theme.chip(holder, '已完成', 'ok'), 22),
            'checkbox': (ui_theme.checkbox(holder, '整个播放列表'), 24),
            'switch': (ui_theme.switch(holder, '稳健模式'), 24),
            'label_caption': (ui_theme.label(holder, '说明 Ag', role='caption'), 18),
            'label_body': (ui_theme.label(holder, '正文 Ag'), 22),
            'label_heading': (ui_theme.label(holder, '标题 Ag', role='heading'), 22),
            'label_title': (ui_theme.label(holder, '大标题 Ag', role='title'), 32),
            'label_brand': (ui_theme.label(holder, 'YT-DLP', role='brand'), 24),
        }
        for row, (widget, _) in enumerate(widgets.values()):
            widget.grid(row=row, column=0, sticky='w')
        settle(self.root)
        for name, (widget, logical) in widgets.items():
            self.assertAlmostEqual(widget.winfo_height() / self.scale, logical, delta=1, msg=name)
        self.assertTrue(widgets['entry'][0].placeholder_label.winfo_ismapped())
        variable.set('x')
        settle(self.root, 2)
        self.assertFalse(widgets['entry'][0].placeholder_label.winfo_ismapped())
        variable.set('')
        settle(self.root, 2)
        self.assertTrue(widgets['entry'][0].placeholder_label.winfo_ismapped())
        # A checkbox and a switch are as wide as their content, a chip at least 64 px.
        self.assertLess(widgets['checkbox'][0].winfo_width() / self.scale, 130)
        self.assertAlmostEqual(widgets['chip'][0].winfo_width() / self.scale, 64, delta=1)
        # A button grows with its text and keeps 8 px on each side.
        wide = ui_theme.button(holder, '重试未完成 (12) 并继续下载', None)
        wide.grid(row=20, column=0, sticky='w')
        settle(self.root, 2)
        self.assertGreater(wide.winfo_width() / self.scale, 150)

    def test_placeholder_is_cut_to_a_narrow_entry(self):
        frame = self.box(400)
        entry = ui_theme.entry(frame, placeholder='订阅名称（使用网页采集页的当前设置）', width=120)
        entry.grid(row=0, column=0)
        settle(self.root)
        hint = entry.placeholder_label
        self.assertTrue(hint.cget('text').endswith('…'))
        self.assertLessEqual(hint.winfo_x() + hint.winfo_width(), entry.winfo_width())
        entry.configure(width=380)
        settle(self.root)
        self.assertEqual(hint.cget('text'), '订阅名称（使用网页采集页的当前设置）')

    def test_wrap_to_width_settles(self):
        frame = self.box(390)
        frame.grid_columnconfigure(0, weight=1)
        label = ui_theme.label(frame, LONG, role='caption', text_color=ui_theme.MUTED, anchor='w', justify='left')
        label.grid(row=0, column=0, sticky='ew')
        ui_theme.button(frame, '检查更新', None, width=90).grid(row=0, column=1)
        changes = []
        plain = label.configure

        def counting(require_redraw=False, **kwargs):
            if 'wraplength' in kwargs:
                changes.append(kwargs['wraplength'])
            return plain(require_redraw=require_redraw, **kwargs)
        label.configure = counting
        ui_theme.wrap_to_width(label)
        ui_theme.wrap_to_width(label)       # a second call binds nothing new
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        logical = label.winfo_width() / self.scale
        self.assertAlmostEqual(logical, 300, delta=4)
        self.assertGreaterEqual(len(changes), 1)
        self.assertLessEqual(len(changes), 10)
        self.assertEqual(label.cget('wraplength'), changes[-1])
        self.assertLessEqual(abs(label.cget('wraplength') - logical), 4)
        self.assertEqual(label._wrap_px, label.cget('wraplength'))
        # The text is unchanged for readers; only the Tk label inside got line breaks.
        self.assertEqual(label.cget('text'), LONG)
        shown = label._label.cget('text')
        self.assertEqual(shown.replace('\n', '').replace(' ', ''), LONG.replace(' ', ''))
        lines = shown.split('\n')
        self.assertGreater(len(lines), 2)
        caption = exact_font(self.root, (ui_theme.FAMILY, -round(12 * self.scale)))
        limit = label.cget('wraplength') * self.scale
        for line in lines:
            self.assertLessEqual(caption.measure(line), limit)
        for line in lines[:-1]:
            self.assertGreater(caption.measure(line), limit - 3 * 12 * self.scale)
        # Tk shows exactly these lines: it had no reason to wrap any of them again.
        self.assertEqual(label._label.winfo_reqheight(), len(lines) * caption.metrics('linespace'))
        self.assertEqual(label._label.winfo_reqwidth(), max(caption.measure(line) for line in lines))
        self.assertLessEqual(label._label.winfo_reqwidth(), label.winfo_width())
        self.assertEqual(label.winfo_height(), label._label.winfo_reqheight())
        # New text is broken again; a wider column re-wraps.
        label.configure(text='短句。')
        self.assertEqual((label.cget('text'), label._label.cget('text')), ('短句。', '短句。'))
        label.configure(text=LONG)
        self.assertEqual(label._label.cget('text'), shown)
        before = len(changes)
        frame.configure(width=700)
        settle(self.root)
        self.assertTrue(before + 1 <= len(changes) <= before + 3, changes)
        self.assertLessEqual(abs(label.cget('wraplength') - label.winfo_width() / self.scale), 4)
        self.assertLess(len(label._label.cget('text').split('\n')), len(lines))

    def test_setting_row_description_wraps_inside_its_column(self):
        frame = self.box(520, 300)
        frame.grid_columnconfigure(0, weight=1)
        row = ui_theme.setting_row(frame, '稳健模式（推荐）', LONG, lambda parent: ui_theme.switch(parent))
        row.grid(row=0, column=0, sticky='ew')
        settle(self.root, 10)
        description = row.description_label
        self.assertGreater(description.winfo_height() / self.scale, 2 * 15)
        right = description.winfo_rootx() + description.winfo_width()
        self.assertLessEqual(right, row.control_area.winfo_rootx())
        self.assertLessEqual(row.winfo_width(), frame.winfo_width())
        self.assertLessEqual(description._label.winfo_reqwidth(), description.winfo_width())
        # The control sits at the right edge of the row.
        self.assertAlmostEqual(row.control.winfo_rootx() + row.control.winfo_width(),
                               row.winfo_rootx() + row.winfo_width(), delta=2)

    def test_fit_cuts_to_the_label_width_and_refits(self):
        frame = self.box(260)
        frame.grid_columnconfigure(0, weight=1)
        label = ui_theme.label(frame, anchor='w')
        label.grid(row=0, column=0, sticky='ew')
        ui_theme.chip(frame, '已完成', 'ok').grid(row=0, column=1, padx=8)
        settle(self.root)
        long = '徐秉龍、沈以誠 - 白羊『青春一記荒唐 亦然學著瘋狂。』【動態歌詞Lyrics】 [xrooMGuRhgY]'
        ui_theme.fit(label, long)
        settle(self.root)
        shown = label.cget('text')
        self.assertTrue(shown.endswith('…'))
        self.assertTrue(long.startswith(shown[:-1]))
        self.assertEqual(ui_theme.full_text(label), long)
        measure = exact_font(self.root, (ui_theme.FAMILY, -round(13 * self.scale))).measure
        self.assertLessEqual(measure(shown), label.winfo_width())
        self.assertGreater(measure(shown), label.winfo_width() - 3 * 13 * self.scale)
        self.assertEqual(label._label.winfo_reqwidth(), measure(shown))        # nothing is clipped
        self.assertLessEqual(label._label.winfo_reqwidth(), label.winfo_width())
        ui_theme.fit(label, '短标题')
        self.assertEqual(label.cget('text'), '短标题')
        ui_theme.fit(label, long)
        frame.configure(width=900)
        settle(self.root)
        self.assertEqual(label.cget('text'), long)
        frame.configure(width=200)
        settle(self.root)
        self.assertTrue(label.cget('text').endswith('…'))
        self.assertLess(len(label.cget('text')), len(shown))

    def test_fit_follows_a_direct_configure(self):
        frame = self.box(260)
        frame.grid_columnconfigure(0, weight=1)
        label = ui_theme.label(frame, anchor='w')
        label.grid(row=0, column=0, sticky='ew')
        settle(self.root)
        long = '正在下载 1 / 3 · 一个很长很长很长很长很长很长很长很长的标题'
        ui_theme.fit(label, long)
        settle(self.root)
        self.assertTrue(label.cget('text').endswith('…'))
        self.assertEqual(ui_theme.full_text(label), long)
        # A handler writes the label directly, then the window is resized: the text that
        # fit() was given must not come back.
        done = '下载完成 · 3 条成功'
        label.configure(text=done)
        self.assertEqual((label.cget('text'), ui_theme.full_text(label)), (done, done))
        frame.configure(width=420)
        settle(self.root)
        self.assertEqual((label.cget('text'), ui_theme.full_text(label)), (done, done))
        # A long text written directly is cut like one given to fit(), in the same call.
        other = ' · '.join(['任务结束：2 条成功，1 条未完成'] + ['失败的链接已保存，可以稍后重试'] * 4)
        writes = []
        tk_configure = label._label.configure

        def recording(*args, **kwargs):
            if 'text' in kwargs:
                writes.append(kwargs['text'])
            return tk_configure(*args, **kwargs)
        label._label.configure = recording
        label.configure(text=other, text_color=ui_theme.WARN)
        label._label.configure = tk_configure
        shown = label.cget('text')
        self.assertEqual(writes, [shown])                    # the uncut text never reached the screen
        self.assertTrue(shown.endswith('…'))
        self.assertTrue(other.startswith(shown[:-1]))
        self.assertEqual((ui_theme.full_text(label), label.cget('text_color')), (other, ui_theme.WARN))
        measure = exact_font(self.root, (ui_theme.FAMILY, -round(13 * self.scale))).measure
        self.assertLessEqual(measure(shown), label.winfo_width())
        self.assertGreater(measure(shown), label.winfo_width() - 3 * 13 * self.scale)
        frame.configure(width=200)
        settle(self.root)
        self.assertLess(len(label.cget('text')), len(shown))
        self.assertTrue(other.startswith(label.cget('text')[:-1]))
        frame.configure(width=1390)
        settle(self.root)
        self.assertEqual(label.cget('text'), other)
        # A new font is measured at once, without waiting for a resize.
        label.configure(font=ui_theme.font('title'))
        self.assertTrue(label.cget('text').endswith('…'))
        self.assertEqual(ui_theme.full_text(label), other)
        label.configure(font=ui_theme.font('body'))
        self.assertEqual(label.cget('text'), other)

    def test_scrollbars_follow_a_scaling_change(self):
        # customtkinter repeats the last grid() call of each of its widgets after a scaling
        # change (the window moves to a monitor with another DPI). A bar that was taken out
        # must stay out, and one that is shown must stay.
        frame = self.box(900, 480)
        frame.grid_columnconfigure((0, 1), weight=1, uniform='half')
        frame.grid_rowconfigure(0, weight=1)
        table = ui_theme.table(frame, [('a', 'A', 120, False), ('b', 'B', 200)], empty='空')
        table.container.grid(row=0, column=0, sticky='nsew')
        area = ui_theme.scroll_area(frame)
        area.grid(row=0, column=1, sticky='nsew')
        ui_theme.label(area, '一行').grid(row=0, column=0, sticky='w')
        table.fill((number, ('x', 'y')) for number in range(60))
        settle(self.root)
        self.assertTrue(table._bar.winfo_ismapped())
        table.fill((number, ('x', 'y')) for number in range(2))     # shown once, then taken out
        settle(self.root)
        self.assertFalse(table._bar.winfo_ismapped())
        self.assertFalse(area._scrollbar.winfo_ismapped())

        def check_hidden(message):
            inset = round(4 * scaling(self.root))
            self.assertFalse(table._bar.winfo_ismapped(), message)
            self.assertEqual(table._bar.winfo_manager(), '', message)
            self.assertEqual(table.container.winfo_width() - table.winfo_x() - table.winfo_width(), inset, message)
            self.assertEqual(table.yview(), (0.0, 1.0), message)
            self.assertFalse(area._scrollbar.winfo_ismapped(), message)
            self.assertEqual(area._scrollbar.winfo_manager(), '', message)
            self.assertFalse(area.bar_visible, message)
            self.assertEqual(area.canvas.yview(), (0.0, 1.0), message)
            self.assertEqual(area.canvas.winfo_width(), area._parent_frame.winfo_width(), message)   # no gutter
            # The bars were not merely removed again: customtkinter holds no grid call of
            # theirs that it could repeat at the next change.
            self.assertIsNone(table._bar._last_geometry_manager_call, message)
            self.assertIsNone(area._scrollbar._last_geometry_manager_call, message)

        def check_shown(message):
            self.assertTrue(table._bar.winfo_ismapped(), message)
            self.assertTrue(area._scrollbar.winfo_ismapped(), message)
            self.assertTrue(area.bar_visible, message)
            self.assertLess(area.canvas.winfo_width(), area._parent_frame.winfo_width(), message)
            self.assertEqual(table.container.winfo_width() - table._bar.winfo_x() - table._bar.winfo_width(),
                             int(2 * scaling(self.root)), message)

        check_hidden('before')
        try:
            ctk.set_widget_scaling(1.5)
            self.assertEqual((table._bar.winfo_manager(), area._scrollbar.winfo_manager()), ('', ''))
            settle(self.root, 12)
            self.assertAlmostEqual(scaling(self.root), self.scale * 1.5)
            check_hidden('scaled up')
            ctk.set_widget_scaling(1.0)
            settle(self.root, 12)
            check_hidden('scaled back')
            # The other direction: bars that are needed survive the change, at the new scaling.
            rows = [ui_theme.label(area, f'第 {number} 行') for number in range(60)]
            for number, row in enumerate(rows, 1):
                row.grid(row=number, column=0, sticky='w')
            table.fill((number, ('x', 'y')) for number in range(60))
            settle(self.root)
            check_shown('before')
            ctk.set_widget_scaling(1.5)
            settle(self.root, 12)
            check_shown('scaled up')
            ctk.set_widget_scaling(1.0)
            settle(self.root, 12)
            check_shown('scaled back')
            # Content that fits again takes the bars out again, and they stay out.
            for row in rows:
                row.destroy()
            table.fill((number, ('x', 'y')) for number in range(2))
            settle(self.root)
            check_hidden('emptied')
            ctk.set_widget_scaling(1.5)
            settle(self.root, 12)
            check_hidden('emptied, scaled up')
        finally:
            ctk.set_widget_scaling(1.0)
            settle(self.root, 12)
            release_size(self.root)
        self.assertEqual(scaling(self.root), self.scale)
        check_hidden('at the end')
        # bar_visible reads the grid: a bar gridded behind the area's back is reported, and
        # the next check takes it out.
        area._scrollbar.grid(row=1, column=1, sticky='nsew')
        self.assertTrue(area.bar_visible)
        settle(self.root)
        check_hidden('after a stray grid call')
        # A scaling change asks for a check by itself, also when no widget changes its size.
        checks = []
        plain = area._bar_check

        def counting():
            checks.append(area.canvas.yview())
            plain()
        area._bar_check = counting
        area._set_scaling(self.scale, self.scale)
        settle(self.root)
        self.assertEqual(checks, [(0.0, 1.0)])

    def test_set_page_cap(self):
        content = ctk.CTkFrame(self.stage, fg_color='transparent', corner_radius=0)
        content.grid(row=0, column=0, sticky='nsew')
        self.stage.grid_columnconfigure(0, weight=1)
        self.stage.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        pages, cards = {}, {}
        for name in ('capped', 'free'):             # stacked in one cell, like the app's pages
            page = ctk.CTkFrame(content, fg_color='transparent', corner_radius=0)
            page.grid(row=0, column=0, sticky='nsew')
            page.grid_columnconfigure(0, weight=1)
            card = ui_theme.card(page, height=100)
            card.grid(row=0, column=0, sticky='ew')
            pages[name], cards[name] = page, card
        ui_theme.set_page_cap(self.root, content, {pages['capped']: ui_theme.PAGE_MAX, pages['free']: None})
        settle(self.root)
        scale = self.scale
        self.assertAlmostEqual(content.winfo_width() / scale, 1400, delta=2)
        self.assertAlmostEqual(cards['capped'].winfo_width() / scale, 1080, delta=2)
        self.assertEqual(cards['capped'].winfo_x(), 0)
        self.assertAlmostEqual(cards['free'].winfo_width() / scale, 1400, delta=2)
        # Both page frames still fill the cell: a raised capped page hides the page below it
        # completely, also beside its content.
        for page in pages.values():
            self.assertEqual((page.winfo_x(), page.winfo_width()), (0, content.winfo_width()))
            info = page.grid_info()
            self.assertEqual((info['sticky'], int(info['row']), int(info['column']), int(info['padx'])), ('nesw', 0, 0, 0))
        self.assertNotIn('_cap_spare', vars(pages['free']))
        # A narrower window gives the width back; widening caps again.
        self.root.geometry('1000x520+0+0')
        settle(self.root)
        self.assertAlmostEqual(cards['capped'].winfo_width() / scale, 1000, delta=2)
        self.root.geometry('1400x520+0+0')
        settle(self.root)
        self.assertAlmostEqual(cards['capped'].winfo_width() / scale, 1080, delta=2)
        # The cap is a number of physical pixels: it follows a change of scaling although the
        # window, like a maximised one, keeps its size and no <Configure> arrives.
        pixels = content.winfo_width()
        try:
            ctk.set_widget_scaling(1.25)
            settle(self.root, 12)
            now = scaling(self.root)
            self.assertAlmostEqual(now, scale * 1.25)
            self.assertEqual(content.winfo_width(), pixels)
            self.assertAlmostEqual(cards['capped'].winfo_width() / now, 1080, delta=2)
            self.assertAlmostEqual(cards['free'].winfo_width() / now, 1120, delta=2)
        finally:
            ctk.set_widget_scaling(1.0)
            settle(self.root, 12)
            release_size(self.root)
        self.assertAlmostEqual(cards['capped'].winfo_width() / scale, 1080, delta=2)
        self.assertAlmostEqual(cards['free'].winfo_width() / scale, 1400, delta=2)

    def test_scroll_area_shows_no_bar_for_a_rounding_overhang(self):
        # WP4 follow-up of a WP3 minor: a content one logical pixel taller than the viewport has
        # nothing to scroll; a bar (and its gutter) appears only for a real overhang.
        frame = self.box(400, 300)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        area = ui_theme.scroll_area(frame)
        area.grid(row=0, column=0, sticky='nsew')
        area.grid_columnconfigure(0, weight=1)
        settle(self.root)
        view = area.canvas.winfo_height() / self.scale
        filler = ctk.CTkFrame(area, height=round(view) + 1, fg_color='transparent', corner_radius=0)
        filler.grid(row=0, column=0, sticky='ew')
        settle(self.root, 3)
        self.assertFalse(area.bar_visible)
        self.assertEqual(area.canvas.canvasy(0), 0)
        filler.configure(height=round(view) + 40)
        settle(self.root, 3)
        self.assertTrue(area.bar_visible)
        filler.configure(height=round(view) - 20)
        settle(self.root, 3)
        self.assertFalse(area.bar_visible)

    def test_scroll_area_hides_its_bar_and_scrolls_by_wheel(self):
        frame = self.box(400, 300)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        area = ui_theme.scroll_area(frame)
        self.assertIsInstance(area, ctk.CTkScrollableFrame)
        self.assertEqual(area.cget('fg_color'), 'transparent')
        area.grid(row=0, column=0, sticky='nsew')
        area.grid_columnconfigure(0, weight=1)
        first = ui_theme.label(area, '第一行')
        first.grid(row=0, column=0, sticky='w')
        settle(self.root)
        self.assertFalse(area.bar_visible)
        self.assertFalse(area._scrollbar.winfo_ismapped())
        full_width = area.canvas.winfo_width()
        self.assertAlmostEqual(full_width / self.scale, 400, delta=2)       # no gutter without a bar
        fake = types.SimpleNamespace(widget=first, delta=-120)
        area._mouse_wheel_all(fake)
        self.assertEqual(area.canvas.canvasy(0), 0)
        rows = [ui_theme.label(area, f'第 {number} 行') for number in range(2, 40)]
        for number, row in enumerate(rows, 1):
            row.grid(row=number, column=0, sticky='w')
        settle(self.root)
        self.assertTrue(area.bar_visible)
        self.assertTrue(area._scrollbar.winfo_ismapped())
        self.assertLess(area.canvas.winfo_width(), full_width)
        area._mouse_wheel_all(fake)
        settle(self.root, 2)
        step = area.canvas.canvasy(0)
        self.assertEqual(step, round(ui_theme.WHEEL_STEP * self.scale))
        self.assertTrue(48 <= step / self.scale <= 64)
        area._mouse_wheel_all(types.SimpleNamespace(widget=rows[5], delta=120))
        self.assertEqual(area.canvas.canvasy(0), 0)
        # Widgets that scroll themselves keep the wheel; unknown widgets (menus) are ignored.
        table = ui_theme.table(area, [('a', 'A', 100)])
        table.container.grid(row=50, column=0, sticky='ew')
        area._mouse_wheel_all(types.SimpleNamespace(widget=table, delta=-120))
        area._mouse_wheel_all(types.SimpleNamespace(widget='.menu', delta=-120))
        self.assertEqual(area.canvas.canvasy(0), 0)
        for row in rows:
            row.destroy()
        table.container.destroy()
        settle(self.root)
        self.assertFalse(area.bar_visible)
        self.assertFalse(area._scrollbar.winfo_ismapped())
        # An area that loses every child must not keep the bar of the content it had.
        rows = [ui_theme.label(area, f'第 {number} 行') for number in range(40)]
        for number, row in enumerate(rows):
            row.grid(row=number, column=0, sticky='w')
        first.destroy()
        settle(self.root)
        self.assertTrue(area.bar_visible)
        area._mouse_wheel_all(types.SimpleNamespace(widget=rows[0], delta=-360))
        self.assertGreater(area.canvas.canvasy(0), 0)
        for row in rows:
            row.destroy()
        settle(self.root)
        self.assertEqual(area.winfo_children(), [])
        self.assertFalse(area.bar_visible)
        self.assertEqual(area.canvas.yview(), (0.0, 1.0))
        again = [ui_theme.label(area, f'第 {number} 行') for number in range(40)]
        for number, row in enumerate(again):
            row.grid(row=number, column=0, sticky='w')
        settle(self.root)
        self.assertTrue(area.bar_visible)
        self.assertEqual(area.canvas.canvasy(0), 0)
        # The same when one tall child is the only one and goes alone: nothing but its own
        # <Destroy> event tells the area.
        for row in again:
            row.destroy()
        settle(self.root)
        tall = ctk.CTkFrame(area, height=900, fg_color=ui_theme.ROW)
        tall.grid(row=0, column=0, sticky='ew')
        settle(self.root)
        self.assertTrue(area.bar_visible)
        tall.destroy()
        settle(self.root)
        self.assertFalse(area.bar_visible)
        self.assertEqual(area.canvas.yview(), (0.0, 1.0))

    def test_table_layout_and_scrollbar(self):
        frame = self.box(500, 260)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        table = ui_theme.table(frame, [('kind', '类型', 80, False), ('title', '任务', 200)], empty='暂无')
        table.container.grid(row=0, column=0, sticky='nsew')
        settle(self.root)
        scale = self.scale
        self.assertAlmostEqual(table.container.winfo_width() / scale, 500, delta=2)
        self.assertTrue(table.empty_label.winfo_ismapped())
        self.assertFalse(table._bar.winfo_ismapped())
        inset = round(4 * scale)
        self.assertEqual(table.winfo_x(), inset)
        self.assertEqual(table.winfo_y(), inset)
        self.assertEqual(table.container.winfo_width() - table.winfo_x() - table.winfo_width(), inset)
        table.fill((number, ('下载', f'任务 {number}')) for number in range(40))
        settle(self.root)
        self.assertFalse(table.empty_label.winfo_ismapped())
        self.assertTrue(table._bar.winfo_ismapped())
        self.assertGreater(table.container.winfo_width() - table.winfo_x() - table.winfo_width(), round(14 * scale))
        first = table.get_children()[0]
        cell = table.bbox(first, 'kind')
        self.assertEqual(cell[3], round(32 * scale))
        table.fill([(1, ('下载', '任务'))])
        settle(self.root)
        self.assertFalse(table._bar.winfo_ismapped())
        self.assertEqual(table.container.winfo_width() - table.winfo_x() - table.winfo_width(), inset)

    def test_table_ellipsize_cuts_cells_to_their_columns(self):
        # 1.6.0 WP5 fix round 1: no cell is clipped mid-glyph; links lose their middle.
        frame = self.box(500, 200)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        table = ui_theme.table(frame, [('kind', '类型', 80, False), ('title', '标题', 160, True),
                                       ('url', '链接', 200, True)])
        table.container.grid(row=0, column=0, sticky='nsew')
        table.ellipsize(middle=('url',))
        long_title = '城市夜谈节目第二季全集合辑：和他们没有说完的话以及更多更长的标题文字'
        rows = [(str(n), ('媒体', long_title, f'https://media.example.org/season-2026/episode-{n:04d}.mp4'))
                for n in range(3)]
        table.fill(rows)
        settle(self.root)
        measure = ui_theme._measure_font(table, (ui_theme.FAMILY, ui_theme.TABLE_FONT)).measure
        shown = [table.item(iid, 'values') for iid in table.get_children()]
        for (iid, full), values in zip(rows, shown):
            self.assertEqual(table.full_values(iid), full)
            self.assertEqual(values[0], '媒体')
            self.assertTrue(values[1].endswith('…') and long_title.startswith(values[1][:-1]))
            head, tail = values[2].split('…')
            self.assertTrue(full[2].startswith(head) and full[2].endswith(tail))
            self.assertTrue(tail.endswith(f'-{iid.zfill(4)}.mp4'))
            for key, text in zip(('title', 'url'), values[1:]):
                self.assertLessEqual(measure(text), int(table.column(key, 'width')))
        self.assertEqual(len({values[2] for values in shown}), 3)      # the rows stay distinguishable
        # Wider: cut again, longer text shown; fill keeps working on the full values.
        frame.configure(width=1300)
        settle(self.root)
        wider = table.item('0', 'values')
        self.assertGreater(len(wider[1]), len(shown[0][1]))
        table.fill([('x', ('a', 'b', 'c'))])
        self.assertEqual(table.item('x', 'values'), ('a', 'b', 'c'))
        self.assertEqual(table.full_values('x'), ('a', 'b', 'c'))

    def test_tabs_show_one_frame_inside_the_card(self):
        frame = self.box(500, 300)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        tabs = ui_theme.tabs(frame, ['任务历史与续采', '增量订阅'])
        tabs.grid(row=0, column=0, sticky='nsew')
        marks = {name: ui_theme.label(tabs.tab(name), name) for name in ('任务历史与续采', '增量订阅')}
        for mark in marks.values():
            mark.grid(row=0, column=0, padx=10, pady=10)
        settle(self.root)
        scale = self.scale
        inner = tabs.tab('增量订阅')
        self.assertEqual(inner.winfo_x(), int(6 * scale))
        self.assertEqual(tabs.winfo_width() - inner.winfo_x() - inner.winfo_width(), int(6 * scale))
        self.assertEqual(tabs.winfo_height() - inner.winfo_y() - inner.winfo_height(), int(6 * scale))
        self.assertGreater(inner.winfo_y() / scale, 34 + 14)

        # Both frames fill the same cell; the selected one is the top of the stacking order.
        other = tabs.tab('任务历史与续采')
        self.assertEqual((inner.winfo_x(), inner.winfo_y(), inner.winfo_width(), inner.winfo_height()),
                         (other.winfo_x(), other.winfo_y(), other.winfo_width(), other.winfo_height()))
        self.assertIs(tabs.winfo_children()[-1], other)
        tabs.set('增量订阅')
        settle(self.root, 2)
        self.assertIs(tabs.winfo_children()[-1], inner)
        self.assertTrue(all(mark.winfo_ismapped() for mark in marks.values()))


class E_TwoRootsTest(unittest.TestCase):
    """A second root in the same process, after the first one is gone."""

    def build(self):
        root = hidden_root()
        try:
            holder = ctk.CTkFrame(root)
            holder.pack()
            for code in ICON_CODES.values():
                ctk.CTkButton(holder, text='x', image=ui_theme.icon(code)).pack()
                ctk.CTkLabel(holder, text='', image=ui_theme.icon(code, 'ok', 28)).pack()
            for role in ui_theme.SIZES:
                ui_theme.label(holder, role, role=role).pack()
                self.assertGreater(ui_theme.font(role).metrics('linespace'), 0)
            ui_theme.button(holder, '按钮', None, kind='primary', image=ui_theme.icon(ui_theme.ICON_DOWNLOAD, 'white')).pack()
            table = ui_theme.table(holder, [('a', 'A', 100)], empty='空')
            table.fill([(1, ('x',))])
            ui_theme.entry(holder, placeholder='p', textvariable=ctk.StringVar(root, '')).pack()
            label = ui_theme.label(holder, anchor='w')
            ui_theme.fit(label, '文字')
            ui_theme.empty_state(holder, '空', ui_theme.ICON_DOWNLOAD).pack()
            root.update()
            return ttk.Style(root).lookup('App.Treeview', 'rowheight')
        finally:
            close(root)

    def test_appearance_callbacks_of_a_closed_window_drop_out(self):
        class Panel(ctk.CTkFrame):
            """Stands for a window that registers its own recolouring method."""
            calls = 0

            def recolour(self):
                self.calls += 1
                self.configure(border_color=ui_theme.resolve(ui_theme.LINE))

        registered = list(ui_theme._appearance_callbacks)
        first = hidden_root()
        try:
            kept = Panel(first)                 # stays referenced, like a closed app a test still holds
            dropped = Panel(first)              # referenced by nothing but the window and the callback list
            box = ui_theme.textbox(first)

            def paint():                        # a plain function that paints a widget of this window
                paint.calls += 1
                box.tag_config('error', foreground=ui_theme.resolve(ui_theme.DANGER))
            paint.calls = 0
            ui_theme.on_appearance(kept.recolour)
            ui_theme.on_appearance(dropped.recolour)
            ui_theme.on_appearance(paint)
            with self.assertNoLogs('YtDlpDesktop.ui', 'INFO'):
                ctk.set_appearance_mode('dark')
                ctk.set_appearance_mode('light')
            self.assertEqual((kept.calls, dropped.calls, paint.calls), (2, 2, 2))
            self.assertEqual(str(box.tag_cget('error', 'foreground')).upper(), ui_theme.DANGER[0])
            self.assertEqual(len(ui_theme._appearance_callbacks), len(registered) + 3)
        finally:
            ctk.set_appearance_mode('light')
            close(first)
        # The list does not keep a closed window alive.
        gone = weakref.ref(dropped)
        del dropped
        gc.collect()
        self.assertIsNone(gone())
        second = hidden_root()
        try:
            with self.assertLogs('YtDlpDesktop.ui', 'INFO') as logs:
                ctk.set_appearance_mode('dark')
            # The two methods of destroyed widgets were not called and left no trace; the plain
            # function failed on its destroyed textbox once and was dropped with one line.
            self.assertEqual((kept.calls, paint.calls), (2, 3))
            self.assertEqual([record.levelname for record in logs.records], ['WARNING'])
            self.assertIsNone(logs.records[0].exc_info)
            self.assertIn('paint', logs.output[0])
            self.assertEqual(ui_theme._appearance_callbacks, registered)
            with self.assertNoLogs('YtDlpDesktop.ui', 'INFO'):
                ctk.set_appearance_mode('light')
                ctk.set_appearance_mode('dark')
            self.assertEqual((kept.calls, paint.calls), (2, 3))
        finally:
            ctk.set_appearance_mode('light')
            close(second)

    def test_everything_works_under_a_second_root(self):
        if ui_theme.icon_font_path() is None:
            self.skipTest('no Segoe icon font installed')
        first = self.build()
        second = self.build()               # a stale CTkImage would raise: image "pyimage1" doesn't exist
        self.assertEqual(first, second)


class Y_DarkModeTest(unittest.TestCase):
    """(WP7) The dark palette and switching the appearance mode while widgets live. Restores light."""

    # Measured dark contrasts of the WP7 palette (handoff note WP7.md); every TEXT_PAIRS entry is checked below.
    MEASURED = {('INK', 'SURFACE'): 13.25, ('INK', 'BG'): 15.09, ('INK', 'ROW'): 12.28, ('INK', 'SELECT'): 8.37,
                ('INK', 'SEGMENT_ON'): 8.37, ('MUTED', 'SURFACE'): 6.65, ('MUTED', 'BG'): 7.57,
                ('MUTED', 'SURFACE_ALT'): 5.94, ('MUTED', 'ROW'): 6.16, ('MUTED', 'IDLE_SOFT'): 5.38,
                ('ACCENT_TEXT', 'ACCENT_SOFT'): 5.85, ('ACCENT_TEXT', 'ACCENT_SOFT_HOVER'): 4.92,
                ('WHITE', 'ACCENT'): 4.52, ('WHITE', 'ACCENT_HOVER'): 5.46, ('OK', 'OK_SOFT'): 7.05,
                ('WARN', 'WARN_SOFT'): 7.13, ('DANGER', 'DANGER_SOFT'): 7.03, ('DANGER', 'DANGER_SOFT_HOVER'): 5.60}

    @classmethod
    def setUpClass(cls):
        ctk.set_appearance_mode('light')
        cls.root = hidden_root()

    @classmethod
    def tearDownClass(cls):
        ctk.set_appearance_mode('light')
        close(cls.root)

    def tearDown(self):
        ctk.set_appearance_mode('light')

    def test_every_text_pair_reaches_4_5_in_dark_mode(self):
        for fg, bg in ui_theme.TEXT_PAIRS:
            self.assertGreaterEqual(ui_theme.contrast(fg, bg, dark=True), 4.5, f'{fg[1]} on {bg[1]}')
        for (fg, bg), value in self.MEASURED.items():
            self.assertIn((TOKENS[fg], TOKENS[bg]), ui_theme.TEXT_PAIRS, f'{fg}/{bg}')
            self.assertAlmostEqual(ui_theme.contrast(TOKENS[fg], TOKENS[bg], dark=True), value, delta=0.05,
                                   msg=f'{fg}/{bg}')
        # FAINT is for placeholders and disabled text only: it is exempt and in no text pair.
        self.assertFalse([pair for pair in ui_theme.TEXT_PAIRS if ui_theme.FAINT in pair])

    def test_dark_surfaces_are_dark_and_light_values_unchanged(self):
        for name in ('BG', 'SURFACE', 'SURFACE_ALT', 'ROW', 'STRIPE', 'LINE', 'IDLE_SOFT', 'SEGMENT_ON', 'MENU',
                     'TRACK', 'DISABLED_SOFT', 'ACCENT_DISABLED', 'SELECT', 'SIDE', 'SIDE_CARD'):
            self.assertLess(ui_theme._luminance(TOKENS[name][1]), 0.1, name)
        # The two tokens WP7 added keep the light look exactly as it was.
        self.assertEqual(ui_theme.SEGMENT_ON[0], '#FFFFFF')
        self.assertEqual(ui_theme.ACCENT_DISABLED_TEXT[0], ui_theme.WHITE[0])
        # In dark mode a disabled primary button reads as disabled: dimmed text, not white.
        self.assertLess(ui_theme.contrast(ui_theme.ACCENT_DISABLED_TEXT, ui_theme.ACCENT_DISABLED, dark=True), 4.5)
        self.assertGreater(ui_theme.contrast(ui_theme.SEGMENT_ON, ui_theme.IDLE_SOFT, dark=True), 1.15)

    def test_switching_at_runtime_recolours_table_chip_disabled_button_and_progress_bar(self):
        import progress             # an app module: imported here only, after the import checks of A_TokensTest
        root = self.root
        table = ui_theme.table(root, [('a', 'A', 100)], empty='空')
        table.fill([(1, ('x',)), (2, ('y',)), (3, ('z',))])
        chip = ui_theme.chip(root, '失败', 'danger')
        button = ui_theme.button(root, '开始下载', None, kind='primary', state='disabled')
        bar = progress.SmoothProgressBar(root, height=6)
        bar.set(0.5, immediate=True)
        style = ttk.Style(root)
        calls = []

        def counted():
            calls.append(ctk.get_appearance_mode())
        ui_theme.on_appearance(counted)

        def look():
            return {'tree': str(style.lookup('App.Treeview', 'background')).upper(),
                    'heading': str(style.lookup('App.Treeview.Heading', 'background')).upper(),
                    'ink': str(style.lookup('App.Treeview', 'foreground')).upper(),
                    'stripe': str(table.tag_configure('odd', 'background')).upper(),
                    'hint': str(table.empty_label._label.cget('bg')).upper(),
                    'chip': str(chip._label.cget('bg')).upper(), 'chip_text': str(chip._label.cget('fg')).upper(),
                    'button': str(button._canvas.itemcget('inner_parts', 'fill')).upper(),
                    'button_text': str(button._text_label.cget('fg')).upper(),
                    'canvas': str(bar.canvas.cget('background')).upper(),
                    'track': str(bar.canvas.itemcget(bar._track, 'fill')).upper(),
                    'fill': str(bar.canvas.itemcget(bar._fill, 'fill')).upper()}

        def expected(side):
            return {'tree': ui_theme.SURFACE[side], 'heading': ui_theme.SURFACE_ALT[side], 'ink': ui_theme.INK[side],
                    'stripe': ui_theme.STRIPE[side], 'hint': ui_theme.SURFACE[side],
                    'chip': ui_theme.DANGER_SOFT[side], 'chip_text': ui_theme.DANGER[side],
                    'button': ui_theme.ACCENT_DISABLED[side], 'button_text': ui_theme.ACCENT_DISABLED_TEXT[side],
                    'canvas': ui_theme.SURFACE[side], 'track': ui_theme.TRACK[side], 'fill': ui_theme.ACCENT[side]}
        try:
            root.update()
            self.assertEqual(look(), expected(0))
            with self.assertNoLogs('YtDlpDesktop.ui', 'WARNING'):
                ctk.set_appearance_mode('dark')
            root.update()
            self.assertEqual(look(), expected(1))
            self.assertEqual(calls, ['Dark'])                   # once per change
            ctk.set_appearance_mode('dark')                     # no change: no call
            self.assertEqual(calls, ['Dark'])
            # 跟随系统: customtkinter polls Windows; a Windows switch to light recolours like a manual one.
            # (The real Windows setting must not take over while the mode is 'system': every check
            # happens inside the patch, and the mode is a user choice again before it ends.)
            with mock.patch.object(ctk.AppearanceModeTracker, 'detect_appearance_mode', return_value=0):
                ctk.set_appearance_mode('system')
                ctk.AppearanceModeTracker.update()
                root.update()
                self.assertEqual(look(), expected(0))
                self.assertEqual(calls, ['Dark', 'Light'])
                ctk.set_appearance_mode('light')                # already light: no call
            self.assertEqual(calls, ['Dark', 'Light'])
            # A table filled while dark stripes its new rows in the dark colour, and a refill keeps it.
            ctk.set_appearance_mode('dark')
            table.fill([(4, ('a',)), (5, ('b',))])
            self.assertEqual(str(table.tag_configure('odd', 'background')).upper(), ui_theme.STRIPE[1])
            self.assertEqual(calls, ['Dark', 'Light', 'Dark'])
        finally:
            ui_theme.off_appearance(counted)
            ctk.set_appearance_mode('light')
            for widget in (table.container, chip, button, bar):
                widget.destroy()
        self.assertEqual(ctk.get_appearance_mode(), 'Light')

    def test_disabled_button_dims_its_icon_like_its_text(self):
        if ui_theme.icon_font_path() is None:
            self.skipTest('no Segoe icon font installed')
        white = ui_theme.icon(ui_theme.ICON_DOWNLOAD, 'white')
        button = ui_theme.button(self.root, '开始下载', None, kind='primary', image=white, compound='left')
        born = ui_theme.button(self.root, '开始采集', None, kind='primary', state='disabled',
                               image=ui_theme.icon(ui_theme.ICON_SEARCH, 'white'), compound='left')
        try:
            self.assertIs(button.cget('image'), white)
            button.configure(state='disabled')
            dimmed = button.cget('image')
            self.assertIs(dimmed, ui_theme.icon(ui_theme.ICON_DOWNLOAD, 'on_accent_off'))
            self.assertIs(born.cget('image'), ui_theme.icon(ui_theme.ICON_SEARCH, 'on_accent_off'))
            # The glyph has the colour of the disabled text in each mode: white in light (as before), dimmed in dark.
            for side, key in ((0, 'light_image'), (1, 'dark_image')):
                colours = {color for _, color in dimmed.cget(key).convert('RGB').getcolors()}
                self.assertEqual(colours, {ui_theme._rgb(ui_theme.ACCENT_DISABLED_TEXT[side])}, key)
            self.assertEqual(dimmed.cget('light_image').tobytes(), white.cget('light_image').tobytes())
            button.configure(state='normal')
            self.assertIs(button.cget('image'), white)
            # An image given while disabled becomes the enabled image and is shown dimmed.
            button.configure(state='disabled')
            button.configure(image=ui_theme.icon(ui_theme.ICON_FOLDER, 'white'))
            self.assertIs(button.cget('image'), ui_theme.icon(ui_theme.ICON_FOLDER, 'on_accent_off'))
            button.configure(state='normal')
            self.assertIs(button.cget('image'), ui_theme.icon(ui_theme.ICON_FOLDER, 'white'))
            # Other kinds dim to FAINT; an image that is not a ui_theme icon stays as it is.
            soft = ui_theme.button(self.root, '打开文件夹', None, image=ui_theme.icon(ui_theme.ICON_FOLDER, 'accent'))
            soft.configure(state='disabled')
            self.assertIs(soft.cget('image'), ui_theme.icon(ui_theme.ICON_FOLDER, 'faint'))
            soft.destroy()
            plain = ui_theme.button(self.root, '无图', None, kind='primary', state='disabled')
            self.assertIsNone(plain.cget('image'))
            plain.destroy()
            ctk.set_appearance_mode('dark')
            self.root.update()
            self.assertEqual(str(button._text_label.cget('fg')).upper(), ui_theme.WHITE[1])
            button.configure(state='disabled')
            self.root.update()
            self.assertEqual(str(button._text_label.cget('fg')).upper(), ui_theme.ACCENT_DISABLED_TEXT[1])
        finally:
            ctk.set_appearance_mode('light')
            button.destroy()
            born.destroy()

    def test_disabled_on_switch_knob_stays_visible_in_dark_mode(self):
        knob = ui_theme.SWITCH_KNOB_DISABLED
        self.assertEqual(knob[0], ui_theme.ACCENT_DISABLED[0])                  # light look unchanged
        self.assertGreaterEqual(ui_theme.contrast(knob, ui_theme.SURFACE, dark=True), 2.0)
        self.assertGreaterEqual(ui_theme.contrast(knob, ui_theme.SWITCH_ON, dark=True), 1.2)
        # dimmer than the enabled knob
        self.assertLess(ui_theme._luminance(knob[1]), ui_theme._luminance(ui_theme.ACCENT[1]))

    def test_fitted_width_holds_the_whole_text(self):
        holder = ctk.CTkFrame(self.root)
        try:
            self.assertEqual(ui_theme.fitted_width(holder, '取消'), 72)
            width = ui_theme.fitted_width(holder, '恢复内置引擎')
            button = ui_theme.button(holder, '恢复内置引擎', None, kind='ghost', width=width)
            button.pack()
            self.root.update()
            scale = ui_theme._scaling(button)
            # The stored width alone already covers the label and the corner columns, so a redraw that
            # reapplies it cannot cut the text; and it adds at most one physical pixel to the natural size.
            natural = button._text_label.winfo_reqwidth() + 2 * round(ui_theme.RADIUS * scale)
            self.assertGreaterEqual(round(width * scale), natural)
            self.assertLessEqual(round(width * scale), natural + max(1, round(scale)))
        finally:
            holder.destroy()


class Z_ChevronTest(unittest.TestCase):
    """(polish, lead review item 1) Every option menu draws one chevron glyph in MUTED, centred in its
    button part; FAINT while disabled; it follows the appearance mode.  Restores light."""

    @classmethod
    def setUpClass(cls):
        ctk.set_appearance_mode('light')
        cls.root = hidden_root()

    @classmethod
    def tearDownClass(cls):
        ctk.set_appearance_mode('light')
        close(cls.root)

    def tearDown(self):
        ctk.set_appearance_mode('light')

    def fill(self, menu, tag):
        return str(menu._canvas.itemcget(tag, 'fill')).upper()

    def test_chevron_replaces_the_stock_arrow(self):
        menu = ui_theme.option_menu(self.root, ('音频 · 原始最佳', '视频 · MP4'), width=250)
        menu.pack()
        self.root.update()
        try:
            self.assertIsInstance(menu, ui_theme.AppOptionMenu)
            self.assertIsInstance(menu, ctk.CTkOptionMenu)
            family = ui_theme.icon_font_family(menu)
            if not family:
                self.assertEqual(self.fill(menu, 'dropdown_arrow'), ui_theme.MUTED[0].upper())
                self.skipTest('no Windows icon font: the stock arrow stays, in MUTED')
            self.assertIn(family, ui_theme.ICON_FONT_FAMILIES)
            canvas = menu._canvas
            self.assertEqual(canvas.itemcget('dropdown_arrow', 'state'), 'hidden')
            self.assertEqual(len(canvas.find_withtag('app_chevron')), 1)
            self.assertEqual(canvas.itemcget('app_chevron', 'text'), chr(ui_theme.CHEVRON_GLYPH))
            self.assertEqual(self.fill(menu, 'app_chevron'), ui_theme.MUTED[0].upper())
            scale = ui_theme._scaling(menu)
            x, y = canvas.coords('app_chevron')
            self.assertAlmostEqual(x, (250 - 36 / 2) * scale, delta=1)
            self.assertAlmostEqual(y, 36 / 2 * scale, delta=1)
            self.assertIn(str(-round(ui_theme.CHEVRON_SIZE * scale)), str(canvas.itemcget('app_chevron', 'font')))
            menu.configure(state='disabled')
            self.assertEqual(self.fill(menu, 'app_chevron'), ui_theme.FAINT[0].upper())
            menu.configure(state='normal')
            self.assertEqual(self.fill(menu, 'app_chevron'), ui_theme.MUTED[0].upper())
            menu.configure(text_color=ui_theme.INK)             # any redraw keeps one chevron, no stock arrow
            self.assertEqual(len(canvas.find_withtag('app_chevron')), 1)
            self.assertEqual(canvas.itemcget('dropdown_arrow', 'state'), 'hidden')
            ctk.set_appearance_mode('dark')
            self.root.update()
            self.assertEqual(self.fill(menu, 'app_chevron'), ui_theme.MUTED[1].upper())
        finally:
            menu.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=1)
