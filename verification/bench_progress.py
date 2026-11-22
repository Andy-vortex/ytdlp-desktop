"""Frame rhythm and motion of the progress bar, run by hand (2.0.3; not a test_ file, run_tests.py skips it).

    .venv\\Scripts\\python.exe -X utf8 -B verification\\bench_progress.py [light|dark]

The real bar (904 logical px, like the download page) under mainloop in a borderless window at the bottom
left that never takes the focus, about 39 s; _tick and _render are wrapped on the instance.  Parts:
indeterminate (8 s), set1 (8 s, set() once a second, growing evenly), sequence (download, [Merger], done,
retry, next item in ui_download's order at the real program's report times, two 0.18 s main-thread blocks),
switch (2.0.4, 7 s: video then audio, resume, wait, done with links unfinished, next item, stop).
Prints one JSON line and the gates; exits 1 when one fails.  The limits were measured on the owner's
machine (200 %); elsewhere compare with the old build, except set1_moving_pct >= 85.

switch_max_px (2.0.4): the largest change in one frame at a switch, measured as in design-polish 5: the
visible amount of every painted picture (after each frame and each switch) is the sum over the visible
lines of (length + bar height) x the opacity their colour shows between the track and the fill colour,
here taken pixel by pixel in the canvas's stacking order so that a line covered by another one does not
count; the change is the difference between two pictures in a row, the largest up to 0.5 s after each
switch (or up to the next scripted event, such as a main-thread block) counts.  A fade of the whole bar changes about width x 15.5 / 200 px a frame by itself; the gate
is 10 % of the width.
"""
import _sandbox                                    # first: a private state and app folder, removed at exit
import ctypes
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

os.environ['YTDLP_DESKTOP_STATE'] = str(_sandbox.BASE / 'state')
import paths  # noqa: E402
for folder in (paths.STATE_DIR, paths.APP_DIR):
    assert Path(folder).resolve().is_relative_to(_sandbox.BASE), folder
import customtkinter as ctk  # noqa: E402
from progress import SmoothProgressBar  # noqa: E402
import ui_theme  # noqa: E402

LOOK = sys.argv[1] if len(sys.argv) > 1 else 'light'
REPORTS = [(0.0, .008), (.397, .053), (.893, .114), (1.887, .235), (2.911, .356), (3.906, .476),
           (4.902, .596), (5.923, .716), (6.792, .835), (7.926, .971)]    # measure-frames app-normal-trace
GATES = [('indeterminate_over20_pct', '<=', 1), ('indeterminate_median_ms', '<=', 17),
         ('indeterminate_move_ratio', '<=', 1.2), ('set1_over20_pct', '<=', 1), ('set1_moving_pct', '>=', 85),
         ('set1_speed_ratio', '<=', 1.1), ('set1_render_ms_p95', '<=', .5), ('download_moving_pct', '>=', 85),
         ('download_stops_over_200ms', '<=', 2), ('sequence_over20_pct', '<=', 3),
         ('over_target_frames', '<=', 0), ('focus_taken', '<=', False), ('switch_max_pct', '<=', 10)]
SWITCHES = ('launch', 'progress', 'merger', 'done', 'retry', 'progress2', 'item2', 'switch', 'drop', 'resume', 'wait',
            'merger2', 'partial', 'item3', 'stop')


def q(values, share):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(share * (len(values) - 1))))] if values else 0.0


def run():
    """Shows the bar, plays the three parts; returns (frames, marks, canvas width, height, focus taken)."""
    user32 = ctypes.windll.user32
    ui_theme.enable_dpi_awareness()
    ctk.set_appearance_mode(LOOK)
    ui_theme.apply_theme()
    before = user32.GetForegroundWindow()
    root = ctk.CTk(fg_color=ui_theme.SURFACE)
    root.withdraw()
    root.overrideredirect(True)
    scale = ctk.ScalingTracker.get_window_scaling(root)
    root.geometry(f'944x28+16+{int(root.winfo_screenheight() / scale) - 120}')
    bar = SmoothProgressBar(root, height=6, surface=ui_theme.SURFACE)
    bar.pack(fill='x', padx=20, pady=11)
    root.update_idletasks()
    hwnd = user32.GetParent(root.winfo_id())
    user32.SetWindowLongW(hwnd, -20, user32.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80)  # NOACTIVATE, TOOLWINDOW
    if before:
        user32.SetForegroundWindow(before)
    user32.ShowWindow(hwnd, 4)                      # SW_SHOWNOACTIVATE
    root.update()
    frames, marks, pictures, now = [], [], [], {'part': 'setup', 'tick': False, 'animating': False}
    render, tick = bar._render, bar._tick

    def picture():                                  # what is on the canvas now: (x0, x1, colour), bottom first
        lines = [(*bar.canvas.coords(item)[::2], bar.canvas.itemcget(item, 'fill')) for item in bar.canvas.find_all()
                 if item != bar._track and bar.canvas.itemcget(item, 'state') != 'hidden']
        pictures.append((time.perf_counter(), lines))

    def recorded_render():
        start = time.perf_counter()
        render()
        cost = (time.perf_counter() - start) * 1000
        x0, _y, x1, _y = (bar.canvas.coords(bar._fill) if bar.canvas.itemcget(bar._fill, 'state') != 'hidden'
                          else (None, 0, None, 0))
        value = bar.get() if bar._mode == 'determinate' else None
        frames.append({'t': start, 'ms': cost, 'tick': now['tick'], 'part': now['part'], 'mode': bar._mode,
                       'x0': x0, 'x1': x1, 'value': value, 'over': value is not None and value > bar.target})

    def recorded_tick():
        now['tick'] = True
        tick()
        now['tick'] = False
        picture()
    bar._render, bar._tick = recorded_render, recorded_tick

    def animate(enabled):                           # ui_download.animate
        if enabled != now['animating']:
            now['animating'] = enabled
            if enabled:
                bar.configure(mode='indeterminate')
                bar.start()
            else:
                bar.stop()
                bar.configure(mode='determinate')

    def progress(value):                            # ui_download.on_progress
        animate(False)
        bar.set(value)

    def at(seconds, name, action):
        root.after(int(seconds * 1000), lambda: (marks.append((time.perf_counter(), name)), action(), picture()))

    at(0, 'indeterminate', lambda: (now.update(part='indeterminate'), animate(True)))
    at(8, 'set1', lambda: (animate(False), bar.set(0, immediate=True), now.update(part='set1')))
    for second in range(8):
        at(8.001 + second, 'set', lambda s=second: bar.set((s + 1) / 8.8))
    at(16.5, 'launch', lambda: (now.update(part='sequence'), bar.set(0, immediate=True), animate(True)))
    for when, value in REPORTS:
        at(17.4 + when, 'progress', lambda v=value: progress(v))
    end = 17.4 + REPORTS[-1][0]
    at(end + .25, 'merger', lambda: animate(True))
    at(end + .5, 'stall', lambda: time.sleep(.18))
    at(end + .85, 'done', lambda: (animate(False), bar.set(1)))
    at(end + 1.25, 'retry', lambda: (time.sleep(.18), bar.set(0, immediate=True), animate(True)))
    at(end + 2.4, 'progress2', lambda: progress(.05))
    at(end + 2.8, 'progress2', lambda: progress(.12))
    at(end + 3.6, 'item2', lambda: (bar.set(0, immediate=True), animate(True)))
    start = end + 4.6                               # 2.0.4: the switches the sequence does not have
    at(start, 'switch', lambda: (now.update(part='switch'), progress(.05)))
    at(start + .4, 'report', lambda: progress(.9))
    at(start + .9, 'drop', lambda: progress(.04))   # video done, audio starts
    at(start + 1.4, 'report', lambda: progress(.9))
    at(start + 1.9, 'resume', lambda: animate(True))                # on_resuming, after a robust-mode wait
    at(start + 2.6, 'wait', lambda: animate(False))                 # on_waiting
    at(start + 3.3, 'merger2', lambda: animate(True))
    at(start + 4.0, 'partial', lambda: (animate(False), bar.set(.5)))   # done, half the links unfinished
    at(start + 4.7, 'item3', lambda: (bar.set(0, immediate=True), animate(True)))
    at(start + 5.4, 'report', lambda: progress(.6))
    at(start + 5.9, 'resume', lambda: animate(True))
    at(start + 6.4, 'stop', lambda: animate(False))                 # stopped: the segment becomes the fill
    root.after(int((start + 7) * 1000), root.quit)
    root.mainloop()
    focus_taken = user32.GetForegroundWindow() == hwnd
    width, height = bar.canvas.winfo_width(), bar.canvas.winfo_height()
    colours = [[part // 257 for part in bar.canvas.winfo_rgb(bar._apply_appearance_mode(colour))]
               for colour in (bar._track_color, bar._fill_color)]
    bar.stop()
    root.destroy()
    return frames, marks, pictures, colours, width, height, focus_taken


def switch_changes(pictures, marks, colours, width, height):
    """{switch: the largest one-frame change of the visible amount (px) up to 0.5 s after it}."""
    track, fill = colours
    channel = max(range(3), key=lambda i: abs(fill[i] - track[i]))

    def amount(lines):
        pixels = [0.0] * width
        for x0, x1, colour in lines:
            shade = int(colour[1 + 2 * channel:3 + 2 * channel], 16) if colour.startswith('#') and len(colour) == 7 \
                else None
            opacity = 1.0 if shade is None else (shade - track[channel]) / (fill[channel] - track[channel])
            left, right = max(0, round(x0 - height / 2)), min(width, round(x1 + height / 2))
            pixels[left:right] = [opacity] * (right - left)
        return sum(pixels)
    changes = {}
    for (when, name), (following, _name) in zip(marks, marks[1:] + [(math.inf, '')]):
        if name not in SWITCHES:
            continue
        shown = [lines for t, lines in pictures if when <= t <= min(when + .5, following)]
        before = [lines for t, lines in pictures if t < when][-1:]
        amounts = [amount(lines) for lines in before + shown]
        biggest = max((abs(b - a) for a, b in zip(amounts, amounts[1:])), default=0.0)
        changes[name] = round(max(changes.get(name, 0.0), biggest), 1)
    return changes


def main():
    frames, marks, pictures, colours, width, height, focus_taken = run()
    changes = switch_changes(pictures, marks, colours, width, height)

    def gaps(name):
        ticks = [f['t'] for f in frames if f['tick'] and f['part'] == name]
        return [(b - a) * 1000 for a, b in zip(ticks, ticks[1:]) if b - a < .2]

    def over20(name):
        return round(100 * sum(g > 20 for g in gaps(name)) / max(1, len(gaps(name))), 1)
    flow = [f for f in frames if f['part'] == 'indeterminate' and f['tick'] and f['x1'] is not None]
    moves = [max(b['x0'] - a['x0'], b['x1'] - a['x1']) for a, b in zip(flow, flow[1:])
             if b['x0'] >= a['x0'] and b['x1'] >= a['x1'] and b['t'] - a['t'] < .2]
    det = [f for f in frames if f['part'] == 'set1' and f['tick'] and f['value'] is not None]
    pairs = [(a, b) for a, b in zip(det, det[1:]) if b['t'] - a['t'] < .2 and b['value'] != a['value']]
    speeds = [(b['value'] - a['value']) * (width - height) / (b['t'] - a['t']) for a, b in pairs]
    first, last = [t for t, name in marks if name == 'progress'][::len(REPORTS) - 1]
    down = [f for f in frames if first <= f['t'] <= last and f['mode'] == 'determinate' and f['x1'] is not None]
    steps = [(b['t'] - a['t'], round(a['x1']) != round(b['x1'])) for a, b in zip(down, down[1:])]
    result = {
        'look': LOOK, 'canvas_px': [width, height], 'frame_ms': SmoothProgressBar.FRAME_MS,
        'indeterminate_over20_pct': over20('indeterminate'), 'indeterminate_intervals': len(gaps('indeterminate')),
        'indeterminate_median_ms': round(statistics.median(gaps('indeterminate')), 2),
        'indeterminate_max_ms': round(max(gaps('indeterminate')), 2),
        'indeterminate_move_px': [round(q(moves, .95), 2), round(q(moves, .5), 2)],
        'indeterminate_move_ratio': round(q(moves, .95) / q(moves, .5), 3),
        'set1_over20_pct': over20('set1'),
        'set1_moving_pct': round(100 * sum(b['t'] - a['t'] for a, b in pairs) / 8, 1),
        'set1_speed_px_s': [round(q(speeds, .95), 1), round(q(speeds, .5), 1)],
        'set1_speed_ratio': round(q(speeds, .95) / q(speeds, .5), 3),
        'set1_render_ms_p95': round(q([f['ms'] for f in det], .95), 3),
        'download_moving_pct': round(100 * sum(dt for dt, moved in steps if moved) / (last - first), 1),
        'download_stops_over_200ms': sum(dt > .2 for dt, _moved in steps),
        'sequence_over20_pct': over20('sequence'),
        'over_target_frames': sum(f['over'] for f in frames), 'focus_taken': focus_taken,
        'switch_px': changes, 'switch_max_px': max(changes.values()),
        'switch_max_pct': round(100 * max(changes.values()) / width, 1)}
    print(json.dumps(result), flush=True)
    failed = 0
    for key, op, limit in GATES:
        ok = result[key] <= limit if op == '<=' else result[key] >= limit
        failed += not ok
        print(f"{'pass' if ok else 'FAIL'}  {key} = {result[key]}  (gate {op} {limit})", flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
