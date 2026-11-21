# quick
"""2.0.1 fixes without a window: the collector worker after a failed history write (F3), the proxy kept off
disk (F4), one after-cancel helper (F6) and Library.subscriptions() in one statement (F8).  F1, F2 and the
helper's behaviour are in test_theme.py, F5 in golden/ui.json.

    python -X utf8 -B test_v201_fixes.py
"""
import _sandbox  # noqa: F401  (first: the sandbox)
import base64
import http.server
import json
import os
from pathlib import Path
import re
import socketserver
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import collector
import library
import messages
import paths
from library import Library, _dump
from messages import raw

ROOT = Path(__file__).resolve().parents[1]
PAGE = 'https://example.org/page'
SECRET = 'http://owner:hunter2secret@127.0.0.1:9'


class Case(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.history = self.root / 'history.sqlite3'

    def request(self, **options):
        """request.json of a worker for a new collect job (as CollectionRunner.run writes it)."""
        job = Library(self.history).create_job('collect', raw(PAGE), dict(urls=[PAGE]))
        request = self.root / 'job' / 'request.json'
        request.parent.mkdir(exist_ok=True)
        data = collector.asdict(collector.CollectOptions([PAGE], job_id=job, history_path=str(self.history), **options))
        request.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return job, request

    @staticmethod
    def events(request):
        return [json.loads(line) for line in request.with_suffix('.events').read_text(encoding='utf-8').splitlines()]


def fake_collect(options, emit):
    emit('collect_status', 'status')
    emit('collect_page', {'url': PAGE, 'title': 'T', 'text': 'x', 'markdown': 'x', 'links': [], 'next': None,
                          '_checkpoint': {'visited': [PAGE], 'todo': []}})
    emit('collect_checkpoint', {'visited': [PAGE], 'todo': []})


class StoreFailureTests(Case):
    """(2.0.1 F3) A history write that fails in the worker: the worker stops writing the history, the window
    still gets every event, and the real cause arrives as a collect_error (not the generic worker exit)."""

    def test_a_failed_write_while_collecting_drops_the_store_and_reports_the_cause(self):
        job, request = self.request(proxy=SECRET)
        calls = []

        def locked(self_, job_id, kind, value):
            calls.append(kind)
            raise sqlite3.OperationalError(f'database is locked (via {SECRET})')

        with mock.patch.object(collector, 'collect', fake_collect), \
                mock.patch.object(Library, 'collection_event', locked):
            self.assertEqual(collector.worker_main(str(request)), 0)
        events = self.events(request)
        mask = messages.PROXY_TOKEN
        self.assertEqual([kind for kind, _value in events],
                         ['collect_status', 'collect_page', 'collect_error', 'collect_checkpoint'])
        self.assertEqual(events[1][1]['url'], PAGE, 'the page reaches the window')
        self.assertEqual(events[2][1], f'database is locked (via {mask})', 'the real cause, the proxy masked')
        self.assertEqual(calls, ['collect_page'], 'no history write after the failed one')
        self.assertNotIn('hunter2secret', request.with_suffix('.events').read_text(encoding='utf-8'))

    def test_a_damaged_history_at_the_start_reports_the_cause_and_collects(self):
        job, request = self.request()
        with open(self.history, 'r+b') as file:
            file.write(b'GARBAGE!' * 16)
        with mock.patch.object(collector, 'collect', fake_collect):
            self.assertEqual(collector.worker_main(str(request)), 0)
        events = self.events(request)
        self.assertEqual([kind for kind, _value in events],
                         ['collect_error', 'collect_status', 'collect_page', 'collect_checkpoint'])
        self.assertEqual(events[0][1], 'file is not a database')

    def test_a_locked_history_at_the_start_reports_the_cause_and_collects_without_it(self):
        job, request = self.request()
        written = []
        with mock.patch.object(collector, 'collect', fake_collect), \
                mock.patch.object(Library, 'change_job', side_effect=sqlite3.OperationalError('database is locked')), \
                mock.patch.object(Library, 'collection_event', lambda *arguments: written.append(arguments)):
            self.assertEqual(collector.worker_main(str(request)), 0)
        events = self.events(request)
        self.assertEqual([kind for kind, _value in events],
                         ['collect_error', 'collect_status', 'collect_page', 'collect_checkpoint'])
        self.assertEqual(events[0][1], 'database is locked')
        self.assertEqual(written, [], 'the history is not written after the failed start')

    def test_a_working_history_is_written_as_before(self):
        job, request = self.request()
        with mock.patch.object(collector, 'collect', fake_collect):
            self.assertEqual(collector.worker_main(str(request)), 0)
        self.assertEqual([kind for kind, _value in self.events(request)],
                         ['collect_status', 'collect_page', 'collect_checkpoint'])
        record = Library(self.history).job(job)
        self.assertEqual((record['status'], len(record['pages'])), ('completed', 1))


class ProxyOffDiskTests(Case):
    """(2.0.1 F4) The proxy (it may hold a password) goes to the worker in its environment, never in
    request.json; the worker takes it out of its environment at once."""

    def test_worker_takes_the_proxy_from_its_environment_and_removes_it(self):
        job, request = self.request()
        seen = {}

        def collect(options, emit):
            seen.update(proxy=options.proxy, environment=os.environ.get(collector.PROXY_ENV))

        with mock.patch.dict(os.environ, {collector.PROXY_ENV: SECRET}), mock.patch.object(collector, 'collect', collect):
            self.assertEqual(collector.worker_main(str(request)), 0)
            self.assertNotIn(collector.PROXY_ENV, os.environ)
        self.assertEqual(seen, {'proxy': SECRET, 'environment': None})

    def test_a_real_worker_uses_the_proxy_that_is_written_nowhere(self):
        seen_by_proxy = []

        class Proxy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen_by_proxy.append((self.path, self.headers.get('Proxy-Authorization', '')))
                body = b'<html><head><title>T</title></head><body><main><p>hello</p></main></body></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *arguments):
                pass

        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Proxy)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        secret = f'http://owner:hunter2secret@127.0.0.1:{server.server_address[1]}'
        page = 'http://127.0.0.1:9/page'           # nothing listens there: only the proxy can answer
        store = Library(self.history)
        job = store.create_job('collect', raw(page), dict(urls=[page], proxy=secret))
        options = collector.CollectOptions([page], proxy=secret, job_id=job, history_path=str(self.history))
        started, lines, received = {}, [], []
        real_popen = subprocess.Popen

        def popen(command, **keywords):
            started.update(request=Path(command[-1]).read_text(encoding='utf-8'), env=dict(keywords['env']))
            return real_popen(command, **keywords)

        runner = collector.CollectionRunner(lambda kind, value: received.append((kind, value)))
        forward = runner._forward
        runner._forward = lambda line: (lines.append(line), forward(line))
        with mock.patch.object(subprocess, 'Popen', popen):
            runner.run(options)
        self.assertEqual(json.loads(started['request'])['proxy'], '', 'request.json holds no proxy')
        self.assertNotIn('hunter2secret', started['request'])
        self.assertEqual(started['env'][collector.PROXY_ENV], secret)
        self.assertNotIn(collector.PROXY_ENV, os.environ, 'the window process keeps no copy')
        # The worker used it: the proxy got the request with the credentials.
        self.assertEqual(seen_by_proxy[0][0], page)
        self.assertEqual(seen_by_proxy[0][1], 'Basic ' + base64.b64encode(b'owner:hunter2secret').decode())
        self.assertEqual([kind for kind, _value in received if kind in ('collect_page', 'collect_done')],
                         ['collect_page', 'collect_done'], received)
        self.assertEqual(Library(self.history).job(job)['status'], 'completed')
        # Nowhere on disk or in the events: the history, the state folder (log file included), the IPC lines.
        self.assertTrue(lines)
        self.assertFalse(any(b'hunter2secret' in line for line in lines))
        self.assertNotIn('hunter2secret', repr(received))
        for path in [self.history, *paths.STATE_DIR.rglob('*')]:
            if path.is_file():
                self.assertNotIn(b'hunter2secret', path.read_bytes(), path)

    def test_no_proxy_no_variable(self):
        started = {}

        class Finished:
            pid, returncode, stdout = 0, 0, None

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def popen(command, **keywords):
            started.update(env=keywords['env'])
            return Finished()

        with mock.patch.dict(os.environ, {collector.PROXY_ENV: 'left over'}), mock.patch.object(subprocess, 'Popen', popen):
            collector.CollectionRunner(lambda *_: None).run(collector.CollectOptions(['http://127.0.0.1:9/page']))
        self.assertNotIn(collector.PROXY_ENV, started['env'])


class CancelAfterTests(unittest.TestCase):
    def test_one_after_cancel_helper(self):
        """(2.0.1 F6) Pages cancel their after jobs through ui_theme.cancel_after, not their own copies."""
        found = []
        for path in sorted((ROOT / 'src').glob('*.py')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if re.search(r'\.after_cancel\(|def _cancel(_after)?\(', line):
                    found.append(f'{path.name}:{number}')
        # ui_theme.cancel_after itself; progress.py cancels its own frame id it always holds.
        self.assertEqual([place.split(':')[0] for place in found], ['progress.py', 'ui_theme.py'], found)


class SubscriptionsTests(unittest.TestCase):
    """(2.0.1 F8) Library.subscriptions() reads every subscription and its pending count in one statement,
    with the same rows as the loop of 2.0.0."""

    @staticmethod
    def old_subscriptions(store):
        # 2.0.0 Library.subscriptions(), verbatim but for self -> store
        with store.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM subscriptions ORDER BY rowid')]
            for row in rows:
                row['data'] = library._load(row['data'], 'subscription')
                row['name_text'] = row['name'] if isinstance(row['name'], str) else ''
                row['name'] = raw(row['name_text']) or messages.msg('value.subscription.untitled')
                row['pending'] = db.execute('SELECT count(*) FROM entries WHERE subscription=? AND pending=1',
                                            (row['id'],)).fetchone()[0]
            return rows

    def test_same_rows_in_one_statement(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Library(Path(folder) / 'library.sqlite3')
            options = {'urls': ['https://example.org/c'], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
            keys = [store.subscription(name, options) for name in ('频道 A', '', 'Channel C', 'Empty')]
            with store.tx() as db:
                for number, key in enumerate(keys[:3]):
                    for index in range(7 + number):
                        url = f'https://www.youtube.com/watch?v=s{number}e{index}'
                        db.execute('INSERT INTO entries VALUES(?,?,?,?)',
                                   (key, library.identity(url), int(index % 3 != 0), _dump({'url': url}, 'entry')))
            store.configure_subscription(keys[2], False, 86400)
            self.assertEqual(store.subscriptions(), self.old_subscriptions(store))
            self.assertEqual([row['pending'] for row in store.subscriptions()], [4, 5, 6, 0])
            statements = []
            connect = sqlite3.connect

            def counting(*arguments, **keywords):
                db = connect(*arguments, **keywords)
                db.set_trace_callback(statements.append)
                return db

            with mock.patch.object(library.sqlite3, 'connect', counting):
                rows = store.subscriptions()
            self.assertEqual(len(rows), 4)
            self.assertEqual(len([s for s in statements if s.lstrip().upper().startswith('SELECT')]), 1, statements)


if __name__ == '__main__':
    unittest.main()
