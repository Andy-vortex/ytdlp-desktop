"""File naming (1.6.0). Standard library only; imports no app module.

The engine always writes its work name ``Title [id].ext`` (OUTPUT_TEMPLATE): that name is
unique per video, so resuming a partial file and yt-dlp's own "already downloaded" check
keep working.  When the file is finished the Runner renames it here, in Python, with
os.rename, which never replaces an existing file on Windows.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import time

NAMING = ('title', 'clean', 'title_id')
LABELS = {'title': '标题（推荐）', 'clean': '精简标题', 'title_id': '标题 [视频 ID]'}
OUTPUT_TEMPLATE = '%(title).140B [%(id)s].%(ext)s'
# "Artist - Song" followed by a decoration: cut at the first decoration bracket or hashtag.
CLEAN_RULE = re.compile(r'^(.+? - [^【「『#\[]+?)\s*(?:【|「|『|#|\[).*$')
RESERVED = re.compile(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?')
MAX_COPIES = 99
LOCK_TRIES = 3
LOCK_PAUSE = 0.3


def renames(style, playlist=False, archive_root='') -> bool:
    """Whether a finished file is renamed (and whether the engine may use a temp path).

    Playlist runs and archived items keep the work name: with it yt-dlp itself skips or
    overwrites entries that are already there, where a renamed file would be downloaded
    again as " (2)"; and the archive records the path the engine reported.
    """
    return style != 'title_id' and not playlist and not archive_root


def target_stem(stem, media_id, style, audio=True):
    """The final file stem for a work stem, or None when the file keeps its name."""
    media_id = '' if media_id is None else str(media_id)
    if style == 'title_id' or not media_id:
        return None
    suffix = f' [{media_id}]'
    if not stem.endswith(suffix):
        return None
    base = stem[:-len(suffix)].rstrip()
    if style == 'clean' and audio:
        cut = CLEAN_RULE.sub(r'\1', base).strip(' .')
        if len(cut) >= 2:
            base = cut
    if not base or RESERVED.fullmatch(base):
        return None
    return base


def finalize(path, media_id, style, audio=True, log=None, folder=None) -> str:
    """Rename a finished file to its final name; returns the path the file has afterwards.

    Never raises and never overwrites: an existing target gives " (2)" ... " (99)".  A
    target that is locked (a player, a virus scanner) is tried three times; after that,
    and after any other error, the file keeps its work name and ``log`` gets one line.
    ``folder``: where the final file goes when that is not the folder it is in (a file
    the engine left in the work folder); it must be on the same drive.
    """
    def report(text):
        if log is not None:
            try:
                log(text)
            except Exception:
                pass

    try:
        source = Path(path)
        stem = target_stem(source.stem, media_id, style, audio)
        if stem is None:
            return str(path)
        for number in range(1, MAX_COPIES + 1):
            candidate = (Path(folder) if folder is not None else source.parent) / (
                (stem if number == 1 else f'{stem} ({number})') + source.suffix)
            taken = False
            for attempt in range(LOCK_TRIES):
                try:
                    os.rename(source, candidate)
                    return str(candidate)
                except FileExistsError:
                    taken = True
                    break
                except PermissionError:
                    if attempt + 1 >= LOCK_TRIES:
                        report(f'文件正被其他程序占用，未能改名，已保留原文件名：{source.name}')
                        return str(path)
                    time.sleep(LOCK_PAUSE)
                except OSError as error:
                    report(f'文件未能改名（{error.strerror or type(error).__name__}），已保留原文件名：{source.name}')
                    return str(path)
            if not taken:
                break
        report(f'同名文件过多，未能改名，已保留原文件名：{source.name}')
    except Exception as error:   # a path the file system cannot express, a broken log callback
        report(f'文件未能改名（{type(error).__name__}），已保留原文件名。')
    return str(path)
