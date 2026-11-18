import argparse
import json
from pathlib import Path
import traceback

from engine import diagnostics


def main():
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
    import customtkinter as ctk
    from studio import StudioApp
    ctk.set_appearance_mode('light')
    ctk.set_default_color_theme('blue')
    app = StudioApp(arguments)
    app.report_callback_exception = lambda kind, value, trace: app.append_log(
        '界面错误：' + ''.join(traceback.format_exception(kind, value, trace)))
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
