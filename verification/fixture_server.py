from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(root / 'fixture')))
info = {'port': server.server_port, 'url': f'http://127.0.0.1:{server.server_port}/fixture.mp4'}
# server-info.json is written only on request (--write PATH), never into verification/ by default.
if '--write' in sys.argv[1:-1]:
    Path(sys.argv[sys.argv.index('--write') + 1]).write_text(json.dumps(info), encoding='utf-8')
print(json.dumps(info), flush=True)
server.serve_forever()
