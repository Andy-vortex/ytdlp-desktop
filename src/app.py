import argparse
import json
import os
from pathlib import Path
import threading
import traceback

from engine import diagnostics

_instance = None  # the single-instance mutex; held until the process ends
RESTART_WAIT = 20  # s: (1.7.0) a restarted program waits this long for the old one to end


def main():
    global _instance
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', metavar='REPORT')
    parser.add_argument('--smoke-url')
    parser.add_argument('--smoke-output')
    parser.add_argument('--smoke-archive', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--collect-worker', metavar='JOB', help=argparse.SUPPRESS)
    # (1.7.0) The language row's restart: the pid of the program that started this one.
    parser.add_argument('--restarted-from', metavar='PID', type=int, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.collect_worker:
        from collector import worker_main
        return worker_main(arguments.collect_worker)
    if arguments.self_test:
        report = Path(arguments.self_test)
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = diagnostics()
            # (1.7.0) What the language would be (read-only: peek never sets a damaged file aside).
            import i18n
            import settings
            result['language'] = i18n.diagnostics(i18n.stored_choice(settings.peek()))
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
    import i18n
    import paths
    import procutil
    import updater
    if arguments.restarted_from:
        # (1.7.0) Started by the language row's 立即重启: the old window still holds the
        # single-instance mutex for a moment.  After the timeout the 1.6.0 rule below applies.
        procutil.wait_exit(arguments.restarted_from, timeout=RESTART_WAIT)
    if not arguments.smoke_url:
        _instance = procutil.single_instance()
        if _instance is None:
            # Another window is running: bring it forward and leave.  A sandboxed launch has its
            # own instance name and must never raise the owner's real window.
            if not os.environ.get('YTDLP_DESKTOP_INSTANCE'):
                procutil.focus_window(i18n.all_values('app.title'))
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
        prefs = settings.load()
        arguments.appearance = prefs['appearance']
        # (1.7.0) The language of this process: YTDLP_DESKTOP_LANG > the stored choice > Windows.
        choice = i18n.stored_choice(prefs)
    else:
        choice = 'system'           # smoke mode: the environment decides (tests: zh)
    i18n.init(choice)
    info = i18n.diagnostics(choice)
    applog.get('app').info('language: %s (choice %s, env %s, ui %s, locale %s)', info['resolved'], choice,
                           info['env'], info['ui_language'], info['locale'])
    from studio import StudioApp
    app = StudioApp(arguments)
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
