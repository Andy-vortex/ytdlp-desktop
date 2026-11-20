"""Output formats as one data table (1.6.0). Standard library and messages only (1.8.0: the
errors are messages).

``id`` is what is persisted (2.0.0: settings.json, failed-downloads.json, the job records of the
history store it as ``mode``) and the stable short name used in keys (the unfinished list, the
downloads table); the format menu and every other place show it as i18n.t('format.' + id) (2.0.0 S4: one
name per format).  The resolution is stored as it is
('1080p', '2160p / 4K'), with BEST ('best') for no height limit.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

import messages


@dataclass(frozen=True)
class Preset:
    id: str
    kind: str          # 'audio' or 'video'
    args: tuple        # argv words; the literal {L} stands for the height limit
    cover: bool = True  # False: this container cannot carry a cover picture


PRESETS = (
    Preset('audio_best', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'best')),
    Preset('audio_wav', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'wav',
            '--postprocessor-args', 'ExtractAudio+ffmpeg_o:-c:a pcm_f32le'), cover=False),
    Preset('audio_flac', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'flac',
            '--postprocessor-args', 'ExtractAudio+ffmpeg_o:-sample_fmt s32 -bits_per_raw_sample 24')),
    Preset('audio_mp3', 'audio',
           ('-f', 'ba/b', '-x', '--audio-format', 'mp3', '--audio-quality', '320K')),
    Preset('video_mkv', 'video',
           ('-f', 'bv*{L}+ba/b{L}', '--merge-output-format', 'mkv', '--remux-video', 'mkv')),
    Preset('audio_m4a', 'audio',
           ('-f', 'ba[ext=m4a]/ba[acodec^=mp4a]/ba/b', '-x', '--audio-format', 'm4a')),
    Preset('video_mp4', 'video',
           ('-f', 'bv*{L}+ba/b{L}', '-S', 'vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac',
            '--merge-output-format', 'mp4', '--remux-video', 'mp4')),
)

MODES = tuple(preset.id for preset in PRESETS)   # the stored mode values (engine.MODES)
# Preset ids in the order the format menu shows them (the menu's values are the ids).
MENU_ORDER = ('audio_best', 'audio_m4a', 'audio_mp3', 'audio_flac', 'audio_wav', 'video_mp4', 'video_mkv')

_HEIGHT = re.compile(r'^(\d+)p')
BEST = 'best'   # engine.RESOLUTIONS[0]: no height limit


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


def by_id(preset_id) -> Preset:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    raise ValueError(messages.msg('core.formats.bad_id', id=repr(preset_id)))


def format_args(preset, resolution) -> list[str]:
    """The preset's argv words for one resolution choice.

    {L} becomes ``[height<=N]``, N being the digits before ``p`` in the resolution
    ('1080p', '2160p / 4K'), and nothing for 'best' (or any text without such digits).
    """
    match = _HEIGHT.match(str(resolution))
    limit = f'[height<={match.group(1)}]' if match else ''
    return [word.replace('{L}', limit) for word in preset.args]
