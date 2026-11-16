"""Start the app by hand inside a sandbox (1.6.0 WP0).

    .venv\\Scripts\\python.exe -B verification\\sandbox_app.py [--keep] [--base DIR] [app arguments...]

This is the only way to start the app from source during the upgrade.  ``python src\\app.py``
would use the repository root as the application folder and create ``data\\library.sqlite3``
and ``下载文件`` there, and it would read and write the owner's real settings and log.

--keep       keep the temporary sandbox after the app exits (its path is printed)
--base DIR   use DIR as the sandbox (created when missing, always kept); two launches with
             the same DIR share one sandbox and therefore one single-instance name
Everything else is passed to app.main(), e.g. --self-test REPORT --smoke-url URL --smoke-output DIR.
"""
import argparse
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sandbox_app.py', allow_abbrev=False, add_help=False,
                                     description='Start YT-DLP下载器 from source inside a sandbox.')
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--base', metavar='DIR')
    options, rest = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if options.base:
        os.environ['YTDLP_VERIFY_BASE'] = options.base
        # An explicit folder wins over a sandbox inherited from a parent process.
        os.environ.pop('YTDLP_VERIFY_SANDBOX', None)
    if options.keep:
        os.environ['YTDLP_VERIFY_KEEP'] = '1'
    import _sandbox
    print(f'BASE {_sandbox.BASE}', flush=True)
    sys.argv = [str(_sandbox.SRC / 'app.py'), *rest]
    import app
    return app.main()


if __name__ == '__main__':
    raise SystemExit(main())
