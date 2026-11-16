# tier: all
"""Real downloads of the local fixture with the bundled yt-dlp and ffmpeg (no website):
every format, file naming, spacing between links, a playlist run, and cancellation."""
import _sandbox
import json
from pathlib import Path
import subprocess
import threading

import engine
import formats
import paths
from engine import BIN, HIDDEN, MODES, Options, Runner, process_environment

base, _server = _sandbox.fixture_site()
url = base + '/fixture.mp4'
RUN = _sandbox.out('integration')
# Keep verification queues separate from the user's saved failed links.
_sandbox.set_state_dir(RUN / 'state')
MEDIA = ('.m4a', '.mp3', '.flac', '.wav', '.opus', '.mkv', '.mp4', '.webm', '.part', '.ytdl')
report = {'status': 'running', 'formats': []}


def download(links, options):
    events = []
    Runner(lambda kind, value: events.append((kind, value))).download(links, options)
    return events, next(value for kind, value in reversed(events) if kind == 'done')


def of(events, kind):
    return [value for name, value in events if name == kind]


def probe(path):
    result = subprocess.run([str(BIN / 'ffprobe.exe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            capture_output=True, encoding='utf-8', check=True, creationflags=HIDDEN, env=process_environment())
    return json.loads(result.stdout)


def media_in(folder):
    return sorted(path.name for path in Path(folder).iterdir() if path.suffix.lower() in MEDIA) if Path(folder).is_dir() else []


try:
    assert len(MODES) == 7, MODES
    for index, mode in enumerate(MODES):
        # last_media_end is shared by all runners: without the reset every format would wait 10-20 s.
        engine.Runner.reset_pacing()
        output = RUN / str(index)
        events, done = download([url], Options(output, mode))
        assert done['success'] == 1 and done['failed'] == 0, events
        files = [Path(p) for p in done['files']]
        assert files and all(p.is_file() for p in files), events
        # 1.6.0 naming: the finished file is renamed, the [id] of the work name is gone.
        assert files[-1].stem == 'fixture', files
        assert media_in(output) == [files[-1].name], media_in(output)
        media = of(events, 'media')
        assert len(media) == 1 and media[0]['filepath'] == str(files[-1]) and media[0]['id'] == 'fixture', media
        assert of(events, 'file') == [str(files[-1])], of(events, 'file')
        kinds = [kind for kind, _ in events]
        assert kinds.index('media') < kinds.index('file'), kinds
        data = probe(files[-1])
        audio = next(s for s in data['streams'] if s['codec_type'] == 'audio')
        assert abs(float(data['format']['duration']) - 3) < .3
        if index == 1:
            assert audio['codec_name'] == 'pcm_f32le', audio
        if index == 2:
            assert audio['codec_name'] == 'flac' and audio['bits_per_raw_sample'] == '24', audio
        if index == 3:
            assert audio['codec_name'] == 'mp3' and int(audio['bit_rate']) == 320000, audio
        if index == 4:
            assert any(s['codec_type'] == 'video' for s in data['streams'])
            assert files[-1].suffix == '.mkv'
        if index == 5:
            assert files[-1].suffix == '.m4a' and audio['codec_name'] == 'aac', (files, audio)
        if index == 6:
            assert files[-1].suffix == '.mp4', files
            assert any(s['codec_type'] == 'video' and s['codec_name'] == 'h264' for s in data['streams']), data['streams']
            assert audio['codec_name'] == 'aac', audio
        # Tags are on by default (--embed-metadata): the source link travels with the file.
        tags = {key.lower(): value for key, value in (data['format'].get('tags') or {}).items()}
        assert tags.get('title') == 'fixture' and url in (tags.get('comment'), tags.get('purl')), tags
        report['formats'].append({'mode': mode, 'file': str(files[-1]), 'audio_codec': audio['codec_name'],
                                   'duration': data['format']['duration'], 'status': 'passed'})
        # A single link never waits for the spacing between downloads.
        waits = [value for value in of(events, 'waiting') if value.get('kind') == 'interval']
        assert not waits, waits
        report['formats'][-1]['interval_waits'] = len(waits)
        print(f'Passed: {mode}', flush=True)
    assert [formats.by_mode(mode).id for mode in MODES] == ['audio_best', 'audio_wav', 'audio_flac', 'audio_mp3', 'video_mkv',
                                                           'audio_m4a', 'video_mp4']

    # Two links in one job: the second waits for the spacing interval, the first does not;
    # the second file of the same title gets " (2)" instead of replacing the first.
    engine.Runner.reset_pacing()
    engine.PACE_SECONDS = (1, 2)
    events, done = download([url + '?a=1', url + '?a=2'], Options(RUN / 'two', MODES[3]))
    engine.PACE_SECONDS = (10, 20)
    assert done['success'] == 2 and done['failed'] == 0 and done['total'] == 2, events
    kinds = [(kind, value.get('index') if isinstance(value, dict) else None) for kind, value in events]
    second = kinds.index(('item', 2))
    assert not [value for kind, value in events[:second] if kind == 'waiting' and value.get('kind') == 'interval'], events[:second]
    interval = [value for kind, value in events[second:] if kind == 'waiting' and value.get('kind') == 'interval']
    assert interval and all(value['index'] == 2 and 1 <= value['seconds'] <= 2 for value in interval), interval
    assert kinds.index(('waiting', 2)) < kinds.index(('item_done', 2))
    assert media_in(RUN / 'two') == ['fixture (2).mp3', 'fixture.mp3'], media_in(RUN / 'two')
    assert [Path(p).name for p in done['files']] == ['fixture.mp3', 'fixture (2).mp3'], done['files']
    report['spacing_between_two_links'] = {'interval_wait_seconds': interval[0]['seconds'], 'files': media_in(RUN / 'two')}

    # The same link again into the same folder: a second copy, never an overwrite.
    engine.Runner.reset_pacing()
    events, done = download([url], Options(RUN / '0', MODES[0]))
    assert done['success'] == 1, events
    assert len(media_in(RUN / '0')) == 2 and Path(done['files'][-1]).stem == 'fixture (2)', media_in(RUN / '0')
    report['second_identical_run'] = media_in(RUN / '0')

    # Fix round 1: a file that an earlier download left under the work name ("Title [id].ext":
    # 1.5.1, a playlist run, the title_id style) is never renamed.  The engine reports it as
    # already downloaded and the link is done with that path; a native format that the engine
    # downloads again anyway leaves no second, identical copy.
    for index in (3, 0):
        folder = RUN / f'earlier-{index}'
        engine.Runner.reset_pacing()
        events, done = download([url], Options(folder, MODES[index], naming='title_id'))
        assert done['success'] == 1 and media_in(folder) == [f'fixture [fixture]{Path(done["files"][-1]).suffix}'], media_in(folder)
        earlier = Path(done['files'][-1])
        engine.Runner.reset_pacing()
        events, done = download([url], Options(folder, MODES[index]))
        assert done['success'] == 1 and done['files'] == [str(earlier)], (done, media_in(folder))
        assert media_in(folder) == [earlier.name], media_in(folder)
        assert [value['path'] for value in of(events, 'item_done')] == [str(earlier)], of(events, 'item_done')
        assert f'保存位置已有这个文件（之前下载的），保留原文件名：{earlier.name}' in of(events, 'log'), of(events, 'log')
        assert media_in(paths.work_dir()) == [], media_in(paths.work_dir())
        report.setdefault('earlier_work_name_kept', []).append(media_in(folder))

    # A playlist run keeps the 1.5.1 file behaviour: the name with [id], no temp folder, and a
    # second run of the same list neither duplicates the file nor leaves anything behind.
    engine.Runner.reset_pacing()
    listed = RUN / 'playlist'
    events, done = download([url], Options(listed, MODES[0], playlist=True))
    assert done['success'] == 1, events
    # Inside one playlist process the engine itself keeps the 10-20 s interval; the UI sees it as before.
    waits = [value for value in of(events, 'waiting') if value['seconds'] >= 10]
    assert waits and 'kind' not in waits[0] and 10 <= waits[0]['seconds'] <= 20, of(events, 'waiting')
    report['playlist_engine_wait_seconds'] = waits[0]['seconds']
    engine.Runner.reset_pacing()
    events, done = download([url], Options(listed, MODES[0], playlist=True, batch_safe=False))
    assert done['success'] == 1, events
    found = media_in(listed)
    assert len(found) == 1 and Path(found[0]).stem.endswith(' [fixture]'), found
    assert not [name for name in found if '(2)' in name], found
    assert media_in(paths.work_dir()) == [], media_in(paths.work_dir())
    assert not any(word.startswith('temp:') for word in engine.build_command(url, Options(listed, MODES[0], playlist=True)))
    report['playlist_two_runs'] = found

    original = engine.build_command
    def slow_command(url, options, **kw):
        command = original(url, options, **kw)
        return command[:-2] + ['--limit-rate', '8K'] + command[-2:]
    engine.build_command = slow_command
    engine.Runner.reset_pacing()
    started = threading.Event()
    events = []
    def emit(kind, value):
        events.append((kind, value))
        if kind == 'progress' and value.get('downloaded', 0):
            started.set()
    runner = Runner(emit)
    thread = threading.Thread(target=runner.download, args=([url], Options(RUN / 'cancel-test', MODES[4])))
    thread.start()
    assert started.wait(30), 'Download did not begin'
    runner.cancel()
    thread.join(20)
    assert not thread.is_alive(), 'Cancellation did not stop the process'
    done = next(value for kind, value in reversed(events) if kind == 'done')
    assert done['cancelled'] and done['pending_urls'] == [url], done
    # Nothing half-finished in the output folder: the partial file is in the work folder.
    assert media_in(RUN / 'cancel-test') == [], media_in(RUN / 'cancel-test')
    assert [entry['reason'] for entry in engine.load_unfinished()] == ['stopped'], engine.load_unfinished()
    report['cancellation'] = 'passed'
    engine.build_command = original
    report['status'] = 'passed'
except Exception:
    import traceback
    report['status'] = 'failed'
    report['traceback'] = traceback.format_exc()
    raise
finally:
    _sandbox.write_report('integration-report.json', report)
