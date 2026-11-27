from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 1.6.0: onedir build. dist\YT-DLP下载器\YT-DLP下载器.exe + _internal\ (sys._MEIPASS = <exe folder>\_internal).
# vendor\ is added once, as a Tree on COLLECT; it is never fed through Analysis (that bundled the FFmpeg DLLs twice).
root = Path(SPECPATH)
data = collect_data_files('customtkinter')
for package in ('scrapling', 'browserforge', 'apify_fingerprint_datapoints', 'tld', 'patchright'):
    data += collect_data_files(package)
hidden = collect_submodules('scrapling', filter=lambda name: not any(p in name for p in ('.cli', '.spiders')))
hidden += ['PIL.ImageDraw', 'PIL.ImageFont']
data += [(str(root / 'app.ico'), '.')]
a = Analysis([str(root / 'src' / 'app.py')], pathex=[str(root / 'src')],
             binaries=[], datas=data, hiddenimports=hidden, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='YT-DLP下载器', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False, icon=str(root / 'app.ico'),
          contents_directory='_internal')
# (2.0.5) build.py collects the Python packages' licences into the stage at every build; a python-packages folder an
# older prepare_vendor.py left in vendor\licenses is not bundled.
vendor = Tree(str(root / 'vendor'), prefix='vendor', excludes=['verification', '*.zip', '*.part', 'python-packages'])
coll = COLLECT(exe, a.binaries, a.datas, vendor, strip=False, upx=False, name='YT-DLP下载器')
