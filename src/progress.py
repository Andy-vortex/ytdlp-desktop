"""Lightweight, time-based progress animation; never predicts unreported progress.

A determinate bar glides at an even speed to each reported value; an indeterminate bar runs one
segment left to right at an even speed.

Colours come from ui_theme tokens ((light, dark) pairs); explicit colour strings still work.
"""
import math
import time
import tkinter as tk

import customtkinter as ctk
import ui_theme


FLOW_PERIOD = 1.8      # s: one pass of the indeterminate segment across the bar
FLOW_LENGTH = 0.3      # the indeterminate segment, as a share of the bar


def flow(elapsed, span):
    """(start, end) of the indeterminate segment ``elapsed`` s into a run: it enters at the left and
    leaves at the right at an even speed, clipped to 0..span."""
    length = span * FLOW_LENGTH
    head = elapsed / FLOW_PERIOD % 1 * (span + length)
    return max(head - length, 0.0), min(head, span)


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

        Each is a ui_theme token (a ``(light, dark)`` pair) or one colour string.
        """
        self._motion = ProgressMotion()
        self._mode = 'determinate'
        self._running = False
        self._frame_id = None
        self._phase_started = time.perf_counter()
        self._fill_color = ui_theme.ACCENT if progress_color is None else progress_color
        self._track_color = ui_theme.TRACK if fg_color is None else fg_color
        self._surface_color = ui_theme.SURFACE if surface is None else surface
        super().__init__(master, height=height, corner_radius=0, fg_color=self._surface_color, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                background=self._apply_appearance_mode(self._surface_color))
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._track = self.canvas.create_line(0, 0, 0, 0, fill=self._apply_appearance_mode(self._track_color),
                                              capstyle=tk.ROUND)
        self._fill = self.canvas.create_line(0, 0, 0, 0, fill=self._apply_appearance_mode(self._fill_color),
                                             capstyle=tk.ROUND, state='hidden')
        self.canvas.bind('<Configure>', lambda _event: self._render())

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, 'canvas'):
            self.canvas.configure(background=self._apply_appearance_mode(self._surface_color))
            self.canvas.itemconfigure(self._track, fill=self._apply_appearance_mode(self._track_color))
            self.canvas.itemconfigure(self._fill, fill=self._apply_appearance_mode(self._fill_color))

    @property
    def target(self):
        return self._motion.target

    def set(self, value, immediate=False):
        self._motion.retarget(value, time.perf_counter(), immediate=immediate)
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

    def _schedule(self):
        active = self._running if self._mode == 'indeterminate' else not self._motion.settled(time.perf_counter())
        if active and self._frame_id is None:
            self._frame_id = self.after(self.FRAME_MS, self._tick)

    def _tick(self):
        self._frame_id = None
        self._render()
        self._schedule()

    def _render(self):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2 or height < 2:
            return
        radius, span = height / 2, max(width - height, 1)
        self.canvas.coords(self._track, radius, radius, width - radius, radius)
        self.canvas.itemconfigure(self._track, width=height)
        if self._mode == 'indeterminate':
            start, end = flow(time.perf_counter() - self._phase_started, span)
        else:
            start, end = 0.0, span * self.get()
        if end <= start:
            self.canvas.itemconfigure(self._fill, state='hidden')
            return
        self.canvas.coords(self._fill, radius + start, radius, radius + end, radius)
        self.canvas.itemconfigure(self._fill, width=height, state='normal')

    def destroy(self):
        self.stop()
        super().destroy()
