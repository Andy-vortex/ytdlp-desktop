"""Frame rhythm and motion of the progress bar, run by hand (2.0.3; not a test_ file, run_tests.py skips it).

    .venv\\Scripts\\python.exe -X utf8 -B verification\\bench_progress.py [light|dark]

The real bar (904 logical px, like the download page) under mainloop in a borderless window at the bottom
left that never takes the focus, about 32 s; _tick and _render are wrapped on the instance.  Parts:
indeterminate (8 s), set1 (8 s, set() once a second, growing evenly), sequence (download, [Merger], done,
retry, next item in ui_download's order at the real program's report times, two 0.18 s main-thread blocks).
Prints one JSON line and the gates; exits 1 when one fails.  The limits were measured on the owner's
machine (200 %); elsewhere compare with the old build, except set1_moving_pct >= 85.
"""
import _sandbox                                    # first: a private state and app folder, removed at exit
import ctypes
import json
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
         ('over_target_frames', '<=', 0), ('focus_taken', '<=', False)]


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
    frames, marks, now = [], [], {'part': 'setup', 'tick': False, 'animating': False}
    render, tick = bar._render, bar._tick

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
        root.after(int(seconds * 1000), lambda: (marks.append((time.perf_counter(), name)), action()))

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
    root.after(int((end + 4.6) * 1000), root.quit)
    root.mainloop()
    focus_taken = user32.GetForegroundWindow() == hwnd
    width, height = bar.canvas.winfo_width(), bar.canvas.winfo_height()
    bar.stop()
    root.destroy()
    return frames, marks, width, height, focus_taken


def main():
    frames, marks, width, height, focus_taken = run()

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
        'over_target_frames': sum(f['over'] for f in frames), 'focus_taken': focus_taken}
    print(json.dumps(result), flush=True)
    failed = 0
    for key, op, limit in GATES:
        ok = result[key] <= limit if op == '<=' else result[key] >= limit
        failed += not ok
        print(f"{'pass' if ok else 'FAIL'}  {key} = {result[key]}  (gate {op} {limit})", flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
