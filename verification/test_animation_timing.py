# serial: measures frame intervals (about 17 ms) on the wall clock
import _sandbox
import json
from pathlib import Path
import statistics
import sys
import time

# Every output goes to the sandbox (_sandbox.out); nothing is written into verification/.

import customtkinter as ctk
from studio import StudioApp

ctk.set_appearance_mode('light')
app = StudioApp(_sandbox.app_args('http://127.0.0.1/animation-test', str(_sandbox.out('ui-test'))))
app.withdraw()
app.progress.canvas.place_configure(relwidth=0, relheight=0, width=600, height=6)
app.update_idletasks()
ticks, samples = [], []
original = app.progress._tick
def record_frame():
    ticks.append(time.perf_counter())
    samples.append(app.progress.get())
    original()
app.progress._tick = record_frame
try:
    began = time.perf_counter()
    for index in range(1, 10):
        app.after(index * 150, lambda value=index * .08: app.progress.set(value))
    until = began + 1.75
    while time.perf_counter() < until:
        app.update()
        time.sleep(.001)
    intervals = [1000 * (b - a) for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) >= 35, ticks
    assert len(set(round(value, 4) for value in samples)) >= 30
    assert app.progress.target == .72
    assert abs(app.progress.get() - .72) < 1e-8
    count_at_stall = len(ticks)
    until = time.perf_counter() + .25
    while time.perf_counter() < until:
        app.update()
        time.sleep(.001)
    assert len(ticks) == count_at_stall, 'A settled bar should not keep repainting or invent progress'
    app.animate(True)
    until = time.perf_counter() + .3
    while time.perf_counter() < until:
        app.update()
        time.sleep(.001)
    assert len(ticks) > count_at_stall + 8
    app.animate(False)
    report = {'status': 'passed', 'frame_callbacks': count_at_stall,
              'median_interval_ms': round(statistics.median(intervals), 2),
              'maximum_interval_ms': round(max(intervals), 2),
              'distinct_positions': len(set(round(value, 4) for value in samples)),
              'network_stall_stops_motion': True, 'unknown_progress_animates': True}
    _sandbox.write_report('animation-report.json', report)
    print(json.dumps(report), flush=True)
finally:
    app.destroy()
