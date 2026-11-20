"""The yt-dlp engine (1.6.0): which exe runs, its version, the release check, the updater.

Imports paths, procutil and applog; never engine.  The only hosts ever contacted are
api.github.com (release check) and the official release asset under
https://github.com/yt-dlp/yt-dlp/releases/download/ .
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time

import applog
from messages import message_of, msg, raw
import paths
import procutil

RELEASE_URL = 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest'
ASSET_PREFIX = 'https://github.com/yt-dlp/yt-dlp/releases/download/'
ASSET_NAME = 'yt-dlp.exe'
USER_AGENT = 'YtDlpDesktop/1.0'
MAX_SIZE = 150 * 1024 * 1024
BLOCK = 256 * 1024
NETWORK_TIMEOUT = 25
PROBE_TIMEOUT = 60
PROBE_GRACE = 10               # after a probe was killed: how long its output pipes may take to close

INSTALL_RETRY = 3.0            # seconds os.replace is repeated while another program holds a file
INSTALL_PAUSE = 0.1
# (1.7.0, c5-updater a) The new engine must first run normally: "--version" ends with exit code 0 and
# prints its version (yt-dlp's 2026.10.21 or a nightly's 2026.10.21.123456) as the first line.
VERSION_LINE = re.compile(r'^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$')
# (1.7.0, c5-updater c) 恢复内置引擎 when the updated engine is still the one in use afterwards.
RESTORE_RETRY = 1.0            # seconds a delete is repeated while another program holds the file

_cache = None                  # (key, path): the last engine_path() answer for an updated engine
_cache_lock = threading.Lock()
_serial = itertools.count(1)   # makes every run's temp file name unique in this process
# (1.8.0, C-F43 / C-H-18) engine_version() when the version is unknown: equal to '已内置', so
# `version == engine_version()` and version_newer compare as before; the UI shows it with show().
BUILTIN = msg('core.update.builtin')


class UpdateError(Exception):
    """An update that was refused or failed; str() is a Chinese message for the user (1.8.0: a
    messages.Message that keeps its key, so the UI shows it in the running language)."""


def _stamp(path):
    status = os.stat(path)
    return status.st_size, status.st_mtime_ns


def engine_path() -> Path:
    """The updated engine when its SHA256 matches its receipt, else the bundled one.

    The answer is cached on (size, mtime) of the updated exe and of the receipt, so the
    exe is hashed again only after one of the two files changed.
    """
    global _cache
    bundled = paths.VENDOR / 'yt-dlp.exe'
    updated = paths.engines_dir() / 'yt-dlp.exe'
    receipt = updated.with_suffix('.json')
    try:
        key = (str(updated), _stamp(updated), _stamp(receipt), str(bundled))
        with _cache_lock:
            cached = _cache
        if cached is not None and cached[0] == key:
            return cached[1]
        metadata = json.loads(paths.read_text(receipt))
        if not isinstance(metadata, dict):
            raise ValueError('the receipt is not an object')
        expected = metadata['sha256']
        digest = hashlib.sha256()
        with open(updated, 'rb') as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        result = updated if digest.hexdigest() == expected else bundled
        with _cache_lock:
            _cache = (key, result)
        return result
    except (OSError, ValueError, KeyError):
        return bundled


def engine_version() -> str:
    try:
        if engine_path() != paths.VENDOR / 'yt-dlp.exe':
            return json.loads(paths.read_text(paths.engines_dir() / 'yt-dlp.json'))['version']
        return json.loads((paths.VENDOR / 'manifest.json').read_text(encoding='utf-8'))[0]['version']
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return BUILTIN


def invalidate() -> None:
    """Forget the cached engine_path() answer."""
    global _cache
    with _cache_lock:
        _cache = None


def _network_errors() -> tuple:
    import http.client
    return OSError, http.client.HTTPException


def _reason(error):
    """A short reason for the network message (no URL, one line): the HTTP code or the system's
    text (raw), or the timeout message."""
    import urllib.error
    if isinstance(error, urllib.error.HTTPError):
        return raw(f'HTTP {error.code}')
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, TimeoutError):
        return msg('core.update.timeout')
    text = ' '.join(str(cause).split()) or type(cause).__name__
    return raw(text[:80])


def _file_reason(error):
    """Why a file operation failed: the system's own words (raw), without the paths str(error) adds."""
    return raw(' '.join(str(getattr(error, 'strerror', None) or type(error).__name__).split())[:80])


def check_latest(timeout=NETWORK_TIMEOUT) -> dict:
    """Ask GitHub for the latest stable release: {'tag', 'url', 'size', 'sha256'}.

    'url' is '' (and 'size' 0) when the release has no yt-dlp.exe, 'sha256' is '' when
    GitHub gives no digest; the updater turns those into its own messages.  Raises
    UpdateError when GitHub cannot be reached or the answer is not a release.
    """
    import urllib.request
    request = urllib.request.Request(RELEASE_URL, headers={'User-Agent': USER_AGENT,
                                                           'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            release = json.load(response)
    except ValueError as error:
        raise UpdateError(msg('core.update.bad_release')) from error
    except _network_errors() as error:
        raise UpdateError(msg('core.update.no_github', reason=_reason(error))) from error
    tag = release.get('tag_name') if isinstance(release, dict) else None
    if not isinstance(tag, str) or not tag.strip():
        raise UpdateError(msg('core.update.bad_release'))
    assets = release.get('assets')
    asset = next((item for item in assets if isinstance(item, dict) and item.get('name') == ASSET_NAME),
                 None) if isinstance(assets, list) else None
    if asset is None:
        return {'tag': tag, 'url': '', 'size': 0, 'sha256': ''}
    url, size, digest = asset.get('browser_download_url'), asset.get('size'), asset.get('digest')
    expected = (digest if isinstance(digest, str) else '').removeprefix('sha256:')
    return {'tag': tag, 'url': url if isinstance(url, str) else '',
            'size': size if isinstance(size, int) and not isinstance(size, bool) else 0,
            'sha256': expected.strip().lower()}


def cleanup() -> None:
    """Remove download leftovers of earlier runs from the engines folder; never raises.

    'yt-dlp.download.<pid>.<n>.exe' of a process that is gone;
    the file of an update that is running now is left alone (paths.purge_engine_downloads).
    """
    paths.purge_engine_downloads(procutil.alive)


def _delete_engine_file(path):
    """One try to delete ``path``; a read-only file loses its read-only attribute first (the owner
    asked for the delete).  None when the file is gone afterwards, else the OSError."""
    try:
        path.unlink()
        return None
    except FileNotFoundError:
        return None
    except PermissionError as error:
        try:
            if os.path.isfile(path) and not os.path.islink(path) and not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWRITE)
                path.unlink()
                return None
        except FileNotFoundError:
            return None
        except OSError as again:
            return again
        return error
    except OSError as error:
        return error


def _remove_updated() -> bool:
    """Delete the updated exe and its receipt; True when the bundled engine is the one in use
    afterwards (engine_path()).  Never raises.

    Either file gone is enough: without its receipt the updated exe is never used, and a receipt
    without its exe neither.  Only when both stay, a file another program holds for a moment
    (sharing violation) is tried again, for RESTORE_RETRY seconds in all; a running exe (access
    denied, WinError 5) is not, since it stays held until that program ends.  restore_bundled runs
    on the window's thread (1.7.0 fix round 1): restoring while a download runs on the updated
    engine no longer blocks it for a second per file."""
    folder = paths.engines_dir()
    names = ('yt-dlp.json', 'yt-dlp.exe')   # the receipt first
    deadline = time.monotonic() + RESTORE_RETRY
    while True:
        errors = {}
        for name in names:
            error = _delete_engine_file(folder / name)
            if error is not None:
                errors[name] = error
        if len(errors) < len(names):
            break
        passing = [error for error in errors.values()
                   if isinstance(error, PermissionError) and getattr(error, 'winerror', None) != 5]
        if not passing or time.monotonic() >= deadline:
            break
        time.sleep(INSTALL_PAUSE)
    for name, error in errors.items():
        applog.get('updater').warning('could not delete %s: %s', name, error)
    invalidate()
    try:
        return engine_path() == paths.VENDOR / 'yt-dlp.exe'
    except Exception:
        return False


def restore_bundled() -> bool:
    """Go back to the engine that ships with the app: delete the updated exe and its receipt.

    Returns True when the bundled engine is really the one in use afterwards (an exe that cannot be
    deleted now is out of use once its receipt is gone).  When both stay (held by another program,
    read-only and not changeable), the updated engine is still used: UpdateError(core.update.restore_failed), so
    that the caller never reports a restore that did not happen (c5-updater c)."""
    if not _remove_updated():
        raise UpdateError(msg('core.update.restore_failed'))
    return True


def _probe_output(command, timeout) -> bytes:
    """Everything an engine prints when it is started with ``command`` (the app's options, no URL):
    stdout, a line break and stderr of _engine_run (the exit code is not part of it)."""
    _code, output, errors = _engine_run(command, timeout)
    return output + b'\n' + errors


def _version_output(exe, timeout):
    """(exit code, stdout, stderr) of '<exe> --ignore-config --version' (_engine_run)."""
    return _engine_run([str(exe), '--ignore-config', '--version'], timeout)


def _engine_run(command, timeout):
    """(exit code, stdout, stderr) of an engine started with ``command``.

    Raises OSError when it does not start and subprocess.TimeoutExpired when it does not
    end in time.  A onefile engine is two processes that share the output pipes: after a
    timeout the whole tree is ended (procutil.terminate_tree), because killing the first
    process alone would leave this call reading until the second one ends by itself.
    The engine unpacks itself into TEMP; it gets a private folder, removed afterwards.
    """
    environment = procutil.process_environment()
    run_tmp = None
    try:
        run_tmp = str(paths.run_dir(paths.tmp_dir()))
        environment.update(TEMP=run_tmp, TMP=run_tmp)
    except OSError:
        run_tmp = None
    job = procutil.JobObject()
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, cwd=str(paths.BIN), env=environment,
                                   creationflags=procutil.HIDDEN)
        job.assign(process)
        try:
            output, errors = process.communicate(timeout=timeout)
        except BaseException:
            procutil.terminate_tree(process, job)
            try:
                process.communicate(timeout=PROBE_GRACE)   # the pipes are closed now: collect the readers
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass   # something outside the tree still holds a pipe; it is not waited for
            raise
        return process.returncode, output or b'', errors or b''
    finally:
        job.close()
        if run_tmp:
            shutil.rmtree(run_tmp, ignore_errors=True)


class EngineUpdater:
    """Download, verify and install the latest yt-dlp.exe.

    emit(kind, value): 'update_status' (str), 'update_progress' ({fraction, downloaded,
    total}), 'updated' ({version, unchanged}), 'update_error' (str), 'update_done'
    ({cancelled}; always last).
    cancelled: a threading.Event; once it is set nothing is installed.
    probe_args: the yt-dlp options the app uses (no exe, no URL); the new exe must accept
    them all, or it is refused.
    """

    def __init__(self, emit, cancelled, probe_args=None):
        self.emit = emit
        self.cancelled = cancelled
        self.probe_args = [str(word) for word in probe_args] if probe_args else None

    def _status(self, text):
        self.emit('update_status', text)

    def _progress(self, received, total):
        self.emit('update_progress', {'fraction': received / total, 'downloaded': received, 'total': total})

    def _probe(self, exe):
        """Refuse an engine that does not run normally (_check_start), then one that does not know
        one of the options this app passes."""
        self._check_start(exe)
        try:
            output = _probe_output([str(exe), *self.probe_args], PROBE_TIMEOUT).decode('utf-8', errors='replace')
        except subprocess.TimeoutExpired as error:
            applog.get('updater').warning('the new engine did not finish the option check within %s s', error.timeout)
            raise UpdateError(msg('core.update.incompatible')) from error
        except OSError as error:
            applog.get('updater').warning('the new engine could not be started for the option check: %s', error)
            raise UpdateError(msg('core.update.incompatible')) from error
        # A good and a bad option set both end with exit code 2 (no URL given); only the text differs.
        if 'no such option' in output.lower():
            applog.get('updater').warning('the new engine rejects an option: %s', ' '.join(output.split())[:300])
            raise UpdateError(msg('core.update.incompatible'))

    def _check_start(self, exe):
        """(1.7.0, c5-updater a) "--version" must end with exit code 0 and print a version as its
        first line; an engine that starts and ends with an error (a damaged or blocked unpacking,
        a crash) is refused with core.update.start_failed before the option check, which cannot tell such an
        engine from a good one (both end without the "no such option" text)."""
        log = applog.get('updater')
        try:
            code, output, errors = _version_output(exe, PROBE_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            log.warning('the new engine did not answer --version within %s s', error.timeout)
            raise UpdateError(msg('core.update.start_failed',
                                  reason=msg('core.update.start_timeout', timeout=f'{error.timeout:g}'))) from error
        except OSError as error:
            log.warning('the new engine could not be started: %s', error)
            raise UpdateError(msg('core.update.start_failed', reason=msg('core.update.start_cannot'))) from error
        text = output.decode('utf-8', errors='replace').strip()
        first = text.splitlines()[0].strip() if text else ''
        if code != 0 or not VERSION_LINE.match(first):
            log.warning('the new engine failed --version (exit code %s): %s', code,
                        ' '.join((text + ' ' + errors.decode('utf-8', errors='replace')).split())[:300])
            raise UpdateError(msg('core.update.start_failed', reason=msg('core.update.start_exit', code=code)
                                  if code != 0 else msg('core.update.start_no_version')))

    def _install(self, temporary, target) -> bool:
        """os.replace(temporary, target), repeated for about INSTALL_RETRY seconds while Windows
        reports a sharing violation (a virus scanner or indexer that briefly holds the new or
        the old exe; core fix review-state-2).  False when Stop came meanwhile (nothing was
        changed); OSError when it still fails at the end."""
        deadline = time.monotonic() + INSTALL_RETRY
        while True:
            # (1.7.0, c5-updater b) Stop wins before every attempt: a Stop during the pause below
            # is seen before the next os.replace, also when the other program lets go meanwhile.
            if self.cancelled.is_set():
                return False
            try:
                os.replace(temporary, target)
                return True
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
            if self.cancelled.is_set():
                return False
            time.sleep(INSTALL_PAUSE)

    def run(self) -> None:
        import urllib.request
        temporary = None
        try:
            self._status(msg('core.update.checking'))
            release = check_latest()
            version = release['tag']
            if version == engine_version():
                self.emit('updated', {'version': version, 'unchanged': True})
                return
            if self.cancelled.is_set():
                return
            url, size, expected = release['url'], release['size'], release['sha256']
            if not url:
                raise UpdateError(msg('core.update.no_exe'))
            if not url.startswith(ASSET_PREFIX):
                raise UpdateError(msg('core.update.bad_host'))
            if len(expected) != 64:
                raise UpdateError(msg('core.update.no_sha'))
            if not 0 < size <= MAX_SIZE:
                raise UpdateError(msg('core.update.bad_size'))
            target = paths.own_dir(paths.engines_dir()) / 'yt-dlp.exe'
            # Unique per run: after Stop the window is free at once while an abandoned run may
            # still wait for the network; a second update must not share its file.
            temporary = target.with_name(f'yt-dlp.download.{os.getpid()}.{next(_serial)}.exe')
            checksum, received = hashlib.sha256(), 0
            self._status(msg('core.update.downloading'))
            network = _network_errors()
            try:
                response = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}),
                                                  timeout=NETWORK_TIMEOUT)
            except network as error:
                raise UpdateError(msg('core.update.no_github', reason=_reason(error))) from error
            try:
                with response, temporary.open('wb') as handle:
                    while True:
                        try:
                            block = response.read(BLOCK)
                        except network as error:
                            raise UpdateError(msg('core.update.no_github', reason=_reason(error))) from error
                        if not block:
                            break
                        if self.cancelled.is_set():
                            return
                        handle.write(block)
                        checksum.update(block)
                        received += len(block)
                        if received > size:
                            raise UpdateError(msg('core.update.bad_hash'))
                        self._progress(received, size)
            except OSError as error:
                # The download file could not be created or written (disk full, no permission, ...);
                # network errors were turned into UpdateError above (core fix review-state-3).
                raise UpdateError(msg('core.update.save_failed', reason=_file_reason(error))) from error
            if checksum.hexdigest() != expected or received != size:
                raise UpdateError(msg('core.update.bad_hash'))
            if self.probe_args:
                self._status(msg('core.update.testing'))
                self._probe(temporary)
            # Stop may have come during the download's last block or during the option check.
            if self.cancelled.is_set():
                return
            try:
                if not self._install(temporary, target):
                    return      # stopped while a scanner still held a file
            except OSError as error:
                # Nothing was changed: the engine that was in use still is.
                raise UpdateError(msg('core.update.write_failed', reason=_file_reason(error))) from error
            finally:
                invalidate()
            try:
                paths.atomic_write_text(target.with_suffix('.json'),
                                        json.dumps({'version': version, 'sha256': expected}))
            except OSError as error:
                # The new exe is in place, but without its receipt it is never used, and an
                # updated engine that was installed before is gone.  Leave a clean state (the
                # bundled engine) and say so, instead of claiming that nothing changed.
                _remove_updated()
                raise UpdateError(msg('core.update.record_failed', reason=_file_reason(error))) from error
            finally:
                invalidate()
            self.emit('updated', {'version': version, 'unchanged': False})
        except UpdateError as error:
            applog.get('updater').warning('engine update refused: %s', error, exc_info=error.__cause__ is not None)
            self.emit('update_error', message_of(error))
        except Exception as error:
            applog.get('updater').exception('engine update failed')
            self.emit('update_error', message_of(error))
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()   # only this run's own file
                except OSError:
                    pass
            self.emit('update_done', {'cancelled': self.cancelled.is_set()})
