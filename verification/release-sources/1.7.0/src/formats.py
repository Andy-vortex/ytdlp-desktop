"""Output formats as one data table (1.6.0). Standard library only; imports no app module.

``mode`` is the string that is persisted (settings.json, failed-downloads.json, the job
records of the history) and never changes; ``id`` is the stable short name used in keys
(the unfinished list, the downloads table); ``label`` is what the format menu shows.
The first five entries are the formats of 1.5.1, in their old order: the position of a
mode in MODES is frozen.  New formats are appended.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Preset:
    id: str
    mode: str
    label: str
    kind: str          # 'audio' or 'video'
    args: tuple        # argv words; the literal {L} stands for the height limit
    cover: bool = True  # False: this container cannot carry a cover picture


PRESETS = (
    Preset('audio_best', '音频 · 原始最佳', '原始音频 · 保留源编码', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'best')),
    Preset('audio_wav', '音频 · WAV 32 位', 'WAV · 32 位浮点', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'wav',
            '--postprocessor-args', 'ExtractAudio+ffmpeg_o:-c:a pcm_f32le'), cover=False),
    Preset('audio_flac', '音频 · FLAC', 'FLAC · 24 位', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'flac',
            '--postprocessor-args', 'ExtractAudio+ffmpeg_o:-sample_fmt s32 -bits_per_raw_sample 24')),
    Preset('audio_mp3', '音频 · MP3 320K', 'MP3 · 320 kbps', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'mp3', '--audio-quality', '320K')),
    Preset('video_mkv', '视频 · 最佳画质 MKV', 'MKV · 保留源画质', 'video',
           ('-f', 'bv*{L}+ba/b{L}', '--merge-output-format', 'mkv', '--remux-video', 'mkv')),
    Preset('audio_m4a', '音频 · M4A 原生', 'M4A · AAC 原生（免转码）', 'audio',
           ('-f', 'ba[ext=m4a]/ba[acodec^=mp4a]/ba/b', '-x', '--audio-format', 'm4a')),
    Preset('video_mp4', '视频 · MP4 兼容', 'MP4 · 兼容优先（H.264）', 'video',
           ('-f', 'bv*{L}+ba/b{L}', '-S', 'vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac',
            '--merge-output-format', 'mp4', '--remux-video', 'mp4')),
)

MODES = tuple(preset.mode for preset in PRESETS)
# The formats 1.5.1 knows: a list for the rollback version may only name these.
LEGACY_MODES = MODES[:5]
# Preset ids in the order the format menu shows them.  The UI maps a label to its preset
# with by_label(), never by list position.
MENU_ORDER = ('audio_best', 'audio_m4a', 'audio_mp3', 'audio_flac', 'audio_wav', 'video_mp4', 'video_mkv')

_HEIGHT = re.compile(r'^(\d+)p')
BEST = '最佳'   # engine.RESOLUTIONS[0]: no height limit


def format_key(preset, resolution) -> str:
    """Which output a download is, for comparing and storing (the queue, the unfinished
    list's ``format`` field, the downloads table): the preset id, and for a video preset
    with a height limit the id + '@' + the resolution ('video_mp4@1080p').  An audio file
    is the same whatever resolution was chosen."""
    if preset.kind != 'video' or resolution in (None, '', BEST):
        return preset.id
    return f'{preset.id}@{resolution}'


def by_key(key) -> Preset:
    """The preset of a format key (see format_key); ValueError when there is none."""
    return by_id(str(key).partition('@')[0])


def by_mode(mode) -> Preset:
    for preset in PRESETS:
        if preset.mode == mode:
            return preset
    raise ValueError(f'Unsupported output format: {mode!r}')


def by_id(preset_id) -> Preset:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    raise ValueError(f'Unsupported output format id: {preset_id!r}')


def by_label(label) -> Preset:
    for preset in PRESETS:
        if preset.label == label:
            return preset
    raise ValueError(f'Unsupported output format label: {label!r}')


def format_args(preset, resolution) -> list[str]:
    """The preset's argv words for one resolution choice.

    {L} becomes ``[height<=N]``, N being the digits before ``p`` in the resolution
    ('1080p', '2160p / 4K'), and nothing for '最佳' (or any text without such digits).
    """
    match = _HEIGHT.match(str(resolution))
    limit = f'[height<={match.group(1)}]' if match else ''
    return [word.replace('{L}', limit) for word in preset.args]
