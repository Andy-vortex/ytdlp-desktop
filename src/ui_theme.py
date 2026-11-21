"""Design system of YT-DLP下载器: colour tokens, theme patch, widget factories, ttk styles.

One module owns every colour, font, size and widget look of the app (SPEC 3.13).
It imports the standard library, tkinter, customtkinter and PIL only, never an
app module, so that the collect worker and the self-test never pay for it.

Conventions
- Every colour token is a ``(light, dark)`` tuple. customtkinter widgets take the
  tuple as it is; plain Tk / ttk APIs (``tag_config``, ``ttk.Style``, ``tk.Menu``,
  canvas items) need one colour and get it through ``resolve(token)``.
- Sizes are logical pixels; customtkinter multiplies them by the window scaling.
  Code that talks to plain Tk multiplies by the scaling itself.
- The caller decides the appearance mode (``ctk.set_appearance_mode``) before it
  builds a window; ``apply_theme()`` never does.
"""
import ctypes
from dataclasses import dataclass
import logging
import os
import sys
import tkinter
import weakref
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont  # imported statically so that PyInstaller collects them

_log = logging.getLogger('YtDlpDesktop.ui')

# ---------------------------------------------------------------- tokens
BG = ('#F4F7FC', '#121722')                 # window
SURFACE = ('#FFFFFF', '#1B2333')            # cards, tables
SURFACE_ALT = ('#F7F9FD', '#212C3E')        # inputs, table heading
ROW = ('#F8FAFE', '#20293A')                # task rows
STRIPE = ('#FAFBFE', '#1E273A')             # odd table rows
LINE = ('#E5EAF3', '#2C3749')               # borders
INK = ('#18253B', '#E6ECF5')                # text
MUTED = ('#5B6780', '#A0A9B9')              # secondary text
FAINT = ('#9AA6B8', '#758093')              # placeholder and disabled text only
ACCENT = ('#3268EB', '#3F6FE8')             # filled controls
ACCENT_HOVER = ('#2557D0', '#3562D4')
ACCENT_TEXT = ('#2557D0', '#8FB0FF')        # accent-coloured text
ACCENT_SOFT = ('#EDF3FF', '#1E3260')        # soft buttons, info chip
ACCENT_SOFT_HOVER = ('#DFE9FF', '#263D73')
ACCENT_DISABLED = ('#A9BEF5', '#2A3446')    # disabled primary button
ACCENT_DISABLED_TEXT = ('#FFFFFF', '#8792A6')   # (WP7) its text: white in light mode, dimmed in dark mode
DISABLED_SOFT = ('#F1F4F9', '#222B3B')      # disabled soft / danger button
OK = ('#0B6B4C', '#5FD3A8')
OK_SOFT = ('#E7F6F0', '#12372E')
WARN = ('#8F5408', '#F0B45C')
WARN_SOFT = ('#FFF4E0', '#3D2E12')
DANGER = ('#B93026', '#FF9B8F')
DANGER_SOFT = ('#FDECEA', '#44201D')
DANGER_SOFT_HOVER = ('#FAD9D5', '#55302C')
IDLE_SOFT = ('#EEF2F9', '#283345')          # idle chip, segmented track
SEGMENT_ON = ('#FFFFFF', '#37435A')         # (WP7) selected segment: raised above the track in both modes
SELECT = ('#DCE7FF', '#2A4175')             # table selection
MENU = ('#F1F5FC', '#253042')               # option menus
MENU_BUTTON = ('#E5ECF8', '#313D51')
MENU_BUTTON_HOVER = ('#D7E3F7', '#3C4A63')
CHECK_BORDER = ('#AEBED6', '#515E73')
SCROLL = ('#D7E0EE', '#3A4658')
SCROLL_HOVER = ('#C0CCE0', '#4C5A72')
TRACK = ('#E9EFF9', '#283345')              # progress track
SWITCH_TRACK = ('#DDE4EF', '#313D51')
SWITCH_ON = ('#BFD2FF', '#31508F')
SWITCH_KNOB_OFF = ('#8A97AD', '#7C8AA3')
SWITCH_KNOB_DISABLED = ('#A9BEF5', '#56657F')   # (WP7) knob of a switch that is on and disabled
SIDE = ('#111F35', '#0D131D')               # sidebar
SIDE_HOVER = ('#203451', '#1B2435')
SIDE_ACTIVE = ('#253D63', '#1E3260')
SIDE_CARD = ('#192A44', '#161D2B')
SIDE_TEXT = ('#A9B8CF', '#A9B8CF')
SIDE_MUTED = ('#8A9BB8', '#8A9BB8')
SIDE_OK = ('#65D8AE', '#65D8AE')            # sidebar status line
SIDE_INFO = ('#7FB2FF', '#7FB2FF')
SIDE_WARN = ('#F0B45C', '#F0B45C')
SIDE_DANGER = ('#FF9B8F', '#FF9B8F')
WHITE = ('#FFFFFF', '#FFFFFF')              # text on ACCENT

# (foreground, background) pairs that carry text somewhere in the factories or in
# the shell built from them. test_theme.py keeps every light pair at 4.5:1 or more.
TEXT_PAIRS = [
    (INK, SURFACE), (INK, BG), (INK, SURFACE_ALT), (INK, ROW), (INK, STRIPE), (INK, SELECT),
    (INK, MENU), (INK, IDLE_SOFT), (INK, ACCENT_SOFT), (INK, LINE),
    (MUTED, SURFACE), (MUTED, BG), (MUTED, SURFACE_ALT), (MUTED, ROW), (MUTED, IDLE_SOFT),
    (ACCENT_TEXT, SURFACE), (ACCENT_TEXT, BG), (ACCENT_TEXT, ACCENT_SOFT), (ACCENT_TEXT, ACCENT_SOFT_HOVER),
    (WHITE, ACCENT), (WHITE, ACCENT_HOVER),
    (OK, OK_SOFT), (WARN, WARN_SOFT), (DANGER, DANGER_SOFT), (DANGER, DANGER_SOFT_HOVER),
    (SIDE_TEXT, SIDE), (SIDE_TEXT, SIDE_HOVER), (SIDE_MUTED, SIDE), (SIDE_MUTED, SIDE_CARD), (WHITE, SIDE_ACTIVE),
    (SIDE_OK, SIDE), (SIDE_INFO, SIDE), (SIDE_WARN, SIDE), (SIDE_DANGER, SIDE), (SIDE_WARN, SIDE_HOVER),
    (INK, SEGMENT_ON),                                  # (WP7) the selected segment of a tab bar
]

FAMILY = 'Microsoft YaHei UI'
SIZES = {'caption': 12, 'body': 13, 'heading': 15, 'title': 22, 'brand': 17}
BOLD_ROLES = ('heading', 'title', 'brand')
PAGE_PAD = 24
GAP = 12
RADIUS = 8
CONTROL_H = 36
CONTROL_H_SM = 28
LABEL_H = {'caption': 18, 'body': 22, 'heading': 22, 'title': 32, 'brand': 24}
SIDEBAR_W = 168
ROW_H = 52
PAGE_MAX = 1080
WHEEL_STEP = 56          # logical px a ScrollArea moves per wheel notch
TABLE_ROW_H = 32         # logical px of one table row
TABLE_FONT = 12          # table text uses the caption size
# (2.0.0 S4) The spacing scale of the pages (00-system 2.2): INLINE between neighbours on one line, GAP between
# two lines, PAD inside notice bars and dialogs, SECTION between parts of a page.
INLINE = 8
PAD = 16
SECTION = 24
SETTINGS_MAX = 560       # the settings page is never wider (a label stays near its switch)
PATH_W = 320             # the width of a path button (path_button)
# (2.0.0 S4) The few words the shared pieces show themselves, in the running language.  This module imports no
# app module, so the app fills them once at start-up (studio: common.cancel / save / choose_ellipsis).
WORDS = {'cancel': '', 'save': '', 'choose': ''}

# Segoe Fluent Icons / Segoe MDL2 Assets code points (present in both fonts).
ICON_DOWNLOAD = 0xE896
ICON_SEARCH = 0xE721
ICON_LIBRARY = 0xE8F1
ICON_LOG = 0xE9F9
ICON_SETTINGS = 0xE713
ICON_DONE = 0xE930
ICON_CLOCK = 0xE823
ICON_ERROR = 0xE783
ICON_PAUSE = 0xE769
ICON_STOP = 0xE73B          # filled square: a stopped task (the outlined pause bars read like missing glyphs)
ICON_PENDING = 0xEA3A       # empty circle: a task that never started
ICON_FOLDER = 0xE8B7
ICON_SKIP = 0xE72A
ICON_MORE = 0xE712          # the '⋯' of menu_button
ICON_ARCHIVE = 0xE7B8       # (2.0.0 S4) the download folder button while downloads are archived
ICON_UPDATE = 0xE74A        # (2.0.0 S4) 'Up': the sidebar's 'Update available' link
ICON_FONT_FILES = ('SegoeIcons.ttf', 'segmdl2.ttf')   # tried in this order under %WINDIR%\Fonts
ICON_PX = 64                                           # master size the glyphs are drawn at

_CHIP_TONES = {'idle': (MUTED, IDLE_SOFT), 'info': (ACCENT_TEXT, ACCENT_SOFT), 'ok': (OK, OK_SOFT),
               'warn': (WARN, WARN_SOFT), 'danger': (DANGER, DANGER_SOFT)}
_ICON_TONES = {'ink': INK, 'muted': MUTED, 'faint': FAINT, 'accent': ACCENT, 'ok': OK, 'warn': WARN,
               'danger': DANGER, 'white': WHITE, 'side': SIDE_TEXT, 'side_warn': SIDE_WARN,
               'idle': FAINT, 'info': ACCENT}          # the chip tone names work for icons too
# kind -> (fill, hover fill, text, disabled fill, disabled text)
_BUTTON_KINDS = {
    'primary': (ACCENT, ACCENT_HOVER, WHITE, ACCENT_DISABLED, ACCENT_DISABLED_TEXT),
    'soft': (ACCENT_SOFT, ACCENT_SOFT_HOVER, ACCENT_TEXT, DISABLED_SOFT, FAINT),
    'ghost': ('transparent', ACCENT_SOFT, ACCENT_TEXT, 'transparent', FAINT),
    'danger': (DANGER_SOFT, DANGER_SOFT_HOVER, DANGER, DISABLED_SOFT, FAINT),
}


# ---------------------------------------------------------------- colour helpers
def _pick(color, dark=False):
    if isinstance(color, (tuple, list)):
        return color[1] if dark else color[0]
    return color


def _rgb(color):
    value = str(color).lstrip('#')
    if len(value) != 6:
        raise ValueError(f'not a #RRGGBB colour: {color!r}')
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(color):
    def linear(channel):
        channel /= 255
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
    red, green, blue = _rgb(color)
    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def contrast(a, b, dark=False):
    """WCAG 2 contrast ratio of two colours ('#RRGGBB' strings or tokens; ``dark`` picks the token side)."""
    first, second = _luminance(_pick(a, dark)), _luminance(_pick(b, dark))
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def resolve(token):
    """One '#RRGGBB' of a ``(light, dark)`` token for the current appearance mode; a string passes through."""
    if isinstance(token, (tuple, list)):
        return token[1] if ctk.get_appearance_mode() == 'Dark' else token[0]
    return token


def tone_colors(tone):
    """``(text token, fill token)`` of a chip tone: idle, info, ok, warn or danger."""
    try:
        return _CHIP_TONES[tone]
    except KeyError:
        raise ValueError(f'unknown tone {tone!r}; expected one of {sorted(_CHIP_TONES)}') from None


# ---------------------------------------------------------------- process and theme setup
_dpi_done = False


def enable_dpi_awareness():
    """Make the process per-monitor DPI aware; call it before the first Tk root exists.

    customtkinter makes the same call, but only after it has created the root, which leaves
    ``tk scaling`` and the screen metrics at their 96 dpi values. Calling it early gives plain
    Tk and ttk the real scaling. A second call, or a call after a root exists, is harmless.
    """
    global _dpi_done
    if _dpi_done or not sys.platform.startswith('win'):
        return
    _dpi_done = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def theme_patch():
    """The values ``apply_theme()`` writes into ``ctk.ThemeManager.theme``: ``{widget: {key: value}}``."""
    def c(token):
        return list(token)
    return {
        'CTk': {'fg_color': c(BG)},
        'CTkToplevel': {'fg_color': c(BG)},
        'CTkFrame': {'fg_color': c(SURFACE), 'top_fg_color': c(SURFACE_ALT), 'border_color': c(LINE)},
        'CTkFont': {'family': FAMILY, 'size': SIZES['body']},
        'CTkLabel': {'text_color': c(INK), 'border_color': c(LINE)},
        'CTkButton': {'fg_color': c(ACCENT), 'hover_color': c(ACCENT_HOVER), 'text_color': c(WHITE),
                      'corner_radius': RADIUS},
        'CTkEntry': {'fg_color': c(SURFACE_ALT), 'border_color': c(LINE), 'border_width': 1,
                     'corner_radius': RADIUS, 'text_color': c(INK), 'placeholder_text_color': c(FAINT)},
        'CTkTextbox': {'fg_color': c(SURFACE_ALT), 'border_color': c(LINE), 'border_width': 1,
                       'corner_radius': RADIUS, 'text_color': c(INK), 'scrollbar_button_color': c(SCROLL),
                       'scrollbar_button_hover_color': c(SCROLL_HOVER)},
        'CTkOptionMenu': {'fg_color': c(MENU), 'button_color': c(MENU_BUTTON),
                          'button_hover_color': c(MENU_BUTTON_HOVER), 'text_color': c(INK),
                          'text_color_disabled': c(FAINT), 'corner_radius': RADIUS},
        'DropdownMenu': {'fg_color': c(SURFACE), 'hover_color': c(ACCENT_SOFT), 'text_color': c(INK)},
        'CTkCheckBox': {'fg_color': c(ACCENT), 'hover_color': c(ACCENT_HOVER), 'border_color': c(CHECK_BORDER),
                        'border_width': 2, 'corner_radius': 5, 'text_color': c(INK), 'checkmark_color': c(WHITE)},
        'CTkSwitch': {'fg_color': c(SWITCH_TRACK), 'progress_color': c(SWITCH_ON), 'button_color': c(ACCENT),
                      'button_hover_color': c(ACCENT_HOVER), 'text_color': c(INK)},
        'CTkScrollbar': {'button_color': c(SCROLL), 'button_hover_color': c(SCROLL_HOVER)},
        'CTkSegmentedButton': {'fg_color': c(IDLE_SOFT), 'selected_color': c(SEGMENT_ON),
                               'selected_hover_color': c(SEGMENT_ON), 'unselected_color': c(IDLE_SOFT),
                               'unselected_hover_color': c(LINE), 'text_color': c(INK),
                               'text_color_disabled': c(FAINT), 'corner_radius': RADIUS, 'border_width': 3},
        # Widgets the app does not use today. They are patched so that nothing created
        # without explicit colours can fall back to the stock blue and grey.
        'CTkProgressBar': {'fg_color': c(TRACK), 'progress_color': c(ACCENT), 'border_color': c(LINE)},
        'CTkRadioButton': {'fg_color': c(ACCENT), 'hover_color': c(ACCENT_HOVER), 'border_color': c(CHECK_BORDER),
                           'text_color': c(INK)},
        'CTkSlider': {'fg_color': c(TRACK), 'progress_color': c(SWITCH_ON), 'button_color': c(ACCENT),
                      'button_hover_color': c(ACCENT_HOVER)},
        'CTkComboBox': {'fg_color': c(SURFACE_ALT), 'border_color': c(LINE), 'border_width': 1,
                        'corner_radius': RADIUS, 'button_color': c(MENU_BUTTON),
                        'button_hover_color': c(MENU_BUTTON_HOVER), 'text_color': c(INK),
                        'text_color_disabled': c(FAINT)},
        'CTkScrollableFrame': {'label_fg_color': c(IDLE_SOFT)},
    }


_patched_theme = None


def apply_theme():
    """Load the stock 'blue' theme and overwrite its colours, radii and font with the tokens.

    Idempotent: a repeated call returns at once while the patched theme is still the loaded
    one, and patches again when something replaced it. Call it before the first widget is
    created. It does not touch the appearance mode.
    """
    global _patched_theme
    if _patched_theme is not None and ctk.ThemeManager.theme is _patched_theme:
        return
    ctk.set_default_color_theme('blue')
    theme = ctk.ThemeManager.theme
    for widget, values in theme_patch().items():
        theme[widget].update(values)
    _patched_theme = theme


# ---------------------------------------------------------------- shared helpers
def _scaling(widget):
    """Widget scaling of the window that holds ``widget`` (2.0 at 200 %)."""
    try:
        return float(ctk.ScalingTracker.get_widget_scaling(widget))
    except Exception:
        try:
            window = widget.winfo_toplevel()
            return float(ctk.ScalingTracker.get_window_dpi_scaling(window)) * ctk.ScalingTracker.widget_scaling
        except Exception:
            return 1.0


def _tk_root(widget):
    while getattr(widget, 'master', None) is not None:
        widget = widget.master
    return widget


def _default_root():
    return getattr(tkinter, '_default_root', None)


_fonts = {}
_font_root = None


def font(role='body'):
    """The shared ``CTkFont`` of a text role: caption, body, heading, title or brand.

    Created on first use (it needs a Tk root) and kept for the life of that root.
    """
    global _font_root
    root = _default_root()
    if root is not _font_root:      # a font object belongs to the interpreter it was created in
        _fonts.clear()
        _font_root = root
    try:
        return _fonts[role]
    except KeyError:
        pass
    created = ctk.CTkFont(family=FAMILY, size=SIZES[role], weight='bold' if role in BOLD_ROLES else 'normal')
    _fonts[role] = created
    return created


_measure_fonts = weakref.WeakKeyDictionary()    # Tk root -> {scaled font tuple: tkinter Font}


def _measure_font(widget, font_spec):
    """A plain Tk font with the physical pixel size ``widget`` draws ``font_spec`` at.

    The font is built from separate options. Built from one description ("family -26 bold")
    Tk would round the size to whole points and measure about 3 % short at 200 %.
    """
    scale = _scaling(widget)
    if isinstance(font_spec, ctk.CTkFont):
        family, size, style = font_spec.create_scaled_tuple(scale)
        styles = str(style).split()
    else:
        family, size = font_spec[0], -abs(round(font_spec[1] * scale))
        styles = []
        for part in font_spec[2:]:
            words = part if isinstance(part, (tuple, list)) else str(part).split()
            styles.extend(str(word) for word in words)
    key = (family, size, 'bold' in styles, 'italic' in styles)
    root = _tk_root(widget)
    cache = _measure_fonts.setdefault(root, {})
    measure = cache.get(key)
    if measure is None:
        measure = cache[key] = tkfont.Font(root=root, family=family, size=size,
                                           weight='bold' if key[2] else 'normal',
                                           slant='italic' if key[3] else 'roman')
    return measure


def _ellipsize(text, measure, width):
    """``text`` cut to ``width`` pixels with a trailing ellipsis (``measure`` maps text to pixels)."""
    if measure(text) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(text[:middle].rstrip() + '…') <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + '…'


def cancel_after(widget, job):
    """Cancel a pending ``after`` job, so that it cannot fire on a destroyed widget."""
    if job is not None:
        try:
            widget.after_cancel(job)
        except (tkinter.TclError, ValueError):
            pass


def _bind_outer(widget, sequence, handler):
    """Bind on the outer Tk frame of a widget, keeping the bindings that are already there.

    customtkinter widgets redirect ``bind`` to their inner canvas and label, which report
    their own sizes; geometry code here needs the events of the outer frame.
    """
    tkinter.Misc.bind(widget, sequence, handler, add='+')


# ---------------------------------------------------------------- appearance changes
_appearance_callbacks = []              # callables; a bound method of a Tk widget as a weakref.WeakMethod
_appearance_hooked = False
_styled_roots = weakref.WeakSet()       # Tk roots whose ttk styles are ours
_tables = weakref.WeakSet()             # live AppTable widgets
# How Tk words a call on a widget, or in an application, that has been destroyed.
_GONE_ERRORS = ('invalid command name', 'bad window path name', 'application has been destroyed')


def _hook_appearance(root=None):
    global _appearance_hooked
    if not _appearance_hooked:
        _appearance_hooked = True
        ctk.AppearanceModeTracker.add(_appearance_changed, root)


def _appearance_target(entry):
    """The callable behind an entry of the callback list; None once the widget that owned it is collected."""
    return entry() if isinstance(entry, weakref.WeakMethod) else entry


def _appearance_forget(entry):
    for index, kept in enumerate(_appearance_callbacks):
        if kept is entry:
            del _appearance_callbacks[index]
            return


def _owner_destroyed(callback):
    """True for a bound method of a Tk widget that no longer exists."""
    owner = getattr(callback, '__self__', None)
    if not isinstance(owner, tkinter.Misc):
        return False
    try:
        return not owner.winfo_exists()
    except tkinter.TclError:            # the whole application of that widget is gone
        return True
    except Exception:
        return False


def _appearance_changed(mode=None):
    for root in list(_styled_roots):
        try:
            if root.winfo_exists():
                _configure_ttk(root)
        except tkinter.TclError:
            pass
    for tree in list(_tables):
        try:
            tree.recolor()
        except tkinter.TclError:
            pass
    for entry in list(_appearance_callbacks):
        callback = _appearance_target(entry)
        if callback is None or _owner_destroyed(callback):
            _appearance_forget(entry)           # its window was closed: nothing is left to recolour
            continue
        try:
            callback()
        except tkinter.TclError as error:
            owner = getattr(callback, '__self__', None)
            live_window = isinstance(owner, tkinter.Misc) and not _owner_destroyed(callback)
            if any(mark in str(error) for mark in _GONE_ERRORS) and not live_window:
                # A function that paints a destroyed widget would fail again on every later change.
                # A method of a window that still exists stays registered: its error can be
                # transient (a child being rebuilt) and the window must keep following the mode.
                _appearance_forget(entry)
                _log.warning('appearance callback dropped, its widget no longer exists: %r', callback)
            else:
                _log.exception('appearance callback failed: %r', callback)
        except Exception:
            _log.exception('appearance callback failed: %r', callback)


def on_appearance(callback):
    """Call ``callback()`` after every appearance-mode change (for colours set through ``resolve``).

    Callbacks of a closed window drop out by themselves. A bound method of a Tk widget (a
    window's own method, for example) is held weakly, so this list never keeps the window
    alive, and it is no longer called once that widget is destroyed. Any other callable is
    dropped the first time it fails because the widget it paints no longer exists. A callback
    that fails for another reason is logged and stays registered.
    """
    entry = callback
    if isinstance(getattr(callback, '__self__', None), tkinter.Misc):
        try:
            entry = weakref.WeakMethod(callback)
        except TypeError:
            pass
    _appearance_callbacks.append(entry)
    _hook_appearance()


def off_appearance(callback):
    """Stop calling ``callback``; one that is not registered is ignored.

    A window that closes does not need this (see ``on_appearance``). It is for a callback
    whose widgets live on while the callback itself is no longer wanted.
    """
    _appearance_callbacks[:] = [entry for entry in _appearance_callbacks
                                if _appearance_target(entry) not in (None, callback)]


# ---------------------------------------------------------------- labels, buttons, cards, chips
def label(parent, text='', role='body', **kw):
    """A ``CTkLabel`` in the font and height of ``role``; every keyword can be overridden."""
    kw.setdefault('font', font(role))
    kw.setdefault('text_color', INK)
    kw.setdefault('height', LABEL_H[role])
    return ctk.CTkLabel(parent, text=text, **kw)


class AppButton(ctk.CTkButton):
    """Button with four looks (primary, soft, ghost, danger) and a real disabled look.

    ``CTkButton`` keeps its fill when it is disabled, so a disabled primary button still
    reads as clickable. This class swaps the fill together with the state, both for
    ``configure(state=...)`` and for ``state='disabled'`` given to the constructor.
    """

    def __init__(self, master, text='', command=None, kind='soft', size='md', **kw):
        if kind not in _BUTTON_KINDS:
            raise ValueError(f'unknown button kind {kind!r}; expected one of {sorted(_BUTTON_KINDS)}')
        fill, hover, ink, fill_off, ink_off = _BUTTON_KINDS[kind]
        small = size == 'sm'
        kw.setdefault('height', CONTROL_H_SM if small else CONTROL_H)
        kw.setdefault('width', 72)
        kw.setdefault('corner_radius', RADIUS)
        kw.setdefault('font', font('caption' if small else 'body'))
        kw.setdefault('fg_color', fill)
        kw.setdefault('hover_color', hover)
        kw.setdefault('text_color', ink)
        kw.setdefault('text_color_disabled', ink_off)
        # configure() runs inside CTkButton.__init__ (for the cursor), so these exist first.
        self._kind = kind
        self._fill = kw['fg_color']
        self._fill_off = fill_off
        self._image_on = kw.get('image')        # (WP7) the enabled image; disabled shows a dimmed copy
        super().__init__(master, text=text, command=command, **kw)
        if self.cget('state') == 'disabled':
            self.configure(state='disabled')

    @property
    def kind(self):
        return self._kind

    def configure(self, require_redraw=False, **kwargs):
        if getattr(self, '_kind', None) is not None:
            if 'state' in kwargs:
                kwargs.setdefault('fg_color', self._fill_off if kwargs['state'] == 'disabled' else self._fill)
            elif 'fg_color' in kwargs and self.cget('state') != 'disabled':
                self._fill = kwargs['fg_color']     # an explicit fill becomes the enabled look
            if 'state' in kwargs or 'image' in kwargs:
                self._swap_image(kwargs)
        super().configure(require_redraw=require_redraw, **kwargs)

    def _swap_image(self, kwargs):
        """(WP7) A ``ui_theme.icon`` on a disabled button is shown dimmed like the text."""
        if 'image' in kwargs:
            self._image_on = kwargs['image']    # an explicit image becomes the enabled look
        state = kwargs.get('state', getattr(self, '_state', 'normal'))
        shown = _disabled_icon(self._image_on, self._kind) if state == 'disabled' else self._image_on
        if 'image' in kwargs or shown is not getattr(self, '_image', None):
            kwargs['image'] = shown

    def set_kind(self, kind, image=None):
        """(2.0.0 S4) Show this button as another ``kind`` (the one solid button of a view moves with the
        stage of its flow).  The same kind and no image: nothing happens, so calling it on every refresh
        costs nothing; ``image`` (a ``ui_theme.icon``) replaces the icon, dimmed while disabled."""
        if kind == self._kind and image is None:
            return
        fill, hover, ink, fill_off, ink_off = _BUTTON_KINDS[kind]
        self._kind, self._fill, self._fill_off = kind, fill, fill_off
        changes = {'hover_color': hover, 'text_color': ink, 'text_color_disabled': ink_off,
                   'fg_color': fill_off if self.cget('state') == 'disabled' else fill}
        if image is not None or self._image_on is not None:
            changes['image'] = self._image_on if image is None else image
        self.configure(**changes)       # an image passes _swap_image: dimmed for the new kind when disabled


def fitted_width(parent, text, size='md', ghost=False, inset=GAP):
    """(WP7) The width (logical px, at least 72) an ``AppButton`` needs to show ``text`` in full.

    (2.0.0 S4 visual fix) ``ghost=True``: the width of a ghost button, its text + GAP on each side and no 72
    floor, so the space around a ghost button is only the gap the caller gives it (no empty button edge).
    (final polish) ``inset``: the space on each side of a ghost button's text (the log page's Copy sits next to
    its '⋯' with INLINE).

    A button narrower than its text grows only through grid propagation; a redraw that
    reapplies the stored width can leave it at that width for a moment, and the label is
    then cut on both sides. Given this width the button asks for its full size itself.
    """
    scale = _scaling(parent) or 1.0
    measure = _measure_font(parent, font('caption' if size == 'sm' else 'body'))
    if ghost:
        return int(-(-measure.measure(text) // scale)) + 2 * inset
    # text + the label's 1 px border on each side + the button's corner columns
    needed = measure.measure(text) + 2 + 2 * round(RADIUS * scale)
    return max(72, int(-(-needed // scale)))


def button(parent, text, command=None, kind='soft', size='md', **kw):
    """An ``AppButton`` of ``kind`` ('primary', 'soft', ...) and ``size`` ('md' / 'sm')."""
    return AppButton(parent, text=text, command=command, kind=kind, size=size, **kw)


class Chip(ctk.CTkLabel):
    """Small rounded status label. ``set(text, tone)`` is cheap enough for progress updates."""

    def __init__(self, master, text='', tone='idle', **kw):
        ink, fill = tone_colors(tone)
        kw.setdefault('font', font('caption'))
        kw.setdefault('corner_radius', 11)
        kw.setdefault('height', 22)
        kw.setdefault('width', 64)
        kw.setdefault('text_color', ink)
        kw.setdefault('fg_color', fill)
        super().__init__(master, text=text, **kw)
        self.tone = tone

    def set(self, text, tone=None):
        """Show ``text``; recolour only when ``tone`` is given and differs from the current one."""
        if tone is not None and tone != self.tone:
            ink, fill = tone_colors(tone)
            self.tone = tone
            self.configure(text=text, text_color=ink, fg_color=fill)
        elif text != self.cget('text'):
            self.configure(text=text)


def chip(parent, text='', tone='idle', **kw):
    return Chip(parent, text=text, tone=tone, **kw)


# ---------------------------------------------------------------- icons
_icon_pictures = {}     # (code, tone) -> (light PIL image, dark PIL image), for the life of the process
_icon_images = {}       # (code, tone, size) -> CTkImage, valid for _icon_root only
_icon_root = None
_icon_fonts = {}        # font path -> PIL font
_icon_warned = False


def icon_font_path():
    """Path of the first installed icon font, or None."""
    folder = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
    for name in ICON_FONT_FILES:
        path = folder / name
        if path.is_file():
            return path
    return None


def _icon_picture(code, tone):
    key = (code, tone)
    pictures = _icon_pictures.get(key)
    if pictures is not None:
        return pictures
    path = icon_font_path()
    if path is None:
        raise FileNotFoundError('no icon font installed')
    glyphs = _icon_fonts.get(path)
    if glyphs is None:
        glyphs = _icon_fonts[path] = ImageFont.truetype(str(path), int(ICON_PX * 0.84))
    mask = glyphs.getmask(chr(code))
    if mask.getbbox() is None or bytes(mask) == bytes(glyphs.getmask('\uffff')):
        raise LookupError(f'{path.name} has no glyph U+{code:04X}')
    token = _ICON_TONES[tone]
    drawn = []
    for color in (token[0], token[1]):
        red, green, blue = _rgb(color)
        # The transparent pixels carry the glyph colour, so scaling leaves no dark fringe.
        picture = Image.new('RGBA', (ICON_PX, ICON_PX), (red, green, blue, 0))
        ImageDraw.Draw(picture).text((ICON_PX / 2, ICON_PX / 2), chr(code), font=glyphs,
                                     fill=(red, green, blue, 255), anchor='mm')
        drawn.append(picture)
    pictures = _icon_pictures[key] = tuple(drawn)
    return pictures


def icon(code, tone='ink', size=16):
    """A ``CTkImage`` of one icon-font glyph, or None when it cannot be drawn (show text only then).

    Tones: ink, muted, faint, accent, ok, warn, danger, white, side, and the chip tone
    names idle (= faint) and info (= accent).
    """
    global _icon_root, _icon_warned
    try:
        root = _default_root()
        if root is not _icon_root:
            # A CTkImage holds PhotoImages of the interpreter that first drew it and
            # cannot be shown under another root.
            _icon_images.clear()
            _icon_root = root
        key = (code, tone, size)
        image = _icon_images.get(key)
        if image is None:
            light, dark = _icon_picture(code, tone)
            image = _icon_images[key] = ctk.CTkImage(light_image=light, dark_image=dark, size=(size, size))
        return image
    except Exception as error:
        if not _icon_warned:
            _icon_warned = True
            _log.warning('icons unavailable, falling back to text: %s', error)
        return None


# (WP7) A disabled button shows its icon in the colour of its disabled text: on a primary
# button ACCENT_DISABLED_TEXT (white in light mode, as before), on the other kinds FAINT.
_ICON_TONES['on_accent_off'] = ACCENT_DISABLED_TEXT
_BUTTON_ICON_OFF = {'primary': 'on_accent_off'}


def _disabled_icon(image, kind):
    """The dimmed copy of a ``ui_theme.icon`` image for a disabled ``kind`` button; any other image as is."""
    if image is None:
        return None
    for (code, tone, size), known in list(_icon_images.items()):
        if known is image:
            off = _BUTTON_ICON_OFF.get(kind, 'faint')
            return image if tone == off else (icon(code, off, size) or image)
    return image


# ---------------------------------------------------------------- inputs
class AppEntry(ctk.CTkEntry):
    """Entry whose placeholder also works together with a ``textvariable``.

    customtkinter never shows ``placeholder_text`` once a ``textvariable`` is given. Here the
    placeholder is a faint label laid over the entry (``placeholder_label``), visible while the
    entry is empty, focused or not. A disabled entry shows its text in MUTED on SURFACE (the look of a
    dimmed ``textbox``: only the thin border is left, like an empty slot); a read-only one keeps INK.
    """

    def __init__(self, master, placeholder='', **kw):
        if kw.get('placeholder_text') is not None and (kw.get('textvariable') is not None or placeholder):
            raise ValueError("pass placeholder='...' instead of placeholder_text: customtkinter ignores "
                             'placeholder_text when a textvariable is set')
        kw.setdefault('height', CONTROL_H)
        kw.setdefault('font', font('body'))
        kw.setdefault('text_color', INK)
        self.placeholder_label = None
        self._ink = kw['text_color']
        self._fill = None
        self._hint_text = placeholder or ''
        self._hint_shown = False
        self._hint_trace = None
        self._hint_job = None
        super().__init__(master, **kw)
        self._fill = self.cget('fg_color')
        if self.cget('state') == 'disabled':
            self.configure(state='disabled')
        if not self._hint_text:
            return
        self.placeholder_label = ctk.CTkLabel(self, text=self._hint_text, font=font('caption'), text_color=FAINT,
                                              fg_color=self._hint_fill(), height=LABEL_H['caption'], anchor='w',
                                              cursor='xterm')
        self.placeholder_label.bind('<Button-1>', self._hint_clicked)
        for sequence in ('<KeyRelease>', '<FocusOut>', '<<Paste>>', '<<Cut>>'):
            self.bind(sequence, self._hint_later)
        _bind_outer(self, '<Configure>', self._hint_resized)
        self._hint_watch(self.cget('textvariable'))
        self._hint_sync()

    def _hint_fill(self):
        fill = self.cget('fg_color')
        return self.cget('bg_color') if fill == 'transparent' else fill

    def _hint_watch(self, variable):
        self._hint_unwatch()
        if variable is not None and variable != '':
            self._hint_variable = variable
            self._hint_trace = variable.trace_add('write', self._hint_written)

    def _hint_unwatch(self):
        if self._hint_trace is not None:
            try:
                self._hint_variable.trace_remove('write', self._hint_trace)
            except (tkinter.TclError, RuntimeError):
                pass
            self._hint_trace = None

    def _hint_written(self, *_):
        # Traces fire newest first: the inner entry has not taken the new value yet,
        # so the variable is the truth here.
        try:
            empty = str(self._hint_variable.get()) == ''
        except (tkinter.TclError, RuntimeError):
            return
        self._hint_sync(empty)

    def _hint_later(self, event=None):
        if self._hint_job is None:
            try:
                self._hint_job = self.after_idle(self._hint_idle)
            except tkinter.TclError:
                pass

    def _hint_idle(self):
        self._hint_job = None
        self._hint_sync()

    def _hint_clicked(self, event=None):
        self.focus_set()
        return 'break'      # the window-wide click binding would move the focus to the label

    def _hint_resized(self, event=None):
        hint = self.placeholder_label
        if hint is None:
            return
        width = self.winfo_width()
        text = self._hint_text
        if width > 1:
            room = width - round(20 * _scaling(self))
            text = _ellipsize(text, _measure_font(self, hint.cget('font')).measure, room)
        if text != hint.cget('text'):
            hint.configure(text=text)

    def _hint_sync(self, empty=None):
        hint = self.placeholder_label
        if hint is None:
            return
        try:
            if empty is None:
                empty = self.get() == ''
            if empty and not self._hint_shown:
                hint.place(x=10, rely=0.5, anchor='w')
            elif not empty and self._hint_shown:
                hint.place_forget()
            self._hint_shown = bool(empty)
        except tkinter.TclError:
            pass

    def configure(self, require_redraw=False, **kwargs):
        if getattr(self, '_ink', None) is not None:
            if 'state' in kwargs:
                kwargs.setdefault('text_color', MUTED if kwargs['state'] == 'disabled' else self._ink)
            elif 'text_color' in kwargs and self.cget('state') != 'disabled':
                self._ink = kwargs['text_color']    # an explicit colour becomes the enabled look
        if getattr(self, '_fill', None) is not None:
            if 'state' in kwargs:
                kwargs.setdefault('fg_color', SURFACE if kwargs['state'] == 'disabled' else self._fill)
            elif 'fg_color' in kwargs and self.cget('state') != 'disabled':
                self._fill = kwargs['fg_color']     # an explicit fill becomes the enabled look
        watch = 'textvariable' in kwargs
        super().configure(require_redraw=require_redraw, **kwargs)
        if 'fg_color' in kwargs and getattr(self, 'placeholder_label', None) is not None:
            self.placeholder_label.configure(fg_color=self._hint_fill())
        if watch and getattr(self, 'placeholder_label', None) is not None:
            self._hint_watch(self.cget('textvariable'))
            self._hint_sync()

    def insert(self, index, string):
        result = super().insert(index, string)
        self._hint_sync()
        return result

    def delete(self, first_index, last_index=None):
        super().delete(first_index, last_index)
        self._hint_sync()

    def destroy(self):
        self._hint_unwatch()
        cancel_after(self, self._hint_job)
        self._hint_job = None
        super().destroy()


def entry(parent, placeholder='', **kw):
    """Single-line input, 36 px high. ``placeholder`` works with and without a ``textvariable``."""
    return AppEntry(parent, placeholder=placeholder, **kw)


CHEVRON_GLYPH = 0xE70D          # ChevronDown of Segoe Fluent Icons / Segoe MDL2 Assets
ICON_FONT_FAMILIES = ('Segoe Fluent Icons', 'Segoe MDL2 Assets')
CHEVRON_SIZE = 12              # logical px of the chevron's em box
_icon_families = weakref.WeakKeyDictionary()     # Tk root -> installed icon font family or ''


def icon_font_family(widget):
    """The Tk family name of the first installed icon font ('' when there is none)."""
    root = _tk_root(widget)
    family = _icon_families.get(root)
    if family is None:
        try:
            installed = set(tkfont.families(root))
        except tkinter.TclError:
            installed = set()
        family = next((name for name in ICON_FONT_FAMILIES if name in installed), '')
        _icon_families[root] = family
    return family


class AppOptionMenu(ctk.CTkOptionMenu):
    """Drop-down whose arrow is one clean chevron (lead review item 1).

    customtkinter draws its arrow on Windows as the letter "Y" of its own shapes font, which
    reads as a squashed double chevron at 200 %. Here that item is hidden and the ChevronDown
    glyph of the Windows icon font is drawn instead, in MUTED (FAINT while disabled), centred
    in the button part. Without an icon font the stock arrow stays, recoloured the same way.
    """

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        try:
            self._draw_chevron()
        except tkinter.TclError:
            pass

    def _draw_chevron(self):
        canvas = self._canvas
        disabled = self._state == tkinter.DISABLED
        color = self._apply_appearance_mode(FAINT if disabled else MUTED)
        family = icon_font_family(self)
        if not family:
            canvas.itemconfigure('dropdown_arrow', fill=color)
            return
        canvas.itemconfigure('dropdown_arrow', state='hidden')
        x = self._apply_widget_scaling(self._current_width - self._current_height / 2)
        y = self._apply_widget_scaling(self._current_height / 2)
        size = max(1, round(self._apply_widget_scaling(CHEVRON_SIZE)))
        if not canvas.find_withtag('app_chevron'):
            canvas.create_text(0, 0, text=chr(CHEVRON_GLYPH), anchor='center', tags='app_chevron')
        canvas.coords('app_chevron', round(x), round(y))
        canvas.itemconfigure('app_chevron', font=(family, -size), fill=color)
        canvas.tag_raise('app_chevron')


def _label_maps(values, labels):
    """(1.7.0) (value -> label, label -> value) of a labeled control: ``labels`` is a list parallel
    to ``values`` or a dict value -> label; a value without a label shows as str(value)."""
    values = list(values)
    if isinstance(labels, dict):
        shown = [labels.get(value, str(value)) for value in values]
    else:
        shown = [str(item) for item in labels]
        if len(shown) != len(values):
            raise ValueError('labels must be as many as values')
    to_label = dict(zip(values, shown))
    to_value = {}
    for value, text in zip(values, shown):
        to_value.setdefault(text, value)
    return to_label, to_value


class LabeledOptionMenu(AppOptionMenu):
    """(1.7.0, I18N.md 7) An ``AppOptionMenu`` that shows labels and stores values.

    The menu lists the labels; ``variable`` (when given) holds the stored *value* and is
    followed when somebody writes it; ``command`` is called with the value; ``get()`` returns
    the value and ``set()`` takes a value (a label is accepted too).  ``cget('values')`` gives the
    shown labels, ``stored_values`` the values.
    """

    def __init__(self, master, values, labels, variable=None, command=None, **kw):
        self.stored_values = list(values)
        self._to_label, self._to_value = _label_maps(self.stored_values, labels)
        self._value_variable = variable
        self._value_trace = None
        self._value = variable.get() if variable is not None else (self.stored_values[0] if self.stored_values else '')
        super().__init__(master, values=[self._to_label[value] for value in self.stored_values], command=command, **kw)
        self._show(self._value)
        if variable is not None:
            self._value_trace = variable.trace_add('write', self._value_written)

    def label_of(self, value):
        try:
            return self._to_label.get(value, str(value))
        except TypeError:
            return str(value)

    def _show(self, value):
        self._value = value
        self._current_value = self.label_of(value)
        self._text_label.configure(text=self._current_value)

    def _value_written(self, *_):
        try:
            self._show(self._value_variable.get())
        except tkinter.TclError:
            pass

    def _store(self, value):
        self._show(value)
        if self._value_variable is not None and self._value_variable.get() != value:
            self._value_variable.set(value)

    def _dropdown_callback(self, value: str):
        value = self._to_value.get(value, value)
        self._store(value)
        if self._command is not None:
            self._command(value)

    def set(self, value):
        try:
            if value not in self._to_label and value in self._to_value:
                value = self._to_value[value]
        except TypeError:
            pass
        self._store(value)

    def get(self):
        return self._value

    def cget(self, attribute_name: str):
        if attribute_name == 'variable':
            return self._value_variable
        return super().cget(attribute_name)

    def destroy(self):
        if self._value_variable is not None and self._value_trace is not None:
            try:
                self._value_variable.trace_remove('write', self._value_trace)
            except tkinter.TclError:
                pass
            self._value_trace = None
        super().destroy()


def option_menu(parent, values, labels=None, **kw):
    """Drop-down, 36 px high, with a fixed width (``width=``, default 140) and a chevron arrow.

    (1.7.0) With ``labels`` (a list parallel to ``values`` or a dict value -> label) the menu
    shows the labels and stores the values (``LabeledOptionMenu``); without, the 1.6.0 widget.
    """
    kw.setdefault('height', CONTROL_H)
    kw.setdefault('font', font('body'))
    kw.setdefault('dropdown_font', font('body'))
    kw.setdefault('dynamic_resizing', False)
    if labels is not None:
        return LabeledOptionMenu(parent, list(values), labels, **kw)
    return AppOptionMenu(parent, values=list(values), **kw)


def menu_width(parent, labels, size='md'):
    """(1.7.0) The width (logical px) an ``option_menu`` needs to show its widest label in full:
    the text, the label's 1 px border on each side, the left inset and the chevron area."""
    scale = _scaling(parent) or 1.0
    measure = _measure_font(parent, font('caption' if size == 'sm' else 'body'))
    labels = list(labels.values()) if isinstance(labels, dict) else list(labels)
    widest = max((measure.measure(str(text)) for text in labels), default=0)
    try:
        radius = ctk.ThemeManager.theme['CTkOptionMenu']['corner_radius']
    except (KeyError, TypeError):
        radius = RADIUS
    inset = max(radius, 3) + CONTROL_H + 3        # CTkOptionMenu's left padding and its chevron column
    return int(-(-(widest + 2) // scale)) + inset


class AppCheckBox(ctk.CTkCheckBox):
    """Checkbox with an 18 px box, a pale box while disabled and a soft hover while unchecked.

    ``CTkCheckBox`` keeps the full accent fill when it is disabled and fills an unchecked box
    with the dark hover colour, which reads as checked.
    """

    def __init__(self, master, text='', **kw):
        kw.setdefault('checkbox_width', 18)
        kw.setdefault('checkbox_height', 18)
        kw.setdefault('width', 0)           # as wide as box and text, not the stock minimum of 100
        kw.setdefault('font', font('body'))
        kw.setdefault('fg_color', ACCENT)
        kw.setdefault('hover_color', ACCENT_HOVER)
        kw.setdefault('border_color', CHECK_BORDER)
        kw.setdefault('text_color_disabled', FAINT)
        self._looks = (kw['fg_color'], kw['border_color'], kw['hover_color'])
        super().__init__(master, text=text, **kw)
        if self.cget('state') == 'disabled':
            self.configure(state='disabled')

    def configure(self, require_redraw=False, **kwargs):
        looks = getattr(self, '_looks', None)
        if looks is not None and 'state' in kwargs:
            off = kwargs['state'] == 'disabled'
            kwargs.setdefault('fg_color', ACCENT_DISABLED if off else looks[0])
            kwargs.setdefault('border_color', SCROLL if off else looks[1])
        super().configure(require_redraw=require_redraw, **kwargs)

    def _on_enter(self, event=0):
        checked = self.get() == self.cget('onvalue')
        hover = self._looks[2] if checked else ACCENT_SOFT
        if self.cget('hover_color') != hover:
            super().configure(hover_color=hover)
        super()._on_enter(event)


def checkbox(parent, text, **kw):
    return AppCheckBox(parent, text=text, **kw)


class AppSwitch(ctk.CTkSwitch):
    """Switch with a coloured knob: ACCENT when on, grey when off, pale while disabled.

    A white knob disappears on a white card. The knob follows the state however it changes:
    a click, ``select()`` / ``deselect()`` or a write to the variable all end in ``set()``.
    """

    # (on, disabled) -> (knob, knob under the pointer)
    _KNOBS = {(True, False): (ACCENT, ACCENT_HOVER), (False, False): (SWITCH_KNOB_OFF, MUTED),
              (True, True): (SWITCH_KNOB_DISABLED, SWITCH_KNOB_DISABLED), (False, True): (CHECK_BORDER, CHECK_BORDER)}

    def __init__(self, master, text='', variable=None, **kw):
        kw.setdefault('switch_width', 38)
        kw.setdefault('switch_height', 20)
        # (final polish) No border: CustomTkinter draws the knob over the full height, the track inside the border,
        # so with a border the knob stood out of the track on its own side and an off switch ended 3 px left of
        # an on one.  Without it the track is as high as the knob and both states fill the same box.
        kw.setdefault('border_width', 0)
        kw.setdefault('font', font('body'))
        kw.setdefault('text_color_disabled', FAINT)
        self._bare = not text
        # As wide as track and text, not the stock minimum of 100.
        kw.setdefault('width', kw['switch_width'] if self._bare else 0)
        self._knob = None
        super().__init__(master, text=text, variable=variable, **kw)
        self._trim()
        self._knob = ()
        self._sync_knob()

    def _trim(self):
        if self._bare:      # no text: drop the gap reserved for it, so the track ends at the widget edge
            self.grid_columnconfigure(1, minsize=0)

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        self._trim()

    def _sync_knob(self):
        if self._knob is None:
            return
        look = (self.get() == self.cget('onvalue'), self.cget('state') == 'disabled')
        if look != self._knob:
            self._knob = look
            knob, hover = self._KNOBS[look]
            super().configure(button_color=knob, button_hover_color=hover)

    def set(self, state, from_variable_callback=False):
        super().set(state, from_variable_callback)
        self._sync_knob()

    def configure(self, require_redraw=False, **kwargs):
        super().configure(require_redraw=require_redraw, **kwargs)
        if getattr(self, '_knob', None) is not None and {'variable', 'onvalue', 'offvalue', 'state'} & set(kwargs):
            self._sync_knob()


def switch(parent, text='', variable=None, **kw):
    return AppSwitch(parent, text=text, variable=variable, **kw)


class AppTextbox(ctk.CTkTextbox):
    """(2.0.0 S4) Multi-line input with a placeholder: a faint label laid over the box while it is empty,
    focused or not (``placeholder_label``, None without a placeholder).  ``set_dimmed(True)`` shows the box
    as taken by another activity: disabled, faint text on SURFACE, no placeholder."""

    def __init__(self, master, placeholder='', undo=False, **kw):
        kw.setdefault('font', font('body'))
        super().__init__(master, undo=undo, **kw)
        self.dimmed = False
        self.placeholder_label = None
        self._hint_shown = False
        if not placeholder:
            return
        self.placeholder_label = ctk.CTkLabel(self, text=placeholder, font=kw['font'], text_color=FAINT,
                                              fg_color=self.cget('fg_color'), anchor='w', cursor='xterm')
        self.placeholder_label.bind('<Button-1>', self._hint_clicked)
        self.bind('<KeyRelease>', lambda _event: self.sync_placeholder())
        self.bind('<<Modified>>', self._hint_modified)
        self.sync_placeholder()

    def _hint_clicked(self, _event=None):
        self.focus_set()
        return 'break'

    def _hint_modified(self, _event=None):
        try:
            self.edit_modified(False)
        except tkinter.TclError:
            pass
        self.sync_placeholder()

    def sync_placeholder(self):
        """Show the placeholder while the box is empty and not dimmed (call it after code rewrote the text;
        insert() and delete() do)."""
        hint = self.placeholder_label
        if hint is None:
            return
        try:
            empty = not self.dimmed and self.get('1.0', 'end-1c') == ''
            if empty and not self._hint_shown:
                hint.place(x=10, y=6)
            elif not empty and self._hint_shown:
                hint.place_forget()
            self._hint_shown = empty
        except tkinter.TclError:
            pass

    def set_dimmed(self, dimmed):
        """Taken by another activity (True) or free again (False); only a change redraws."""
        dimmed = bool(dimmed)
        if dimmed == self.dimmed:
            return
        self.dimmed = dimmed
        fill = SURFACE if dimmed else SURFACE_ALT
        self.configure(state='disabled' if dimmed else 'normal', text_color=FAINT if dimmed else INK, fg_color=fill)
        if self.placeholder_label is not None:
            self.placeholder_label.configure(fg_color=fill)
        self.sync_placeholder()

    def insert(self, index, text, tags=None):
        result = super().insert(index, text, tags)
        self.sync_placeholder()
        return result

    def delete(self, index1, index2=None):
        result = super().delete(index1, index2)
        self.sync_placeholder()
        return result


def textbox(parent, placeholder='', undo=False, **kw):
    """A multi-line input (``AppTextbox``); ``placeholder`` is shown while it is empty, ``undo=True`` turns on
    Ctrl+Z (Tk's undo stack, also for text removed by code)."""
    return AppTextbox(parent, placeholder=placeholder, undo=undo, **kw)


# ---------------------------------------------------------------- scrolling
_AREA_CHILD = 'ScrollAreaChild'        # bind tag given to the direct children of a ScrollArea
_child_watched = weakref.WeakSet()     # Tk roots that have the <Destroy> binding of that tag


def _child_destroyed(event):
    parent = getattr(event.widget, 'master', None)
    if isinstance(parent, ScrollArea):
        parent._bar_later()


class ScrollArea(ctk.CTkScrollableFrame):
    """Scrollable frame: transparent, readable wheel speed, scrollbar only when there is something to scroll.

    The stock frame moves 20 physical pixels per wheel notch at any scaling and always
    draws its scrollbar. Here one notch moves ``WHEEL_STEP`` logical pixels and the bar
    (with its gutter) is removed while the whole content is visible.
    """

    def __init__(self, master, **kw):
        kw.setdefault('fg_color', 'transparent')
        kw.setdefault('corner_radius', 0)
        self._bar_shown = True          # whether the bar is wanted; bar_visible reports the grid state itself
        self._bar_job = None
        self._closing = False
        self._tagged = weakref.WeakSet()
        super().__init__(master, **kw)
        self._parent_canvas.bind('<Configure>', self._bar_later, add='+')
        self.bind('<Configure>', self._bar_later, add='+')
        # Tk tells a frame nothing when it loses a child, and a frame that loses its last
        # child even keeps its size. The children carry a bind tag that reports their end.
        root = _tk_root(self)
        if root not in _child_watched:
            _child_watched.add(root)
            root.bind_class(_AREA_CHILD, '<Destroy>', _child_destroyed)
        self._bar_later()

    @property
    def canvas(self):
        """The Tk canvas that scrolls the content (its height is the viewport height)."""
        return self._parent_canvas

    @property
    def bar_visible(self):
        """True while the content does not fit and the scrollbar is shown (read from the grid, not remembered)."""
        try:
            return bool(self._scrollbar.winfo_manager())
        except tkinter.TclError:
            return False

    def _view(self):
        canvas = self._parent_canvas
        return canvas.xview() if self._orientation == 'horizontal' else canvas.yview()

    def _bar_later(self, event=None):
        if self._bar_job is None and not self._closing:
            try:
                self._bar_job = self.after_idle(self._bar_check)
            except tkinter.TclError:
                pass

    def _bar_check(self):
        self._bar_job = None
        try:
            children = self.winfo_children()
            for child in children:
                if child not in self._tagged:
                    self._tagged.add(child)
                    child.bindtags(child.bindtags() + (_AREA_CHILD,))
            if not children:
                # Tk 8.6 leaves a frame at the size of the children it has lost, and the canvas
                # learns a new size only when it is drawn again. Ask for an empty frame and
                # give the canvas its extent now.
                side = 'width' if self._orientation == 'horizontal' else 'height'
                if (self.winfo_reqwidth() if side == 'width' else self.winfo_reqheight()) > 1:
                    tkinter.Frame.configure(self, **{side: 1})
                    self._parent_canvas.configure(scrollregion=self._parent_canvas.bbox('all'))
            first, last = self._view()
            needed = not (first <= 0.0 and last >= 1.0)
            if needed:
                # Nothing to scroll is nothing to scroll: a content that overhangs the viewport by a
                # pixel or two of rounding (or of the inner frame's border) must not bring the bar.
                horizontal = self._orientation == 'horizontal'
                view = self._parent_canvas.winfo_width() if horizontal else self._parent_canvas.winfo_height()
                content = self.winfo_reqwidth() if horizontal else self.winfo_reqheight()
                if view > 1 and content - view <= max(2, round(2 * _scaling(self))):
                    needed = False
                    if first > 0.0:
                        self._parent_canvas.xview_moveto(0) if horizontal else self._parent_canvas.yview_moveto(0)
            self._bar_shown = needed
            # Compared with the grid itself: a remembered state can be wrong, the grid cannot.
            if needed != bool(self._scrollbar.winfo_manager()):
                if needed:
                    self._create_grid()             # the stock call grids the bar at the current scaling
                else:
                    self._bar_hide()
        except tkinter.TclError:
            pass

    def _bar_hide(self):
        # customtkinter's grid_forget, not the raw grid_remove: a CTk widget repeats its last
        # grid() call after every scaling change (another monitor, another Windows scaling).
        # A bar taken out without customtkinter knowing would be back on screen after that.
        self._scrollbar.grid_forget()

    def _create_grid(self):
        super()._create_grid()
        if not getattr(self, '_bar_shown', True):   # the stock method has just gridded the bar again
            self._bar_hide()

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        self._bar_later()                           # the content may fit, or no longer fit, at the new size

    def destroy(self):
        self._closing = True            # the children that go now must not schedule another check
        cancel_after(self, self._bar_job)
        self._bar_job = None
        super().destroy()

    def _wheel_target(self, widget):
        """True when a wheel event on ``widget`` should scroll this area."""
        while widget is not None and not isinstance(widget, str):
            if widget is self._parent_canvas:
                return True
            if isinstance(widget, (ttk.Treeview, tkinter.Text, tkinter.Listbox, ctk.CTkScrollbar, ctk.CTkSlider)):
                return False                         # these scroll themselves
            if isinstance(widget, ctk.CTkScrollableFrame):
                return widget is self
            widget = getattr(widget, 'master', None)
        return False

    def _mouse_wheel_all(self, event):
        if not sys.platform.startswith('win') or self._shift_pressed:
            return super()._mouse_wheel_all(event)
        try:
            if not self._wheel_target(event.widget) or self._parent_canvas.yview() == (0.0, 1.0):
                return
            # yscrollincrement is 1 on Windows, so one unit is one physical pixel.
            pixels = round(event.delta / 120 * WHEEL_STEP * _scaling(self))
            if pixels == 0 and event.delta:
                pixels = 1 if event.delta > 0 else -1
            self._parent_canvas.yview('scroll', -pixels, 'units')
        except tkinter.TclError:
            pass


def scroll_area(parent, **kw):
    return ScrollArea(parent, **kw)


# ---------------------------------------------------------------- keyboard focus of stacked frames
# (final-fix-ui, polish-visual-1 / polish-visual-2) Pages and tab frames lie on top of each other
# and are shown with tkraise(): a covered frame stays mapped, which keeps a switch cheap (no
# window of a page is unmapped and mapped again), but Tk's Tab traversal (tk_focusNext and
# tk_focusPrev, through ::tk::FocusOK) would still walk into it.  ::tk::FocusOK is wrapped once
# per interpreter: a widget inside a frame registered as hidden is never a Tab stop.  The
# original procedure decides everything else (state, -takefocus, viewable, key bindings).
_FOCUS_GATE = r'''
namespace eval ::ytdlp {}
if {[info commands ::ytdlp::FocusOK] eq ""} {
    if {[info procs ::tk::FocusOK] eq ""} {auto_load ::tk::FocusOK}
    if {[info procs ::tk::FocusOK] ne ""} {
        rename ::tk::FocusOK ::ytdlp::FocusOK
        proc ::tk::FocusOK {w} {
            if {[array exists ::ytdlp::hidden]} {
                foreach {key frames} [array get ::ytdlp::hidden] {
                    foreach frame $frames {
                        if {$w eq $frame || [string first "$frame." $w] == 0} {return 0}
                    }
                }
            }
            return [::ytdlp::FocusOK $w]
        }
    }
}
'''


def hide_from_focus(widget, key, frames):
    """Keep Tab traversal out of ``frames`` (group ``key``; a later call replaces the group's frames).

    Returns False when the gate could not be installed (Tk without ::tk::FocusOK)."""
    try:
        widget.tk.eval(_FOCUS_GATE)
        widget.tk.call('set', f'::ytdlp::hidden({key})', tuple(str(frame) for frame in frames))
        return bool(widget.tk.call('info', 'commands', '::ytdlp::FocusOK'))
    except tkinter.TclError:
        return False


def focus_inside(widget, frame):
    """Whether the keyboard focus of ``widget``'s toplevel (shown or last) lies inside ``frame``."""
    try:
        focused = str(widget.tk.call('focus', '-lastfor', widget._w))
    except tkinter.TclError:
        return False
    return focused == str(frame) or focused.startswith(str(frame) + '.')


# ---------------------------------------------------------------- tabs
class Tabs(ctk.CTkFrame):
    """A segmented tab bar and one frame per tab, on the page itself (no card, no border).

    Same calls as ``CTkTabview`` (``add``, ``tab``, ``set``, ``get``).  The bar (``bar``, CONTROL_H_SM
    high) stands above the tab frames, or, with ``bar_parent`` (2.0.0 S4: the library page passes
    ``page.header.left``), is packed into that container from the left; the tab frames stay in the
    Tabs frame either way, in row 1, without inset.

    The tab frames are stacked and raised (a switch unmaps nothing).  The frames of the other
    tabs take no Tab focus (``hide_from_focus``), and a keyboard focus left inside a tab that
    is no longer shown moves to the new one: to ``focus(name)``'s widget when that callback is
    given and returns one, else to the tab frame itself (final-fix-ui, polish-function-3).
    """

    def __init__(self, master, names, command=None, focus=None, labels=None, bar_parent=None, **kw):
        kw.setdefault('fg_color', 'transparent')
        kw.setdefault('corner_radius', 0)
        kw.setdefault('border_width', 0)
        super().__init__(master, **kw)
        self._command = command
        self._focus = focus
        self._frames = {}
        self._current = ''
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        names = list(names)
        # (1.7.0) ``labels`` (dict id -> label, or a list parallel to names): the bar shows the
        # labels, every call keeps using the ids.
        self._labels = dict(labels) if isinstance(labels, dict) else dict(zip(names, labels)) if labels else {}
        self.bar = self._bar = _Segments(
            self if bar_parent is None else bar_parent, values=[self._tab_label(name) for name in names],
            height=CONTROL_H_SM, corner_radius=RADIUS, font=font('body'), command=self._clicked)
        if bar_parent is None:
            self._bar.grid(row=0, column=0, sticky='w', pady=(0, GAP))
        else:
            self._bar.pack(side='left')
        for name in names:
            self._make(name)
        if names:
            self.set(names[0])

    def _make(self, name):
        frame = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0, width=0, height=0)
        frame.grid(row=1, column=0, sticky='nsew')
        self._frames[name] = frame
        current = self._frames.get(self._current)
        if current is not None:
            current.tkraise()
            self._gate()
        return frame

    def _tab_label(self, name):
        """(1.7.0) What the bar shows for tab id ``name``."""
        return self._labels.get(name, name)

    def _tab_id(self, shown):
        """(1.7.0) The tab id of a bar value (a label; an id is accepted as it is)."""
        if shown in self._frames:
            return shown
        return next((name for name in self._frames if self._tab_label(name) == shown), shown)

    def _clicked(self, shown):
        name = self._tab_id(shown)
        self.set(name)
        if self._command is not None:
            self._command(name)

    def add(self, name):
        """The frame of tab ``name``; the tab is created when it does not exist yet."""
        if name in self._frames:
            return self._frames[name]
        self._bar.insert(len(self._frames), self._tab_label(name))
        frame = self._make(name)
        if not self._current:
            self.set(name)
        return frame

    def tab(self, name):
        try:
            return self._frames[name]
        except KeyError:
            raise ValueError(f'no tab named {name!r}') from None

    def set(self, name):
        """Show tab ``name`` and mark it in the bar."""
        frame = self.tab(name)
        self._current = name
        frame.tkraise()
        self._bar.set(self._tab_label(name))
        self._gate()
        others = [other for key, other in self._frames.items() if key != name]
        if any(focus_inside(self, other) for other in others):
            target = None
            if self._focus is not None:
                try:
                    target = self._focus(name)
                except Exception:
                    target = None
            try:
                (target or frame).focus_set()
            except tkinter.TclError:
                pass

    def _gate(self):
        """The frames of the tabs that are not shown take no Tab focus."""
        hide_from_focus(self, f'tabs{self._w}', [frame for key, frame in self._frames.items() if key != self._current])

    def get(self):
        return self._current


def tabs(parent, names, **kw):
    return Tabs(parent, names, **kw)


class _Segments(ctk.CTkSegmentedButton):
    """The segmented bar of ``Tabs`` and ``segmented``: the chosen segment's text is INK, the others MUTED
    (the raised segment and its darker text say the same thing), and every segment keeps an even physical width.

    (final fix round 1, zh-1; 2.0.0 S4 visual fix for the tab bar too) A segment that grows to its text can get
    an odd physical width; its rounded end is then drawn one px short and the bar colour shows as a thin line
    beside the last segment's corners.  Every segment, also one added later, is kept at an even width."""

    def _create_button(self, index, value):
        button = super()._create_button(index, value)
        button.configure(text_color=MUTED)
        tkinter.Misc.bind(button, '<Configure>', lambda _event, b=button: self._even_later(b), add='+')
        return button

    def _unselect_button_by_value(self, value):
        super()._unselect_button_by_value(value)
        if value in self._buttons_dict:
            self._buttons_dict[value].configure(text_color=MUTED)

    def _select_button_by_value(self, value):
        super()._select_button_by_value(value)
        self._buttons_dict[value].configure(text_color=self._sb_text_color)

    def _even_later(self, button):
        try:
            if button.winfo_width() % 2:
                button.after_idle(self._make_even, button)
        except tkinter.TclError:
            pass

    @staticmethod
    def _make_even(button):
        try:
            width = button.winfo_width()
            if width > 1 and width % 2:
                button.configure(width=(width + 1) / (_scaling(button) or 1.0))
        except (tkinter.TclError, ValueError):
            pass


class LabeledSegmentedButton(_Segments):
    """(1.7.0, I18N.md 7) A ``_Segments`` bar that shows labels and stores values: ``command``
    gets the value, ``get()`` returns it and ``set()`` takes a value (a label is accepted too).
    ``cget('values')`` gives the shown labels, ``stored_values`` the values."""

    def __init__(self, master, values, labels, command=None, **kw):
        self.stored_values = list(values)
        self._to_label, self._to_value = _label_maps(self.stored_values, labels)
        self._value_command = command
        super().__init__(master, values=[self._to_label[value] for value in self.stored_values],
                         command=self._chosen, **kw)

    def label_of(self, value):
        try:
            return self._to_label.get(value, str(value))
        except TypeError:
            return str(value)

    def _chosen(self, shown):
        if self._value_command is not None:
            self._value_command(self._to_value.get(shown, shown))

    def set(self, value, from_variable_callback=False, from_button_callback=False):
        if from_button_callback or (value not in self._to_label and value in self._to_value):
            shown = value            # a click hands over the label of the segment
        else:
            shown = self.label_of(value)
        super().set(shown, from_variable_callback, from_button_callback)

    def get(self):
        shown = super().get()
        return self._to_value.get(shown, shown)


def segmented(parent, values, labels, command=None, **kw):
    """(1.7.0) A segmented choice (the appearance and language rows) that shows ``labels`` (a list
    parallel to ``values`` or a dict value -> label) and hands ``command`` the stored value."""
    kw.setdefault('height', 32)
    kw.setdefault('font', font('body'))
    return LabeledSegmentedButton(parent, values, labels, command=command, **kw)


# ---------------------------------------------------------------- tables
def _configure_ttk(root):
    scale = _scaling(root)
    style = ttk.Style(root)
    if style.theme_use() != 'clam':
        style.theme_use('clam')
    text_font = (FAMILY, -round(TABLE_FONT * scale))
    for name in ('App.Treeview', 'Collect.Treeview'):
        style.configure(name, font=text_font, rowheight=round(TABLE_ROW_H * scale), background=resolve(SURFACE),
                        fieldbackground=resolve(SURFACE), foreground=resolve(INK), borderwidth=0, relief='flat')
        style.layout(name, [('Treeview.treearea', {'sticky': 'nswe'})])
        style.map(name, background=[('selected', resolve(SELECT))], foreground=[('selected', resolve(INK))])
        style.configure(name + '.Heading', font=text_font, background=resolve(SURFACE_ALT),
                        foreground=resolve(MUTED), relief='flat', borderwidth=0,
                        padding=(round(10 * scale), round(7 * scale)))
        style.map(name + '.Heading', background=[('active', resolve(IDLE_SOFT))], relief=[('active', 'flat')])
        # Lines the cell text up with the heading text.
        style.configure(name + '.Cell', padding=(round(10 * scale) - 4, 0))


def apply_ttk_styles(root):
    """Configure the ttk theme and the table styles ``App.Treeview`` and ``Collect.Treeview`` for ``root``.

    Fonts and row height are physical pixels computed from the window scaling, so the table
    text matches the customtkinter text at any DPI. The first call for a root also arranges
    for the styles (and the column widths of every table) to follow scaling changes, and for
    the colours to follow the appearance mode.
    """
    root = _tk_root(root)
    _configure_ttk(root)
    if root in _styled_roots:
        return
    _styled_roots.add(root)
    _hook_appearance(root)
    anchor = weakref.ref(root)

    def rescale(widget_scaling=None, window_scaling=None):
        target = anchor()
        if target is None:
            return
        try:
            if not target.winfo_exists():
                return
            _configure_ttk(target)
            for tree in list(_tables):
                if _tk_root(tree) is target and tree.winfo_exists():
                    tree.rescale()
        except tkinter.TclError:
            pass

    ctk.ScalingTracker.add_widget(rescale, root)


class AppTable(ttk.Treeview):
    """Flat ``ttk.Treeview`` in a rounded frame, with stripes, an empty hint and an auto-hiding scrollbar.

    Grid or pack ``table.container``, not the table itself. Fill it with ``fill(rows)``; code
    that inserts rows on its own calls ``restripe()`` afterwards. ``empty_label`` is the hint
    shown while there are no rows.
    """

    def __init__(self, master, columns, selectmode='browse', empty='', height=None):
        if _tk_root(master) not in _styled_roots:
            apply_ttk_styles(master.winfo_toplevel())
        self.container = ctk.CTkFrame(master, fg_color=SURFACE, corner_radius=RADIUS, border_width=1,
                                      border_color=LINE)
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(0, weight=1)
        self._widths = {}
        keys = [column[0] for column in columns]
        # Tk's default of 10 rows would make a page ask for more height than a small window
        # has; the table is stretched by its grid cell anyway.
        super().__init__(self.container, columns=keys, show='headings', style='App.Treeview',
                         selectmode=selectmode, height=4 if height is None else height)
        for column in columns:
            key, title, width = column[0], column[1], column[2]
            stretch = bool(column[3]) if len(column) > 3 else True
            self._widths[key] = (width, min(56, width))
            self.heading(key, text=title, anchor='w')
            self.column(key, stretch=stretch, anchor='w')
        self._bar = ctk.CTkScrollbar(self.container, command=self.yview, width=14)
        self.configure(yscrollcommand=self._scrolled)
        self._empty = empty or ''
        # MUTED like the caption of empty_state(): the hint carries instructions and must be
        # readable (5.68:1). FAINT on SURFACE is 2.46:1 and stays reserved for placeholders.
        self.empty_label = ctk.CTkLabel(self, text=self._empty, font=font('caption'), text_color=MUTED,
                                        fg_color=SURFACE, bg_color=SURFACE)
        self._hint_shown = False
        self.grid(row=0, column=0, sticky='nsew')
        self.rescale()
        self.recolor()
        _tables.add(self)
        self._toggle_hint()

    def rescale(self):
        """Apply the logical column widths, the inset and the scrollbar state at the current scaling."""
        scale = _scaling(self)
        for key, (width, minimum) in self._widths.items():
            self.column(key, width=round(width * scale), minwidth=round(minimum * scale))
        self._bar_sync()
        self._inset()
        if getattr(self, '_cut_full', None) is not None:
            self._recut_later()

    def _inset(self):
        # Keeps the square corners of the tree inside the rounded border of the container.
        # On the right the scrollbar provides the distance while it is shown.
        inset = round(4 * _scaling(self))
        self.grid_configure(padx=(inset, 0 if self._bar.winfo_manager() else inset), pady=inset)

    def recolor(self):
        """Apply the colours that plain Tk cannot take as a (light, dark) pair."""
        self.tag_configure('odd', background=resolve(STRIPE))

    def _scrolled(self, first, last):
        self._bar.set(first, last)
        self._bar_sync()

    def _bar_sync(self):
        """Show the scrollbar while the rows do not fit, take it out while they do."""
        # The view is read back from the bar, never taken from the arguments of _scrolled():
        # set() redraws the bar and lets Tk run its pending idle tasks, during which Tk can
        # report a newer view through _scrolled() again. The bar then holds that newer report,
        # and the outer call must not undo it with its older one.
        first, last = self._bar.get()
        needed = not (first <= 0.0 and last >= 1.0)
        # Compared with the grid itself: a remembered state can be wrong, the grid cannot.
        if needed == bool(self._bar.winfo_manager()):
            return
        if needed:
            self._bar.grid(row=0, column=1, sticky='ns', padx=(0, 2), pady=4)
        else:
            # customtkinter's grid_forget, not the raw grid_remove: a CTk widget repeats its last
            # grid() call after every scaling change, and that would put the bar back.
            self._bar.grid_forget()
        self._inset()

    def _toggle_hint(self):
        show = bool(self._empty) and not self.get_children()
        if show != self._hint_shown:
            self._hint_shown = show
            if show:
                self.empty_label.place(relx=0.5, rely=0.55, anchor='center')
            else:
                self.empty_label.place_forget()

    def fill(self, rows):
        """Replace the content with ``rows``, an iterable of ``(iid, values)``.

        Keeps the given iids (None lets Tk choose one), stripes the rows, shows the empty hint
        when there are none and restores the selection of every iid that still exists.
        """
        selected = self.selection()
        focused = self.focus()
        self.delete(*self.get_children())
        cut = getattr(self, '_cut_full', None)      # set by ellipsize()
        if cut is not None:
            rows = [(iid, tuple(values)) for iid, values in rows]
            cut.clear()
            self._cut_shown = {}
            self._cut_common = self._common_parts(values for _iid, values in rows)
            rooms = self._cut_rooms()
            if rooms is None:
                self._cut_key = None        # cut them once the table is shown
        for number, (iid, values) in enumerate(rows):
            options = {'values': tuple(values), 'tags': ('odd',) if number % 2 else ()}
            if cut is not None:
                full = options['values']
                if rooms is not None:
                    options['values'] = self._cut_values(full, rooms)
            if iid is not None:
                options['iid'] = iid
            iid = self.insert('', 'end', **options)
            if cut is not None:
                cut[iid] = full
                self._cut_shown[iid] = options['values']
        keep = [iid for iid in selected if self.exists(iid)]
        if keep:
            self.selection_set(keep)
        if focused and self.exists(focused):
            self.focus(focused)
        self._toggle_hint()

    def restripe(self):
        """Re-apply the stripe tag (other tags and all iids stay) and the empty hint."""
        for number, iid in enumerate(self.get_children()):
            tags = [tag for tag in self.tk.splitlist(self.item(iid, 'tags')) if tag != 'odd']
            if number % 2:
                tags.append('odd')
            self.item(iid, tags=tags)
        self._toggle_hint()

    def set_empty(self, text):
        """Change the hint shown while the table has no rows ('' shows none)."""
        self._empty = text or ''
        self.empty_label.configure(text=self._empty)
        self._toggle_hint()

    # ------------------------------------------------------------ cut cells (1.6.0 WP5 fix round 1)
    def ellipsize(self, middle=()):
        """From now on show every cell cut to its column width with '…' instead of clipped mid-glyph.

        Columns named in ``middle`` (links) keep their start and their end and lose the middle,
        so rows that share a long prefix stay distinguishable. The cells are cut again when the
        table or a column changes width. Only rows given to ``fill()`` are cut; ``full_values(iid)``
        returns the uncut values. As with ``fit``, a table that is not viewable (a withdrawn
        window) shows the full values until it is mapped.
        """
        self._cut_middle = frozenset(middle)
        if getattr(self, '_cut_full', None) is None:
            self._cut_full = {iid: tuple(self.item(iid, 'values')) for iid in self.get_children()}
            self._cut_shown = {}
            self._cut_job = None
            self._cut_key = None
            for sequence in ('<Configure>', '<Map>', '<ButtonRelease-1>', '<B1-Motion>'):
                self.bind(sequence, self._recut_later, add='+')
        self._cut_common = self._common_parts(self._cut_full.values())
        self._cut_key = None
        self._recut_later()

    def _common_parts(self, rows):
        """{middle column: (characters every row starts with, characters every row ends with)}.

        Links of one page often share both a long start and a long end; the cut then keeps the
        part where they differ (see ``_cut_cell``)."""
        keys = list(self['columns'])
        texts = {key: [] for key in self._cut_middle if key in keys}
        for values in rows:
            for key in texts:
                position = keys.index(key)
                texts[key].append(str(values[position]) if position < len(values) else '')
        common = {}
        for key, column in texts.items():
            if len(column) < 2:
                continue
            prefix = len(os.path.commonprefix(column))
            suffix = len(os.path.commonprefix([text[::-1] for text in column]))
            common[key] = (prefix, suffix)
        return common

    def full_values(self, iid):
        """The values of row ``iid`` as given to ``fill()``, uncut."""
        cut = getattr(self, '_cut_full', None)
        if cut is not None and iid in cut:
            return cut[iid]
        return tuple(self.item(iid, 'values'))

    def _cut_rooms(self):
        """{column: physical px its text may use}, or None while the table is not viewable."""
        try:
            if self.winfo_width() <= 1 or not self.winfo_viewable():
                return None
            spare = cell_spare(_scaling(self))
            return {key: int(self.column(key, 'width')) - spare for key in self['columns']}
        except tkinter.TclError:
            return None

    def _cut_values(self, values, rooms):
        widths = _width_lookup(_measure_font(self, (FAMILY, TABLE_FONT)))
        shown = []
        for key, value in zip(self['columns'], values):
            text = str(value)
            room = rooms.get(key)
            if room is not None:
                text = _cut_cell(text.replace('\n', ' '), widths, room, key in self._cut_middle,
                                 self._cut_common.get(key))
            shown.append(text)
        return tuple(shown) + tuple(values[len(shown):])

    def _recut_later(self, event=None):
        if getattr(self, '_cut_job', None) is None:
            try:
                self._cut_job = self.after_idle(self._recut)
            except tkinter.TclError:
                pass

    def _recut(self):
        self._cut_job = None
        try:
            rooms = self._cut_rooms()
            if rooms is None:
                return
            key = (tuple(sorted(rooms.items())), _scaling(self), self._cut_middle)
            if key == self._cut_key:
                return
            self._cut_key = key
            for iid in self.get_children():
                full = self._cut_full.get(iid)
                if full is None:
                    continue
                shown = self._cut_values(full, rooms)
                if shown != self._cut_shown.get(iid):
                    self.item(iid, values=shown)
                    self._cut_shown[iid] = shown
        except tkinter.TclError:
            pass        # the table is gone

    def destroy(self):
        cancel_after(self, getattr(self, '_cut_job', None))
        self._cut_job = None
        super().destroy()


def _take(sizes, budget, start=0, stop=None):
    """How many characters from ``start`` on fit into ``budget`` px."""
    stop = len(sizes) if stop is None else stop
    count, used = 0, 0
    while start + count < stop and used + sizes[start + count] <= budget:
        used += sizes[start + count]
        count += 1
    return count, used


def cell_spare(scale):
    """Physical px a table cell keeps beside its text (AppTable's cut): the cell padding of the
    style on both sides, and a little air before the next column."""
    return 2 * max(0, round(10 * scale) - 4) + round(4 * scale)


def _cut_cell(text, width_of, room, middle=False, common=None):
    """``text`` cut to ``room`` px with '…' at the end, or in the middle (start and end kept).

    ``common`` = (prefix, suffix): how many characters every row of the column shares at its start
    and its end. When the rows share a long end, a middle cut would show the same end on every
    row; the cut then keeps the start of the link and the path segment where the rows differ,
    cut at its end (``https://host/…/episode-012-the-long…``). Widths are summed per character
    (cached), so a table of a few thousand rows is cut quickly.
    """
    sizes = [width_of(char) for char in text]
    if sum(sizes) <= room:
        return text
    ellipsis = width_of('…')
    budget = room - ellipsis
    if budget <= 0:
        return '…'
    if not middle:
        head, _used = _take(sizes, budget)
        return text[:head].rstrip() + '…'
    prefix, suffix = common or (0, 0)
    suffix = min(suffix, max(0, len(text) - prefix))
    if suffix and sum(sizes[len(text) - suffix:]) > budget / 4 and prefix < len(text) - suffix:
        # Same long end on every row: show the start and the segment holding the first difference.
        start = text.rfind('/', 0, prefix + 1)       # the '/' in front of that segment stays visible
        if start <= 0:
            start = prefix
        host = text.find('/', text.find('//') + 2) + 1 if '//' in text else 0
        head, used = _take(sizes, min(budget * 0.4, sum(sizes[:start])), 0, min(start, host or start))
        if head >= start:
            body, rest = text[:start], budget + ellipsis - used
        else:
            body, rest = text[:head].rstrip('.') + '…', budget - used
        tail, _used = _take(sizes, rest, start)
        if start + tail < len(text):
            tail, _used = _take(sizes, rest - ellipsis, start)
            return body + text[start:start + tail] + '…'
        return body + text[start:]
    # The end (the last path segment of a link) gets up to 60 % of the room, at least half.
    cut = max(text.rstrip('/').rfind('/'), 0)
    tail_budget = max(budget / 2, min(sum(sizes[cut:]), budget * 0.6))
    tail, used = 0, 0
    while tail < len(text) and used + sizes[len(text) - 1 - tail] <= tail_budget:
        used += sizes[len(text) - 1 - tail]
        tail += 1
    head, _used = _take(sizes, budget - used, 0, len(text) - tail)
    return text[:head].rstrip('.') + '…' + (text[len(text) - tail:] if tail else '')


def table(parent, columns, selectmode='browse', empty='', height=None):
    """A table. ``columns``: ``(key, title, width)`` or ``(key, title, width, stretch)``, widths in logical px.

    Returns the ``ttk.Treeview`` (an ``AppTable``); grid or pack its ``container``, ``sticky='nsew'``
    in a weighted row. ``height`` is the number of rows the table asks for (default 4).
    """
    return AppTable(parent, columns, selectmode=selectmode, empty=empty, height=height)


# ---------------------------------------------------------------- composed pieces
def empty_state(parent, text, icon_code=None, action=None):
    """Transparent frame with a faint icon over a muted caption, centred in whatever space it gets.

    ``action`` = (label, command) adds one soft button below the
    caption (2.0.0 S4: an empty view says what to do next).
    Attributes: ``hint_label``, ``icon_label`` (None without an icon), ``action_button`` (None without an action).
    """
    frame = ctk.CTkFrame(parent, fg_color='transparent', corner_radius=0, width=0, height=0)
    center = ctk.CTkFrame(frame, fg_color='transparent', corner_radius=0, width=0, height=0)
    center.pack(expand=True)
    frame.icon_label = None
    picture = icon(icon_code, 'faint', 28) if icon_code is not None else None
    if picture is not None:
        frame.icon_label = ctk.CTkLabel(center, text='', image=picture, width=32, height=32)
        frame.icon_label.pack(pady=(0, 6))
    frame.hint_label = label(center, text, role='caption', text_color=MUTED)
    frame.hint_label.pack()
    frame.action_button = None
    if action is not None:
        caption, command = action
        frame.action_button = button(center, caption, command, kind='soft', width=fitted_width(center, caption))
        frame.action_button.pack(pady=(GAP, 0))
    return frame


def section_label(parent, text):
    """(2.0.0 S4) The small muted name of a group of rows (settings page); the caller grids it with SECTION
    above (0 for the first group) and INLINE below."""
    return label(parent, text, role='caption', text_color=MUTED, anchor='w')


def set_enabled(widget, usable):
    """Set ``widget`` normal or disabled, only when that changes (the hooks that call it run on every set_busy)."""
    state = 'normal' if usable else 'disabled'
    if widget.cget('state') != state:
        widget.configure(state=state)


def show(widget, visible):
    """(2.0.0 S4) grid() or grid_remove() ``widget`` only when that changes anything (a widget gridded once
    remembers its place).  True when it changed.  A ScrollArea is shown through its outer frame (the inner
    one always sits in the canvas)."""
    widget = getattr(widget, '_parent_frame', widget)
    shown = bool(widget.winfo_manager())
    if bool(visible) == shown:
        return False
    if visible:
        widget.grid()
    else:
        widget.grid_remove()
    return True


# ---------------------------------------------------------------- menus (2.0.0 S4, 00-system 3.1)
SEPARATOR = None        # a line between two groups of a menu


@dataclass(frozen=True)
class MenuItem:
    """One entry of a menu.  ``checked`` True / False makes it a choice (the current one marked; a submenu
    of choices such as Schedule ▸); ``children`` makes it a submenu; ``danger`` paints it red (last group)."""
    label: str
    command: object = None
    enabled: bool = True
    checked: object = None
    children: tuple = ()
    danger: bool = False


_open_menus = []        # the one menu that is up (popup destroys the one before)


def menu_items(items):
    """The entries of ``items``: a list, or a function returning one (called now: menus are built when opened)."""
    return list(items() if callable(items) else items)


def _menu(master, items):
    scale = _scaling(master) or 1.0
    menu = tkinter.Menu(master, tearoff=0, font=(FAMILY, -round(SIZES['body'] * scale)), relief='flat',
                        borderwidth=1, background=resolve(SURFACE), foreground=resolve(INK),
                        activebackground=resolve(ACCENT_SOFT), activeforeground=resolve(INK),
                        disabledforeground=resolve(FAINT))
    menu.choice = None
    for item in items:
        if item is SEPARATOR:
            menu.add_separator()
            continue
        options = {'label': item.label, 'state': 'normal' if item.enabled else 'disabled'}
        if item.danger:
            options.update(foreground=resolve(DANGER), activeforeground=resolve(DANGER))
        if item.children:
            menu.add_cascade(menu=_menu(menu, menu_items(item.children)), **options)
            continue
        if item.command is not None:
            options['command'] = item.command
        if item.checked is None:
            menu.add_command(**options)
            continue
        if menu.choice is None:
            menu.choice = tkinter.IntVar(master=menu, value=-1)
        index = menu.index('end')
        value = 0 if index is None else index + 1
        menu.add_radiobutton(variable=menu.choice, value=value, **options)
        if item.checked:
            menu.choice.set(value)
    return menu


def popup(widget, items, x=None, y=None, align_right=False):
    """Open the menu of ``items`` (a list or a function returning one, called now) at screen point (x, y),
    by default the bottom-left corner of ``widget``; ``align_right``: x is where the menu's right edge goes.
    Only one menu is up at a time.  Returns the tk.Menu (None for no entries)."""
    entries = menu_items(items)
    while _open_menus:
        try:
            _open_menus.pop().destroy()
        except tkinter.TclError:
            pass
    if not entries:
        return None
    menu = _menu(widget, entries)
    _open_menus.append(menu)
    if x is None or y is None:
        x, y = widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height()
    if align_right:
        menu.update_idletasks()
        x -= menu.winfo_reqwidth()
    try:
        menu.tk_popup(int(x), int(y))
    finally:
        menu.grab_release()
    return menu


def menu_button(parent, items, size='md'):
    """The '⋯' of an area: a square ghost button that opens ``items`` below itself, right edges aligned.
    ``button.menu_items`` keeps ``items`` (tests and agents read the entries without opening the menu)."""
    side = CONTROL_H_SM if size == 'sm' else CONTROL_H
    picture = icon(ICON_MORE, 'accent', 16)
    more = AppButton(parent, text='' if picture is not None else '⋯', image=picture, kind='ghost', size=size,
                     width=side, height=side)
    more.menu_items = items
    more.configure(command=lambda: popup(more, items, more.winfo_rootx() + more.winfo_width(),
                                         more.winfo_rooty() + more.winfo_height(), align_right=True))
    return more


def bind_menu(widget, items):
    """Right click, Shift+F10 and the menu key open ``items`` on ``widget`` (the mirror of its '⋯'; existing
    bindings stay).  On an AppTable a right click first selects the row under the mouse when it is not
    selected yet (as Windows Explorer does) and opens nothing over the heading or empty space."""
    def mouse(event):
        if isinstance(widget, AppTable):
            row = widget.identify_row(event.y)
            if not row or widget.identify_region(event.x, event.y) == 'heading':
                return 'break'
            if row not in widget.selection():
                widget.selection_set(row)
            widget.focus(row)
            widget.event_generate('<<TreeviewSelect>>')
        popup(widget, items, event.x_root, event.y_root)
        return 'break'

    def key(_event):
        x, y = widget.winfo_rootx() + 8, widget.winfo_rooty() + 8
        if isinstance(widget, AppTable) and widget.selection():
            box = widget.bbox(widget.selection()[0])
            if box:
                x, y = widget.winfo_rootx() + box[0] + 8, widget.winfo_rooty() + box[1] + box[3]
        popup(widget, items, x, y)
        return 'break'

    widget.bind('<Button-3>', mouse, add='+')
    for sequence in ('<Shift-F10>', '<App>'):
        try:
            widget.bind(sequence, key, add='+')
        except tkinter.TclError:
            pass        # a Tk without the menu key's keysym


# ---------------------------------------------------------------- one-line text dialog (00-system 3.2)
class TextDialog(ctk.CTkToplevel):
    """The window of ``ask_text``: an entry and [Cancel] [OK]; Enter accepts, Esc cancels, OK is grey while
    the entry is blank.  ``result`` is the stripped text after OK, else None.  Built without waiting, so
    tests can drive it (``entry``, ``ok_button``, ``cancel_button``, ``accept()``, ``cancel()``)."""

    def __init__(self, parent, title, initial=''):
        super().__init__(parent, fg_color=SURFACE)
        self.result = None
        self.title(title)
        self.resizable(False, False)
        top = parent.winfo_toplevel()
        if top.winfo_viewable():
            self.transient(top)
        self.value = tkinter.StringVar(master=self, value=initial)
        body = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        body.pack(fill='both', expand=True, padx=PAD, pady=PAD)
        body.grid_columnconfigure(0, weight=1)
        self.entry = entry(body, textvariable=self.value, width=360 - 2 * PAD)
        self.entry.grid(row=0, column=0, columnspan=3, sticky='ew')
        ok = WORDS['save']
        self.cancel_button = button(body, WORDS['cancel'], self.cancel, kind='ghost',
                                    width=fitted_width(body, WORDS['cancel']))
        self.cancel_button.grid(row=1, column=1, sticky='e', pady=(PAD, 0), padx=(0, INLINE))
        self.ok_button = button(body, ok, self.accept, kind='primary', width=fitted_width(body, ok))
        self.ok_button.grid(row=1, column=2, sticky='e', pady=(PAD, 0))
        self.value.trace_add('write', self._typed)
        self._typed()
        self.bind('<Return>', self.accept)
        self.bind('<Escape>', self.cancel)
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        icon_path = str(top.tk.call('wm', 'iconbitmap', top._w))   # the raw query: CTk's own marks the icon as set
        if icon_path:       # CTkToplevel puts its own icon back after about 200 ms
            self.after(250, lambda: self._set_icon(icon_path))
        self._place_over(top)
        self.entry.focus_set()
        self.entry.select_range(0, 'end')
        self.entry.icursor('end')

    def _set_icon(self, path):
        try:
            self.iconbitmap(path)
        except tkinter.TclError:
            pass

    def _place_over(self, top):
        """Centred over ``top`` (physical pixels; the raw wm call is not scaled by customtkinter), never left of or
        above its top-left corner (which may be negative on a monitor left of or above the primary one)."""
        self.update_idletasks()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_reqwidth()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_reqheight()) // 3
        self.tk.call('wm', 'geometry', self._w, f'+{max(top.winfo_rootx(), x)}+{max(top.winfo_rooty(), y)}')

    def _typed(self, *_):
        self.ok_button.configure(state='normal' if self.value.get().strip() else 'disabled')

    def accept(self, _event=None):
        text = self.value.get().strip()
        if not text:
            return 'break'
        self.result = text
        self.destroy()
        return 'break'

    def cancel(self, _event=None):
        self.result = None
        self.destroy()
        return 'break'


def ask_text(parent, title, initial=''):
    """A one-line text dialog that follows the theme (rename and the like).  Returns the stripped text, or None
    after Cancel, Esc, closing, or a blank entry.  The OK button says WORDS['save']."""
    dialog = TextDialog(parent, title, initial)
    try:
        dialog.wait_visibility()
        dialog.grab_set()
    except tkinter.TclError:
        pass        # a window that cannot be shown or grabbed still takes the input (or is gone already)
    try:
        dialog.wait_window()
    except tkinter.TclError:
        pass        # closed while it was being shown
    return dialog.result


# ---------------------------------------------------------------- notice bar (00-system 2.9, 3.4)
_NOTICE_TONES = {'idle': (INK, SURFACE_ALT), 'warn': (WARN, WARN_SOFT)}


class NoticeBar(ctk.CTkFrame):
    """One line of status above the content it explains (or below the control it answers), with at most two
    ghost buttons on the right.  The caller grids it once and calls ``hide()``; ``show()`` / ``hide()`` then
    use grid() / grid_remove().  The text is cut to the width (``full_text`` keeps it whole)."""

    def __init__(self, master, tone='idle'):
        super().__init__(master, corner_radius=RADIUS, height=CONTROL_H_SM)
        if tone not in _NOTICE_TONES:
            tone = 'idle'
        self.tone = tone
        self.grid_columnconfigure(0, weight=1)
        self.label = label(self, '', role='caption', anchor='w')
        self.label.grid(row=0, column=0, sticky='ew', padx=(12, INLINE), pady=4)
        fit(self.label, '')
        self._text = ''
        self._buttons = ()
        self._painted = None
        self._paint(tone)

    def _paint(self, tone):
        if tone == self._painted:       # repainting the frame redraws every child: only on a change
            return
        self._painted = tone
        ink, fill = _NOTICE_TONES[tone]
        self.configure(fg_color=fill)
        self.label.configure(text_color=ink, fg_color=fill)

    def show(self, text, actions=(), tone=None):
        """Show ``text`` with ``actions`` [(label, command)] (at most two) as ghost buttons; ``tone`` 'idle' or
        'warn' (None: the bar's own tone)."""
        self._text = '' if text is None else str(text)
        self._paint(tone if tone in _NOTICE_TONES else self.tone)
        actions = tuple(actions)[:2]
        if tuple(caption for caption, _ in actions) == tuple(old.cget('text') for old in self._buttons):
            # The same captions (a countdown ticking every second): keep the buttons, so they do not blank and
            # rebuild; only the commands change.
            for old, (_, command) in zip(self._buttons, actions):
                old.configure(command=command)
        else:
            for old in self._buttons:
                old.destroy()
            made = []
            for column, (caption, command) in enumerate(actions, 1):
                action = button(self, caption, command, kind='ghost', size='sm', width=fitted_width(self, caption, 'sm', ghost=True))
                action.grid(row=0, column=column, padx=(0, INLINE))
                made.append(action)
            self._buttons = tuple(made)
        self.label.configure(text=self._text)
        if not self.winfo_manager():
            self.grid()

    def hide(self):
        if self.winfo_manager():
            self.grid_remove()

    @property
    def shown(self):
        return bool(self.winfo_manager())

    @property
    def full_text(self):
        return self._text

    @property
    def buttons(self):
        return self._buttons


def notice_bar(parent, tone='idle'):
    """A ``NoticeBar``; ``tone`` 'idle' (SURFACE_ALT) or 'warn' (WARN_SOFT, WARN text)."""
    return NoticeBar(parent, tone)


# ---------------------------------------------------------------- path button (00-system 3.6)
def _middle_cut(path, measure, width):
    """``path`` cut in the middle to ``width`` pixels: the drive and the last folder stay ('D:\\Media\\…\\Downloads'),
    then only the last folder cut at its end, then the plain end cut."""
    if measure(path) <= width:
        return path
    parts = path.replace('/', '\\').split('\\')
    if len(parts) > 2:
        for keep in range(len(parts) - 2, 0, -1):
            shown = '\\'.join(parts[:keep] + ['…', parts[-1]])
            if measure(shown) <= width:
                return shown
        head = parts[0] + '\\…\\'
        if measure(head + '…') <= width:
            return head + _ellipsize(parts[-1], lambda text: measure(head + text), width)
    return _ellipsize(path, measure, width)


class PathButton(AppButton):
    """A folder or file shown on a field-coloured button with an icon on the left; the button is the way to change
    it (``command`` opens the system dialog).  It has the fill of a drop-down (MENU, one step darker on hover, the
    same fill while disabled), so a path, a drop-down and an entry read as one kind of field.  Fixed width: the full
    path is cut in the middle once, when it is set (``path`` keeps it whole); an empty path shows
    WORDS['choose'].  The icon is ICON_FOLDER until set_path gives another."""

    def __init__(self, master, command=None, width=PATH_W):
        self._icon_code = ICON_FOLDER
        super().__init__(master, text='', command=command, kind='ghost', width=width, anchor='w', text_color=INK,
                         fg_color=MENU, hover_color=MENU_BUTTON, image=icon(ICON_FOLDER, 'muted', 16), compound='left')
        self._fill_off = MENU       # a disabled path keeps its field; only the text and the icon dim
        self._path = ''
        self.set_path('')

    @property
    def path(self):
        return self._path

    def set_path(self, path, icon_code=None):
        """Show ``path`` (cut to the width) and, when given, the icon ``icon_code`` (ICON_FOLDER, ICON_ARCHIVE …)."""
        self._path = '' if path is None else str(path)
        if icon_code is not None and icon_code != self._icon_code:
            self._icon_code = icon_code
            self.configure(image=icon(icon_code, 'muted', 16))
        if not self._path:
            self.configure(text=WORDS['choose'])
            return
        scale = _scaling(self) or 1.0
        # the room of the text: the width less the corners, the icon and its gap
        room = round((self.cget('width') - 2 * RADIUS - 16 - 6) * scale) - 2
        self.configure(text=_middle_cut(self._path, _measure_font(self, self.cget('font')).measure, room))


def path_button(parent, command, width=PATH_W):
    """A ``PathButton`` (download folder, archive folder, cookies file)."""
    return PathButton(parent, command=command, width=width)


# ---------------------------------------------------------------- text fitting
def _fit_room(label_widget):
    """Physical pixels the text of a CTkLabel may use."""
    scale = _scaling(label_widget)
    inset = min(label_widget.cget('corner_radius'), round(label_widget.cget('height') / 2))
    return label_widget.winfo_width() - 2 * round(inset * scale) - 2


def _refit(label_widget):
    full = getattr(label_widget, '_fit_text', None)
    if full is None:
        return
    try:
        shown = full.replace('\n', ' ')
        # A label in a window that is not shown (withdrawn) keeps the full text: its width is
        # not a width the owner sees.  It is refitted when it is mapped (<Map>, bound in fit()).
        if label_widget.winfo_width() > 1 and label_widget.winfo_viewable():
            measure = _measure_font(label_widget, label_widget.cget('font')).measure
            room = _fit_room(label_widget)
            shown = _ellipsize(shown, measure, room)
        if shown != label_widget.cget('text'):
            label_widget._fit_write(text=shown)     # the label's own configure: the cut text is not a new full text
    except tkinter.TclError:
        pass


def fit(label, text):
    """Show ``text`` on one line of a ``CTkLabel``, cut to the label's width with a trailing '…'.

    The full text is kept (``full_text(label)``) and the label is refitted whenever its width
    changes. A label that has no width yet (a withdrawn window) shows the full text.

    From the first call on the label stays fitted: ``label.configure(text=...)`` is taken as a
    new full text and shown cut in the same way (``configure`` is replaced on this one label),
    so a handler that writes the label directly can never have its text replaced by an older
    one at the next resize. ``label.cget('text')`` is the text as shown, cut or not.

    Precondition: the label is gridded ``sticky='ew'`` in a column with a weight above 0, so
    that its width comes from the layout. In a column without weight a label is as wide as
    its text: it would keep the full text and push its neighbours out.
    """
    if not getattr(label, '_fit_bound', False):
        label._fit_bound = True
        write = label._fit_write = label.configure

        def configure(require_redraw=False, **kwargs):
            refit = 'font' in kwargs
            if 'text' in kwargs:
                value = kwargs.pop('text')
                label._fit_text = '' if value is None else str(value)
                refit = True
            if kwargs or require_redraw:
                write(require_redraw=require_redraw, **kwargs)
            if refit:
                _refit(label)

        label.configure = configure
        _bind_outer(label, '<Configure>', lambda event: _refit(label))
        _bind_outer(label, '<Map>', lambda event: _refit(label))
    label._fit_text = '' if text is None else str(text)
    _refit(label)


def full_text(label):
    """The uncut text of a label managed by ``fit`` (however it was last written); the label text otherwise."""
    text = getattr(label, '_fit_text', None)
    return label.cget('text') if text is None else text


_NO_LINE_START = frozenset('，。、；：？！）］｝〉》」』】〕％…,.;:?!)]}%”’')
_NO_LINE_END = frozenset('（［｛〈《「『【〔([{“‘')


def _break_units(text):
    """Split one paragraph into the pieces a line break must not divide.

    A Chinese (or other wide) character is a piece of its own, a run of Latin letters, digits
    and symbols is one piece. A space stays at the end of its piece, closing punctuation is
    glued to the piece before it and opening punctuation to the piece after it.
    """
    units = []
    for char in text:
        if not units:
            units.append(char)
            continue
        last = units[-1][-1]
        if char == ' ' or char in _NO_LINE_START or last in _NO_LINE_END:
            units[-1] += char
        elif last == ' ' or ord(char) >= 0x2E80 or ord(last) >= 0x2E80:
            units.append(char)
        else:
            units[-1] += char
    return units


def _wrap_text(text, width_of, limit):
    """``text`` with line breaks so that no line is wider than ``limit`` (``width_of`` maps text to pixels).

    Tk wraps at spaces only, so a Chinese sentence with a few spaces in it breaks long before
    the line is full. This fills every line and breaks between Chinese characters, never
    inside a Latin word or number unless that word alone is wider than a line.
    """
    lines = []
    for paragraph in text.split('\n'):
        line, used = '', 0
        for unit in _break_units(paragraph):
            bare = width_of(unit.rstrip(' '))
            if line and used + bare > limit:
                lines.append(line.rstrip(' '))
                line, used = '', 0
            if not line and bare > limit:           # wider than a whole line: cut it by characters
                for char in unit:
                    step = width_of(char)
                    if line and used + step > limit:
                        lines.append(line)
                        line, used = '', 0
                    line += char
                    used += step
                continue
            line += unit
            used += width_of(unit)
        lines.append(line.rstrip(' '))
    return '\n'.join(lines)


def _width_lookup(measure_font):
    """``width_of(text)`` for a Tk font, remembering every piece it has measured."""
    known = measure_font.__dict__.setdefault('_piece_widths', {})
    measure = measure_font.measure

    def width_of(piece):
        try:
            return known[piece]
        except KeyError:
            value = known[piece] = measure(piece)
            return value
    return width_of


def wrap_to_width(label):
    """Keep the ``wraplength`` of a ``CTkLabel`` equal to its own width and break its lines well.

    Same precondition as ``fit``: ``sticky='ew'`` in a weighted column. The width is read from
    the outer widget on every change and never from the event: the inner canvas and the inner
    label of a CTkLabel report different widths, and following them never settles.
    ``wraplength`` is in logical pixels, ``winfo_width()`` in physical ones.

    The line breaks are computed here (see ``_wrap_text``) and given to the Tk label inside
    the CTkLabel only: ``label.cget('text')`` keeps returning the text without them.
    """
    if getattr(label, '_wrap_bound', False):
        return
    label._wrap_bound = True
    label._wrap_px = None
    label._wrap_key = None
    plain_configure = label.configure

    def lay_out(scale):
        inner = getattr(label, '_label', None)      # the Tk label inside a CTkLabel
        if inner is None or label._wrap_px is None:
            return
        widths = _width_lookup(_measure_font(label, label.cget('font')))
        broken = _wrap_text(str(label.cget('text')), widths, int(label._wrap_px * scale) - 1)
        if broken != inner.cget('text'):
            inner.configure(text=broken)

    def refresh(event=None):
        try:
            width = label.winfo_width()
            # Not before the label is shown: while a window is built and not yet mapped, its frames
            # take their requested sizes, and a wrap that follows them shrinks step by step (measured:
            # 85 passes and 2.8 s for the settings page).  <Map> brings the first real wrap.
            if width <= 1 or not label.winfo_viewable():
                return
            scale = _scaling(label)
            new = max(1, int(width / scale) - 2)
            if new != label._wrap_px:
                label._wrap_px = new
                plain_configure(wraplength=new)
            if (new, scale) != label._wrap_key:
                label._wrap_key = (new, scale)
                lay_out(scale)
        except tkinter.TclError:
            pass

    def configure(require_redraw=False, **kwargs):
        plain_configure(require_redraw=require_redraw, **kwargs)
        if 'text' in kwargs or 'font' in kwargs:    # CTkLabel has just handed Tk the unbroken text
            try:
                lay_out(_scaling(label))
            except tkinter.TclError:
                pass

    label.configure = configure
    _bind_outer(label, '<Configure>', refresh)
    _bind_outer(label, '<Map>', refresh)
    refresh()


# ---------------------------------------------------------------- page width cap
CAP_COLUMN = 99     # grid column of a capped page that takes the width its content may not use


def set_page_cap(root, content, pages):
    """Limit the content width of pages stacked in ``content``: ``pages`` maps page frame -> max logical px or None.

    The content of a capped page stays left-aligned and at most that wide; the rest of the
    width stays empty. The page frame itself keeps filling ``content``: the pages lie on top
    of each other and are shown with ``tkraise()``, so a page narrower than its cell would
    let the page below show through beside it. The cap is therefore applied inside the page:
    its grid column ``CAP_COLUMN``, which no page uses, gets the unused width as its minimum
    size, and the page's own columns (column 0 with a weight) share what is left. Nothing
    else of the page's layout is touched.
    """
    pages = dict(pages)
    pending = []

    def apply():
        pending.clear()
        try:
            width = content.winfo_width()
            if width <= 1:
                return
            scale = _scaling(content)
            for page, limit in pages.items():
                if limit is None:
                    continue
                spare = max(0, width - round(limit * scale))    # grid sizes are physical pixels
                if getattr(page, '_cap_spare', None) != spare:
                    page.grid_columnconfigure(CAP_COLUMN, minsize=spare)
                    page._cap_spare = spare
        except tkinter.TclError:
            pass

    def later(event=None):
        if not pending:
            try:
                pending.append(root.after_idle(apply))
            except tkinter.TclError:
                pass

    _bind_outer(content, '<Configure>', later)
    # The cap is a number of physical pixels, so it changes with the scaling as well, and a
    # maximised window keeps its size when the scaling changes: no <Configure> arrives then.
    ctk.ScalingTracker.add_widget(lambda widget_scaling=None, window_scaling=None: later(), content)
    later()
