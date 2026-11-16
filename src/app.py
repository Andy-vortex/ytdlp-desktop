import argparse
import json
import os
from pathlib import Path
import threading
import traceback

from engine import diagnostics

_instance = None  # the single-instance mutex; held until the process ends


def main():
    global _instance
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', metavar='REPORT')
    parser.add_argument('--smoke-url')
    parser.add_argument('--smoke-output')
    parser.add_argument('--smoke-archive', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--collect-worker', metavar='JOB', help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.collect_worker:
        from collector import worker_main
        return worker_main(arguments.collect_worker)
    if arguments.self_test:
        report = Path(arguments.self_test)
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = diagnostics()
            if arguments.smoke_url:
                if not arguments.smoke_output:
                    raise ValueError('下载自检需要 --smoke-output 指定测试目录。')
                from engine import MODES, Options, Runner
                from library import Library
                output = Path(arguments.smoke_output).resolve()
                events = []
                runner = Runner(lambda kind, value: events.append((kind, value)))
                runner.library = Library(output / 'test-library.sqlite3')
                runner.download([arguments.smoke_url], Options(output, MODES[1], batch_safe=False,
                    archive_root=str(output / '资料归档') if arguments.smoke_archive else ''))
                done = next(v for k,v in reversed(events) if k == 'done')
                if done['failed'] or not done['success'] or not done['files'] or done.get('archive_errors'):
                    raise RuntimeError(f'打包下载自检失败：{events}')
                result['download_smoke'] = done
                result['archive_entries'] = runner.library.archives()
            result['status'] = 'passed'
            code = 0
        except Exception:
            result = {'status': 'failed', 'traceback': traceback.format_exc()}
            code = 1
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return code
    import applog
    import paths
    import procutil
    import updater
    if not arguments.smoke_url:
        _instance = procutil.single_instance()
        if _instance is None:
            # Another window is running: bring it forward and leave.  A sandboxed launch has its
            # own instance name and must never raise the owner's real window.
            if not os.environ.get('YTDLP_DESKTOP_INSTANCE'):
                procutil.focus_window()
            return 0
    applog.setup()
    applog.install_hooks()
    updater.cleanup()
    threading.Thread(target=paths.purge_stale, args=(procutil.alive,), daemon=True).start()
    # Per-monitor DPI awareness must be set before the first Tk root (or dialog) exists.
    import ui_theme
    ui_theme.enable_dpi_awareness()
    if not arguments.smoke_url:
        # (WP7) the stored 外观 choice; settings.load() turns an absent or invalid value into 'light'.
        # Smoke mode stays light (StudioApp's default).
        import settings
        arguments.appearance = settings.load()['appearance']
    from studio import StudioApp
    app = StudioApp(arguments)
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
