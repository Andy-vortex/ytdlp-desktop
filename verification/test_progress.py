# quick
import _sandbox
import queue
import unittest
from unittest.mock import Mock, patch

from progress import FLOW_PERIOD, ProgressMotion, flow
from studio import StudioApp


class ProgressTests(unittest.TestCase):
    def test_real_progress_is_interpolated_and_stops_at_the_reported_value(self):
        motion = ProgressMotion()
        motion.retarget(.6, 10)
        values = [motion.value(10 + i * .016) for i in range(31)]
        self.assertEqual(values[0], 0)
        self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))
        self.assertGreater(len(set(values)), 10)
        self.assertEqual(motion.value(11), .6)
        self.assertEqual(motion.value(100), .6)
        self.assertTrue(motion.settled(11))

    def test_new_stream_resets_and_small_estimate_changes_can_follow_backwards(self):
        motion = ProgressMotion()
        motion.retarget(.95, 10, immediate=True)
        motion.retarget(.05, 11)
        self.assertEqual(motion.value(11), .05)
        motion.retarget(.5, 12, immediate=True)
        motion.retarget(.46, 13)
        self.assertAlmostEqual(motion.value(13), .5)
        self.assertAlmostEqual(motion.value(14), .46)
        motion.retarget(0, 15, immediate=True)
        self.assertEqual(motion.value(15), 0)

    def test_repeated_target_does_not_restart_the_easing(self):
        motion = ProgressMotion()
        motion.retarget(.5, 10)
        motion.retarget(.5, 10.2)
        self.assertEqual(motion.value(10.3), .5)
        motion.retarget(float('nan'), 11)
        self.assertEqual(motion.target, .5)

    # --- 2.0.3: an even glide that lasts LEAD times the report interval, and the one-way indeterminate run ---

    def test_glide_lasts_lead_times_the_report_interval(self):
        motion = ProgressMotion()
        motion.retarget(.1, 10)
        motion.retarget(.2, 10.8)              # 0.8 s apart: a 1.0 s glide (1 s apart would hit LONGEST)
        self.assertAlmostEqual(motion.duration, 1.0)
        self.assertAlmostEqual(motion.value(10.8 + .5), .15)
        self.assertFalse(motion.settled(10.8 + .95))
        self.assertTrue(motion.settled(10.8 + 1.0 + .01))
        self.assertEqual(motion.value(10.8 + 1.0 + .01), .2)

    def test_glide_is_clamped(self):
        motion = ProgressMotion()
        motion.retarget(.1, 10)
        motion.retarget(.2, 10.05)
        self.assertAlmostEqual(motion.duration, .24)
        motion.retarget(.3, 20.05)
        self.assertAlmostEqual(motion.duration, 1.2)
        self.assertFalse(motion.settled(21.1))
        self.assertTrue(motion.settled(21.3))

    def test_glide_never_passes_the_report(self):
        motion = ProgressMotion()
        values = []
        for report in range(1, 9):
            now = 10.0 + report
            motion.retarget(report * .08, now)
            if report >= 3:
                for step in range(63):          # every 16 ms until just before the next report
                    value = motion.value(now + step * .016)
                    self.assertLessEqual(value, motion.target)
                    values.append(value)
        self.assertTrue(all(a < b for a, b in zip(values, values[1:])), 'the glide never stops between reports')

    def test_new_stream_forgets_the_interval(self):
        motion = ProgressMotion()
        motion.retarget(.1, 10)
        motion.retarget(.2, 11)
        motion.retarget(.9, 12, immediate=True)
        motion.retarget(.95, 13)
        self.assertAlmostEqual(motion.duration, .24)
        motion.retarget(.96, 14)
        self.assertAlmostEqual(motion.duration, 1.2)
        motion.retarget(.1, 20)                  # a drop of 0.15 or more is a new file
        self.assertEqual(motion.value(20), .1)
        motion.retarget(.2, 21)
        self.assertAlmostEqual(motion.duration, .24)

    def test_real_report_times_keep_moving(self):
        # Progress handled by the real program on the first fixture link (measure-frames, app-normal-trace).
        reports = [(0.0, .008), (.397, .053), (.893, .114), (1.887, .235), (2.911, .356), (3.906, .476),
                   (4.902, .596), (5.923, .716), (6.792, .835), (7.926, .971)]
        motion = ProgressMotion()
        samples, fed, index, step = [], 0.0, 0, 0
        while step / 60 < reports[-1][0]:
            now = step / 60
            while index < len(reports) and reports[index][0] <= now:
                motion.retarget(reports[index][1], reports[index][0])
                fed = max(fed, reports[index][1])
                index += 1
            value = motion.value(now)
            self.assertLessEqual(value, fed)
            if index >= 4:
                samples.append(value)
            step += 1
        still = sum(a == b for a, b in zip(samples, samples[1:])) / (len(samples) - 1)
        self.assertLessEqual(still, .03, f'{still:.1%} of the samples did not move')

    def test_flow_is_one_way_and_clipped(self):
        span = 1000.0
        steps = [i * FLOW_PERIOD / 360 for i in range(360)]
        runs = [flow(elapsed, span) for elapsed in steps]
        for start, end in runs:
            self.assertTrue(0 <= start <= end <= span, (start, end))
        ends = [end for _start, end in runs]
        entering = ends[:ends.index(span)]
        self.assertTrue(entering and all(a <= b for a, b in zip(entering, entering[1:])))
        middle = [start for start, end in runs if 0 < start and end < span]
        moves = [b - a for a, b in zip(middle, middle[1:])]
        self.assertTrue(len(moves) > 10 and max(moves) - min(moves) < 1e-9, moves)
        for elapsed in (0.0, .3, .9, 1.5):
            for got, want in zip(flow(elapsed + FLOW_PERIOD, span), flow(elapsed, span)):
                self.assertAlmostEqual(got, want, places=9)

    def test_progress_coalescing_preserves_task_boundaries(self):
        fake = Mock()
        fake.events = queue.Queue()
        fake.closing = False
        for event in [('progress', {'fraction': .1}), ('progress', {'fraction': .2}),
                      ('item', {'index': 2}), ('progress', {'fraction': .3}),
                      ('done', {'cancelled': True})]:
            fake.events.put(event)
        with patch('studio.time.perf_counter', return_value=0):
            StudioApp.poll(fake)
        self.assertEqual(fake.handle.call_args_list,
                         [unittest.mock.call('progress', {'fraction': .2}),
                          unittest.mock.call('item', {'index': 2}),
                          unittest.mock.call('progress', {'fraction': .3}),
                          unittest.mock.call('done', {'cancelled': True})])

    # --- the time budget of StudioApp.poll (fix round 1; test_poll_order tests the order of a tick) ---

    def poll_once(self, events, clock):
        """One poll() on a stand-in app; returns (handle calls, events left in the queue)."""
        fake = Mock()
        fake.events = queue.Queue()
        fake.closing = False
        for event in events:
            fake.events.put(event)
        with patch('studio.time.perf_counter', side_effect=clock):
            StudioApp.poll(fake)
        left = []
        while not fake.events.empty():
            left.append(fake.events.get_nowait())
        return fake.handle.call_args_list, left

    def test_poll_stops_when_its_five_millisecond_budget_is_used_up(self):
        events = [('item', {'index': number}) for number in range(1, 8)]
        # The clock reads 100.000 s when the deadline is set (budget 5 ms -> 100.005 s), then once
        # after every event: four looks inside the budget, the fifth one past it.
        ticks = iter([100.0, 100.001, 100.002, 100.003, 100.004, 100.006, 100.007, 100.008])
        handled, left = self.poll_once(events, lambda: next(ticks))
        self.assertEqual(handled, [unittest.mock.call(*event) for event in events[:5]])
        self.assertEqual(left, events[5:])

    def test_poll_handles_one_event_when_the_first_one_uses_the_whole_budget(self):
        looks = []

        def clock():
            looks.append(True)
            return 0 if len(looks) == 1 else 1      # the first handler took a whole second

        events = [('item', {'index': 1}), ('item', {'index': 2}), ('done', {'cancelled': False})]
        handled, left = self.poll_once(events, clock)
        self.assertEqual(handled, [unittest.mock.call('item', {'index': 1})])
        self.assertEqual(left, events[1:])
        self.assertEqual(len(looks), 2, 'the clock is read once for the deadline and once per event')

    def test_poll_flushes_the_pending_progress_when_the_budget_ends_on_it(self):
        looks = []

        def clock():
            looks.append(True)
            return 0 if len(looks) <= 2 else 1      # time is up after the second progress event

        events = [('progress', {'fraction': .1}), ('progress', {'fraction': .2}),
                  ('progress', {'fraction': .3}), ('item', {'index': 2})]
        handled, left = self.poll_once(events, clock)
        self.assertEqual(handled, [unittest.mock.call('progress', {'fraction': .2})])
        self.assertEqual(left, events[2:])

    def test_poll_takes_at_most_200_events_per_call(self):
        events = [('file', f'{number}.wav') for number in range(250)]
        handled, left = self.poll_once(events, lambda: 0)
        self.assertEqual(handled, [unittest.mock.call(*event) for event in events[:200]])
        self.assertEqual(left, events[200:])


if __name__ == '__main__':
    unittest.main()
