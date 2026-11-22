"""Lightweight, time-based progress animation; never predicts unreported progress.

A determinate bar glides at an even speed to each reported value; an indeterminate bar runs one
segment left to right at an even speed.  Where the picture would change in one frame (a reset, a new
file, a mode switch) it cross-fades for FADE s instead: the old shape fades out as a ghost line, and a
fill that takes over from the indeterminate segment fades in.  Lines have no alpha: a fade mixes the
track and fill colours.  A fade starts at the second frame after the switch at the earliest, and a bar
off screen (hidden, or shown again but not yet at a frame) does not fade.

Colours come from ui_theme tokens ((light, dark) pairs); explicit colour strings still work.
"""
import math
import time
import tkinter as tk

import customtkinter as ctk
import ui_theme


FLOW_PERIOD = 1.8      # s: one pass of the indeterminate segment across the bar
FLOW_LENGTH = 0.3      # the indeterminate segment, as a share of the bar
FADE = 0.2             # s: a jump or a mode switch cross-fades this long instead of changing in one frame
ON_TIME = 0.03         # s: a frame this soon after the one before: the main thread is free, a fade can start


def flow(elapsed, span):
    """(start, end) of the indeterminate segment ``elapsed`` s into a run: it enters at the left and
    leaves at the right at an even speed, clipped to 0..span."""
    length = span * FLOW_LENGTH
    head = elapsed / FLOW_PERIOD % 1 * (span + length)
    return max(head - length, 0.0), min(head, span)


def blend(a, b, share):
    """'#rrggbb' ``share`` (clamped to 0..1) of the way from rgb ``a`` to rgb ``b`` (0..255 each)."""
    share = min(max(share, 0.0), 1.0)
    return '#%02x%02x%02x' % tuple(round(x + (y - x) * share) for x, y in zip(a, b))


class ProgressMotion:
    """Glides at an even speed to the newest reported value, taking LEAD times as long as the last two
    reports were apart (SHORTEST to LONGEST s): yt-dlp reports about once a second, so the bar is still
    moving when the next report comes instead of jumping and waiting.  It never passes the reported value."""
    SHORTEST, LONGEST, LEAD = 0.24, 1.2, 1.25

    def __init__(self):
        self.duration = self.SHORTEST
        self.origin = self.target = 0.0
        self.started = 0.0
        self.reported = None

    def value(self, now):
        elapsed = max(0.0, now - self.started)
        if elapsed >= self.duration or self.origin == self.target:
            return self.target
        return self.origin + (self.target - self.origin) * elapsed / self.duration

    def settled(self, now):
        return self.origin == self.target or now - self.started >= self.duration

    def retarget(self, target, now, immediate=False):
        target = float(target)
        if not math.isfinite(target):
            return
        target = min(max(target, 0.0), 1.0)
        if not immediate and target == self.target:
            return
        current = self.value(now)
        # A large drop is a new file/stream, rather than a slow backwards sweep: shown at once, and the
        # stream's first glide has no interval to go by.
        if immediate or current - target >= 0.15:
            self.origin, self.target, self.started, self.reported = target, target, now, None
            return
        gap = self.SHORTEST if self.reported is None else (now - self.reported) * self.LEAD
        self.duration = min(max(gap, self.SHORTEST), self.LONGEST)
        self.origin, self.target, self.started, self.reported = current, target, now, now


class SmoothProgressBar(ctk.CTkFrame):
    # Tk's after() wakes on the Windows timer tick (about 15.5 ms): 16 ms waits one or two ticks at
    # random (16/31 ms frames), anything from 2 to 15 ms waits exactly one (an even ~64 frames a second).
    FRAME_MS = 10

    def __init__(self, master, height=6, progress_color=None, fg_color=None, surface=None, **kwargs):
        """``progress_color`` fills, ``fg_color`` is the track, ``surface`` the card behind the bar.

        Each is a ui_theme token (a ``(light, dark)`` pair) or one colour string; when the track or the
        fill is not a real colour (``'transparent'``), switches cut instead of fading.
        """
        self._motion = ProgressMotion()
        self._mode = 'determinate'
        self._running = False
        self._frame_id = None
        self._phase_started = time.perf_counter()
        # The fade: the ghost (the shape last drawn before a jump) goes from ghost_from to 0 opacity and the
        # fill from fill_from to 1 in FADE s from fade_started; opacity is a mix of the track and fill colours.
        self._ghost_at = self._shown = None
        self._ghost_from = self._fill_from = 1.0
        self._fade_started = -math.inf
        self._fade_waits = 0
        self._last_tick = -math.inf
        self._fading = self._off_screen = False
        self._fill_color = ui_theme.ACCENT if progress_color is None else progress_color
        self._track_color = ui_theme.TRACK if fg_color is None else fg_color
        self._surface_color = ui_theme.SURFACE if surface is None else surface
        super().__init__(master, height=height, corner_radius=0, fg_color=self._surface_color, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._track, self._ghost, self._fill = (self.canvas.create_line(0, 0, 0, 0, capstyle=tk.ROUND, state=state)
                                                for state in ('normal', 'hidden', 'hidden'))
        self._recolor()
        self.canvas.bind('<Configure>', lambda _event: self._render())
        tk.Misc.bind(self, '<Unmap>', self._unmapped)       # CTkFrame.bind would bind its own canvas

    def _unmapped(self, _event=None):
        """Hidden (the download page without rows, a minimised window): off screen until a frame finds it mapped
        again, as launch shows the bar, has it mapped and drawn, and resets it in one callback.  Off screen it is
        drawn as it is, with no fade, and nothing drawn there fades out later."""
        self._off_screen = True

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, 'canvas'):
            self._recolor()

    def _recolor(self):
        surface, track, fill = map(self._apply_appearance_mode, (self._surface_color, self._track_color,
                                                                 self._fill_color))
        self.canvas.configure(background=surface)
        rgb = []
        for color in (track, fill):
            try:
                rgb.append([part // 257 for part in self.canvas.winfo_rgb(color)])
            except tk.TclError:             # not a real colour ('transparent'): not drawn, and switches cut
                rgb.append(None)
        self._full = fill if rgb[1] else ''
        self.canvas.itemconfigure(self._track, fill=track if rgb[0] else '')
        self.canvas.itemconfigure(self._fill, fill=self._full)
        self._rgb = rgb if all(rgb) else None

    @property
    def target(self):
        return self._motion.target

    def set(self, value, immediate=False):
        now = time.perf_counter()
        before = self._motion.value(now)
        self._motion.retarget(value, now, immediate=immediate)
        if self._mode == 'determinate' and self._motion.value(now) != before:
            self._jump(now, 1.0 if immediate else None)     # a reset or a new file: the old fill fades out
        self._render()
        self._schedule()

    def get(self):
        return self._motion.value(time.perf_counter())

    def configure(self, **kwargs):
        mode = kwargs.pop('mode', None)
        if kwargs:
            super().configure(**kwargs)
        if mode is not None:
            if mode not in ('determinate', 'indeterminate'):
                raise ValueError(mode)
            if mode != self._mode and hasattr(self, 'canvas'):
                self._jump(time.perf_counter(), 0.0 if mode == 'determinate' else None)
            self._mode = mode
            if hasattr(self, 'canvas'):
                self._render()
                self._schedule()

    def cget(self, name):
        return self._mode if name == 'mode' else super().cget(name)

    def start(self):
        if not self._running:
            self._phase_started = time.perf_counter()
        self._running = True
        self._render()
        self._schedule()

    def stop(self):
        self._running = False
        if self._frame_id is not None:
            self.after_cancel(self._frame_id)
            self._frame_id = None
        if self._fading:        # a fade under way still runs to its end
            self._schedule()

    def _opacities(self, now):
        """(ghost, fill) opacity ``now``: without real colours to mix there is no fade."""
        started = now if self._fade_started is None else self._fade_started
        faded = max((now - started) / FADE, 0.0)
        if faded >= 1 or not self._rgb:
            return 0.0, 1.0
        return self._ghost_from * (1 - faded), self._fill_from + (1 - self._fill_from) * faded

    def _jump(self, now, fill_from=None):
        """The picture is about to change at once: the more visible (opacity x length) of the fill as last
        drawn and the ghost becomes the ghost and fades out; the fill fades in from ``fill_from`` (None: from
        where it is).  The fade starts at a later frame (_tick): the main thread is often busy for a while after
        a switch (on_done, launch), and the fade should not be half over when it is first drawn.  The last frame
        before the switch does not count: the first one after it is never on time."""
        ghost, fill = self._opacities(now)
        ghost = ghost if self._ghost_at else 0.0
        seen = ghost * (self._ghost_at[1] - self._ghost_at[0]) if self._ghost_at else 0.0
        if self._shown is not None and fill * (self._shown[1] - self._shown[0]) >= seen:
            self._ghost_at, ghost = self._shown, fill
        self._ghost_from, self._fill_from = ghost, fill if fill_from is None else fill_from
        self._fade_started, self._fade_waits, self._last_tick = None, 0, -math.inf

    def _schedule(self, wait=None):
        active = self._running if self._mode == 'indeterminate' else not self._motion.settled(time.perf_counter())
        if (active or self._fading) and self._frame_id is None:
            self._frame_id = self.after(wait or self.FRAME_MS, self._tick)

    def _tick(self):
        self._frame_id = None
        now = time.perf_counter()
        if self._off_screen and self.winfo_ismapped():
            self._off_screen = False
        if self._fade_started is None:
            # A fade waiting to start: it starts at the first frame that comes ON_TIME after the one before it,
            # both after the switch (the busy spell after it is over), or at its fifth frame (a main thread late
            # all along).
            self._fade_waits += 1
            if now - self._last_tick < ON_TIME or self._fade_waits >= 5:
                self._fade_started = now
        self._last_tick = now
        self._render()
        # Waiting, and the frame the fade starts at (which can come off the timer tick): 2 ms waits for the
        # next tick only, so the fade's first step is not up to 25 ms long.
        self._schedule(2 if self._fade_started in (None, now) else None)

    def _render(self):
        now = time.perf_counter()
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2 or height < 2:                     # nothing to draw: no fade
            self._fade_started, self._fading = -math.inf, False
            return
        if self._off_screen:                            # drawn as it is now, so it is right when shown again
            self._fade_started = -math.inf
        radius, span = height / 2, max(width - height, 1)
        self.canvas.coords(self._track, radius, radius, width - radius, radius)
        self.canvas.itemconfigure(self._track, width=height)
        if self._mode == 'indeterminate':
            start, end = flow(now - self._phase_started, span)
        else:
            start, end = 0.0, span * self._motion.value(now)
        ghost, fill = self._opacities(now)
        self._fading = fill < 1 or ghost > 0
        if not ghost:
            self._ghost_at = None
        self._shown = (start, end) if end > start else None
        self._line(self._ghost, self._ghost_at, ghost, radius, height)
        self._line(self._fill, self._shown, fill, radius, height)
        # Lines have no alpha: where the two overlap, the more visible one is on top.
        self.canvas.tag_raise(*((self._ghost, self._fill) if ghost > fill else (self._fill, self._ghost)))

    def _line(self, item, at, opacity, radius, height):
        if at is None:
            self.canvas.itemconfigure(item, state='hidden')
            return
        color = self._full if opacity >= 1 else blend(*self._rgb, opacity)
        self.canvas.coords(item, radius + at[0], radius, radius + at[1], radius)
        self.canvas.itemconfigure(item, width=height, fill=color, state='normal')

    def destroy(self):
        self._fading = False    # stop() schedules no frame for a fade
        self.stop()
        super().destroy()
