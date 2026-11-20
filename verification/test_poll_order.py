# quick
"""The event loop tick StudioApp.poll (2.0.0 stage S0).

    python -X utf8 -B test_poll_order.py

Replaces the SHA-256 pin of StudioApp.poll (test_protected_blocks.py, test_i18n_download.py) with
its behaviour, on a stand-in app (the handlers, the log flush, the wait render and after() are
mocks; the queue is a real queue.Queue):

  order        events are handled in queue order; consecutive progress events collapse into the
               last one, but never across another event (progress, log, progress -> three calls);
               a pending progress is handled before the next other event and at the end of the tick
  limits       at most 200 events per tick; the clock is read once for a 5 ms deadline and once
               after each event, and the tick ends at the first reading at or past the deadline
  failures     a handler that raises ends the tick: the events after it stay queued and are
               handled by the next tick; the failing event is not handled again; the failure is
               logged; a failing progress flush, log flush or wait render is logged too; the tick
               is scheduled again (after(25, poll)) in every one of these cases; (2.0.0 S2 P1) a
               progress handler that raises while the pending progress is flushed before another
               event loses that other event and is tried again at the end of the tick (pinned as is)
  every tick   flush_log and render_wait run, also with an empty queue
  closing      closing and idle -> finish_close() and no new tick; closing while busy, or not
               closing -> after(25, poll)

test_progress.py keeps the original budget tests; test_ui_state.py the real-window ones.
"""
import _sandbox
import queue
import unittest
from unittest import mock

from studio import StudioApp


def stand_in(events=(), closing=False, busy=False):
    app = mock.Mock()
    app.events = queue.Queue()
    app.closing, app.busy = closing, busy
    for event in events:
        app.events.put(event)
    return app


def left(app):
    found = []
    while not app.events.empty():
        found.append(app.events.get_nowait())
    return found


def tick(app, clock=lambda: 0):
    with mock.patch('studio.time.perf_counter', side_effect=clock):
        StudioApp.poll(app)


P1, P2, P3, P4 = (('progress', {'fraction': value}) for value in (.1, .2, .3, .4))
LOG = ('log', 'a line')


class OrderTests(unittest.TestCase):
    def test_progress_log_progress_is_handled_in_that_order(self):
        app = stand_in([P1, LOG, P2])
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*P1), mock.call(*LOG), mock.call(*P2)])
        self.assertEqual(left(app), [])

    def test_runs_of_progress_collapse_into_their_last_event_only(self):
        app = stand_in([P1, P2, LOG, P3, P4, ('item', {'index': 2}), P1])
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*P2), mock.call(*LOG), mock.call(*P4),
                                                     mock.call('item', {'index': 2}), mock.call(*P1)])

    def test_only_progress(self):
        app = stand_in([P1, P2, P3])
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*P3)])

    def test_an_empty_queue_still_flushes_and_reschedules(self):
        app = stand_in()
        tick(app)
        app.handle.assert_not_called()
        app.flush_log.assert_called_once_with()
        app.render_wait.assert_called_once_with()
        app.after.assert_called_once_with(25, app.poll)
        app.finish_close.assert_not_called()


class LimitTests(unittest.TestCase):
    def test_at_most_200_events_per_tick(self):
        events = [('file', f'{number}.wav') for number in range(201)]
        app = stand_in(events)
        tick(app)
        self.assertEqual(app.handle.call_count, 200)
        self.assertEqual(left(app), events[200:])
        app.after.assert_called_once_with(25, app.poll)

    def test_the_deadline_is_five_milliseconds_after_the_first_reading(self):
        events = [('item', {'index': number}) for number in range(6)]
        readings = iter([10.0, 10.001, 10.0049, 10.005, 10.006, 10.007, 10.008])
        app = stand_in(events)
        tick(app, lambda: next(readings))
        # deadline 10.005: after events 0 and 1 the clock is below it, after event 2 it reads 10.005
        self.assertEqual(app.handle.call_args_list, [mock.call(*event) for event in events[:3]])
        self.assertEqual(left(app), events[3:])

    def test_the_pending_progress_is_handled_when_the_budget_ends_on_it(self):
        readings = iter([0, 0, 1])
        app = stand_in([LOG, P1, P2])
        tick(app, lambda: next(readings))
        self.assertEqual(app.handle.call_args_list, [mock.call(*LOG), mock.call(*P1)])
        self.assertEqual(left(app), [P2])


class FailureTests(unittest.TestCase):
    def test_a_failing_handler_ends_the_tick_and_the_rest_waits_for_the_next(self):
        first, broken, third = ('log', 'one'), ('log', 'two'), ('log', 'three')
        app = stand_in([first, broken, third])
        app.handle.side_effect = lambda kind, value: (_ for _ in ()).throw(RuntimeError('boom')) \
            if value == 'two' else None
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*first), mock.call(*broken)])
        self.assertEqual(left(app), [third])
        app.logger.exception.assert_called_once()
        app.flush_log.assert_called_once_with()
        app.render_wait.assert_called_once_with()
        app.after.assert_called_once_with(25, app.poll)
        app.events.put(third)
        app.handle.reset_mock()
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*third)], 'handled by the next tick, the failed one never again')

    def test_a_failing_event_after_a_progress_run(self):
        app = stand_in([P1, P2, ('done', {'cancelled': False}), P3])
        app.handle.side_effect = lambda kind, value: (_ for _ in ()).throw(RuntimeError('boom')) \
            if kind == 'done' else None
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*P2), mock.call('done', {'cancelled': False})])
        self.assertEqual(left(app), [P3])
        app.after.assert_called_once_with(25, app.poll)

    def test_a_failing_progress_flush_before_another_event_drops_that_event(self):
        """(2.0.0 S2 P1) Pins the current behaviour (S0.md section 7): the progress handler raises while
        the pending progress is flushed before the next other event.  That event was taken from the
        queue and is never handled; the tick ends; the pending progress is tried once more at the
        end of the tick (and fails again: two failures logged); the events after it stay queued."""
        app = stand_in([P1, LOG, P2])
        app.handle.side_effect = lambda kind, value: (_ for _ in ()).throw(RuntimeError('boom'))             if kind == 'progress' else None
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*P1), mock.call(*P1)],
                         'the log line is lost; the pending progress is tried again at the end')
        self.assertEqual(left(app), [P2])
        self.assertEqual(app.logger.exception.call_count, 2)
        app.flush_log.assert_called_once_with()
        app.render_wait.assert_called_once_with()
        app.after.assert_called_once_with(25, app.poll)

    def test_a_failing_progress_flush_at_the_end_is_logged_and_the_tick_goes_on(self):
        app = stand_in([LOG, P1])
        app.handle.side_effect = lambda kind, value: (_ for _ in ()).throw(RuntimeError('boom')) \
            if kind == 'progress' else None
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*LOG), mock.call(*P1)])
        app.logger.exception.assert_called_once()
        app.flush_log.assert_called_once_with()
        app.after.assert_called_once_with(25, app.poll)

    def test_a_failing_log_flush_or_wait_render_still_reschedules(self):
        for name in ('flush_log', 'render_wait'):
            with self.subTest(name=name):
                app = stand_in([LOG])
                getattr(app, name).side_effect = RuntimeError('tick failed')
                tick(app)
                app.handle.assert_called_once_with(*LOG)
                app.logger.exception.assert_called_once()
                app.after.assert_called_once_with(25, app.poll)
                if name == 'flush_log':
                    app.render_wait.assert_not_called()

    def test_a_failure_while_closing_still_finishes_the_close(self):
        app = stand_in([LOG], closing=True)
        app.handle.side_effect = RuntimeError('boom')
        app.render_wait.side_effect = RuntimeError('tick failed')
        tick(app)
        app.finish_close.assert_called_once_with()
        app.after.assert_not_called()


class ClosingTests(unittest.TestCase):
    def test_closing_and_idle_finishes_the_close_instead_of_a_new_tick(self):
        app = stand_in([LOG, P1], closing=True)
        tick(app)
        self.assertEqual(app.handle.call_args_list, [mock.call(*LOG), mock.call(*P1)], 'the queue is handled first')
        app.finish_close.assert_called_once_with()
        app.after.assert_not_called()

    def test_closing_while_busy_keeps_ticking(self):
        app = stand_in(closing=True, busy=True)
        tick(app)
        app.finish_close.assert_not_called()
        app.after.assert_called_once_with(25, app.poll)

    def test_not_closing_keeps_ticking(self):
        app = stand_in(closing=False, busy=False)
        tick(app)
        app.finish_close.assert_not_called()
        app.after.assert_called_once_with(25, app.poll)


if __name__ == '__main__':
    unittest.main()
