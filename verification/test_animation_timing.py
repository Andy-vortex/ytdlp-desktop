# serial: measures frame intervals (about 11 to 16 ms) on the wall clock
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
    # (2.0.3) The frame rhythm under a real mainloop (the busy loop above wakes Tk every millisecond).
    # Tk's after() wakes on the Windows timer tick (about 15.5 ms): FRAME_MS 10 waits one tick, 16 ms
    # waits one or two at random (36 % to 47 % of the frames took over 20 ms here).
    del ticks[:]
    app.animate(True)
    app.after(1500, app.quit)
    app.mainloop()
    app.animate(False)
    loop_intervals = [1000 * (b - a) for a, b in zip(ticks, ticks[1:]) if b - a < .2]
    assert len(loop_intervals) >= 50, loop_intervals
    over20 = 100 * sum(gap > 20 for gap in loop_intervals) / len(loop_intervals)
    assert over20 <= 10, f'{over20:.1f} % of the mainloop frames took over 20 ms'
    report = {'status': 'passed', 'frame_callbacks': count_at_stall,
              'mainloop_over20_pct': round(over20, 1),
              'mainloop_median_ms': round(statistics.median(loop_intervals), 2),
              'median_interval_ms': round(statistics.median(intervals), 2),
              'maximum_interval_ms': round(max(intervals), 2),
              'distinct_positions': len(set(round(value, 4) for value in samples)),
              'network_stall_stops_motion': True, 'unknown_progress_animates': True}
    _sandbox.write_report('animation-report.json', report)
    print(json.dumps(report), flush=True)
finally:
    app.destroy()
