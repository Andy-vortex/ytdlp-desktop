# tier: exe
"""The packaged collect worker, reached through the in-app path (1.6.0 WP6 §D).

The 1.5.x test started the exe directly, so CollectionRunner.run (worker environment, per-run TEMP, job object,
clean-up) never ran against the packaged program.  Here ``collector.CollectionRunner(emit).run(options)`` runs in
this process with ``sys.frozen = True`` and ``sys.executable = VERIFY_EXE`` patched for the duration of the call,
so the runner starts ``<exe> --collect-worker <job>`` exactly as the installed program does:
  * static page, and the dynamic (Chrome) page when Chrome is installed;
  * stop during a slow page: the worker's process tree is gone within 8 s, the last event is
    collect_done {'cancelled': True};
  * stop after a durable page commit, then a fresh run resumes from the stored checkpoint without a duplicate page;
  * afterwards paths.collect_dir() holds no run-* folder.
The quotes.toscrape.com case runs only with VERIFY_NET=1.  Outputs go to _sandbox.out('packaged-collection').
"""
import _sandbox
import ctypes
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest import mock

if not os.environ.get('VERIFY_EXE'):
    print('SKIPPED: no --exe', flush=True)
    raise SystemExit(0)

import paths
from collector import CollectOptions, CollectionRunner, browser_available
from library import Library

EXE = Path(os.environ['VERIFY_EXE'])
OUT = _sandbox.out('packaged-collection')
OUT.mkdir(parents=True, exist_ok=True)
STOP_LIMIT = 8.0


class Site(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/slow':
            time.sleep(20)
        elif self.path == '/later':
            time.sleep(6)
        html = '<html><title>打包测试</title><main><h1>打包正文</h1><a href="/file.mp4">媒体</a></main>'
        if self.path == '/js':
            html += ('<script>setTimeout(()=>document.querySelector("main").innerHTML+='
                     '\'<p>动态正文打包成功</p><a href="/late.mp4">延迟链接</a>\',100)</script>')
        data = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A stopped worker drops its connection; that is not a test failure.
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


@contextmanager
def packaged():
    """The runner believes it is the packaged program: the worker command is [VERIFY_EXE, '--collect-worker', job]."""
    with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(sys, 'executable', str(EXE)):
        yield


def descendants(root_pid) -> set:
    """Process ids of root_pid and everything below it (toolhelp snapshot)."""
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    parents = {}
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    found = {root_pid} if root_pid in parents else set()
    grew = True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in found and pid not in found and pid != parent:
                found.add(pid)
                grew = True
    return found


def alive(pid) -> bool:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def collect(options, timeout=120):
    """One synchronous CollectionRunner.run through the packaged worker; returns the events."""
    events = []
    runner = CollectionRunner(lambda kind, value: events.append((kind, value)))
    with packaged():
        thread = threading.Thread(target=runner.run, args=(options,), daemon=True)
        thread.start()
        thread.join(timeout)
    assert not thread.is_alive(), f'CollectionRunner.run did not return within {timeout} s'
    assert events and events[-1][0] == 'collect_done', events
    return events


def started_runner(options):
    """Start CollectionRunner.run on a thread (patched for its whole life); returns (runner, thread, events, patch)."""
    events = []
    runner = CollectionRunner(lambda kind, value: events.append((kind, value)))
    patch = packaged()
    patch.__enter__()
    thread = threading.Thread(target=runner.run, args=(options,), daemon=True)
    thread.start()
    return runner, thread, events, patch


def wait_for(condition, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def pages(events):
    return [value for kind, value in events if kind == 'collect_page']


def errors(events):
    return [value for kind, value in events if kind == 'collect_error']


server = Server(('127.0.0.1', 0), Site)
base = f'http://127.0.0.1:{server.server_port}'
threading.Thread(target=server.serve_forever, daemon=True).start()
report = {'exe': str(EXE)}
try:
    assert EXE.is_file(), EXE
    store = Library(OUT / 'packaged-library.sqlite3')
    chrome = browser_available()
    report['chrome_installed'] = chrome

    # 1. static (with history, as the app passes it) and dynamic page
    cases = [('static', base + '/', False)]
    if chrome:
        cases.append(('dynamic', base + '/js', True))
    else:
        report['dynamic'] = {'status': 'skipped', 'reason': 'Google Chrome is not installed'}
    if os.environ.get('VERIFY_NET') == '1':     # the only real website; never part of --all or --exe alone
        cases.append(('public_https', 'https://quotes.toscrape.com/', False))
    for name, url, dynamic in cases:
        job_id = store.create_job('collect', name, {'urls': [url], 'dynamic': dynamic})
        options = CollectOptions([url], dynamic=dynamic, scrolls=0, max_pages=1, job_id=job_id,
                                 history_path=str(store.path))
        started = time.monotonic()
        events = collect(options)
        assert not errors(events), (name, events)
        assert events[-1] == ('collect_done', {'cancelled': False}), events
        found = pages(events)
        assert len(found) == 1, (name, events)
        page = found[0]
        assert page['text'] and page['links'], page
        if url.startswith(base):
            assert '打包正文' in page['text'] and any(item['url'] == base + '/file.mp4' for item in page['links']), page
        if dynamic:
            assert '动态正文打包成功' in page['text'], page
            assert any(item['url'].endswith('/late.mp4') for item in page['links']), page
        record = Library(store.path).job(job_id)
        assert record['status'] == 'completed' and len(record['pages']) == 1, record['status']
        assert record['data']['checkpoint']['todo'] == [], record['data']['checkpoint']
        report[name] = {'status': 'passed', 'title': page['title'], 'links': len(page['links']),
                        'seconds': round(time.monotonic() - started, 2)}

    # 2. stop during a slow page: the whole worker tree is gone within STOP_LIMIT seconds
    options = CollectOptions([base + '/slow'], dynamic=chrome)
    runner, thread, events, patch = started_runner(options)
    try:
        wait_for(lambda: runner.process is not None, 30, 'the packaged worker did not start')
        time.sleep(5 if chrome else 2)          # let the worker (and Chrome) get going
        worker = runner.process
        tree = descendants(worker.pid)
        assert worker.pid in tree, 'the worker is not running'
        started = time.monotonic()
        runner.cancel()
        thread.join(STOP_LIMIT)
        wait_for(lambda: not any(alive(pid) for pid in tree), max(0.1, STOP_LIMIT - (time.monotonic() - started)),
                 f'processes of the stopped worker still alive: {[pid for pid in tree if alive(pid)]}')
        seconds = round(time.monotonic() - started, 3)
        assert not thread.is_alive(), 'CollectionRunner.run did not return after cancel()'
        assert seconds <= STOP_LIMIT, seconds
        assert events[-1] == ('collect_done', {'cancelled': True}), events
        assert runner.process is None
    finally:
        patch.__exit__(None, None, None)
    report['stop_packaged_worker_process_tree'] = {'status': 'passed', 'seconds': seconds,
                                                   'processes': len(tree), 'dynamic': chrome}

    # 3. stop after a durable page commit, then resume from the stored checkpoint in a fresh run
    urls = [base + '/file.mp4', base + '/later']
    job_id = store.create_job('collect', '打包续采', {'urls': urls})
    options = CollectOptions(urls, max_pages=1, job_id=job_id, history_path=str(store.path))
    runner, thread, events, patch = started_runner(options)
    try:
        wait_for(lambda: len(store.job(job_id)['pages']) == 1, 40,
                 'the first page was not committed by the packaged worker')
        runner.cancel()
        thread.join(STOP_LIMIT)
        assert not thread.is_alive()
        assert events[-1] == ('collect_done', {'cancelled': True}), events
    finally:
        patch.__exit__(None, None, None)
    record = Library(store.path).job(job_id)
    assert record['status'] == 'interrupted', record['status']
    checkpoint = record['data']['checkpoint']
    assert checkpoint['todo'] and checkpoint['todo'][0]['url'] == base + '/later', checkpoint
    store.change_job(job_id, status='running', errors=[])
    resumed = CollectOptions(urls, max_pages=1, resume=checkpoint, job_id=job_id, history_path=str(store.path))
    events = collect(resumed)
    assert not errors(events), events
    assert events[-1] == ('collect_done', {'cancelled': False}), events
    assert [page['url'] for page in pages(events)] == [base + '/later'], pages(events)
    record = Library(store.path).job(job_id)
    assert record['status'] == 'completed' and len(record['pages']) == 2, (record['status'], len(record['pages']))
    report['stop_restart_persistent_resume_without_duplicate_page'] = {'status': 'passed'}

    # 4. nothing of the runs is left in the collect folder
    left = sorted(item.name for item in paths.collect_dir().iterdir() if item.name.startswith('run-')) \
        if paths.collect_dir().is_dir() else []
    assert not left, f'run folders left in {paths.collect_dir()}: {left}'
    report['collect_dir_clean'] = {'status': 'passed', 'folder': str(paths.collect_dir())}
    report['status'] = 'passed'
except Exception:
    import traceback
    report.update(status='failed', traceback=traceback.format_exc())
    raise
finally:
    server.shutdown()
    server.server_close()
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
