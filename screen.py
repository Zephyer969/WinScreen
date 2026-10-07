"""WinScreen: persistent Windows PTY sessions and a localhost dashboard."""
import argparse
import base64
import codecs
from contextlib import contextmanager
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import uuid
import webbrowser

import winprocess
from terminal_input import win32_input

VERSION = '1.0.1'
HERE = Path(__file__).resolve().parent
ACTIVE = {'starting', 'running'}
NAME = re.compile(r'^[\w\-\.\u4e00-\u9fff]{1,64}$')
SESSION_ID = re.compile(r'^[a-f0-9]{12}$')


def token_matches(candidate, expected):
    """Malformed non-ASCII credentials are a rejection, not a handler crash."""
    return (isinstance(candidate, str) and candidate.isascii()
            and secrets.compare_digest(candidate, expected))


class LocalServer(ThreadingHTTPServer):
    """Bound idle connections; this is a trusted-user loopback service only."""
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


def json_body(handler):
    if handler.headers.get('Transfer-Encoding'):
        raise ValueError('Chunked requests are not supported')
    length = int(handler.headers.get('Content-Length', 0))
    if not 0 < length <= 262144:
        raise ValueError('Invalid request size')
    body = json.loads(handler.rfile.read(length))
    if not isinstance(body, dict):
        raise ValueError('JSON body must be an object')
    return body


def valid_record(meta, folder):
    if not isinstance(meta, dict):
        return False
    created = meta.get('created')
    return (isinstance(meta.get('id'), str) and SESSION_ID.fullmatch(meta['id'])
            and meta['id'] == folder.name
            and isinstance(meta.get('name'), str) and NAME.fullmatch(meta['name'])
            and isinstance(created, (int, float)) and math.isfinite(created)
            and isinstance(meta.get('cwd'), str)
            and isinstance(meta.get('status'), str)
            and meta.get('status') in {'starting', 'running', 'completed', 'failed', 'stopped', 'interrupted'})


def data_root():
    configured = os.environ.get('WINSCREEN_DIR')
    # AppData file I/O itself can be redirected by MSIX launchers, even when
    # LOCALAPPDATA looks normal. A profile-level directory avoids that redirect.
    root = Path(configured) if configured else Path.home() / '.winscreen'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'sessions').mkdir(exist_ok=True)
    return root.resolve()


def load(path, fallback=None):
    # Windows can briefly deny/read-miss a file during replacement or scanning.
    # A transient metadata read must not turn into a None dereference in clients.
    for attempt in range(8):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            if attempt < 7:
                time.sleep(.01)
    return fallback


def save(path, obj):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    for _ in range(30):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            time.sleep(.01)
    temp.unlink(missing_ok=True)
    raise RuntimeError('无法更新会话记录，请检查目录权限。')


@contextmanager
def file_lock(path):
    import msvcrt
    with open(path, 'a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + 10
        while True:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('操作锁等待超时，请稍后重试。')
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def public(meta):
    result = {k: v for k, v in meta.items() if k not in {'token', 'worker_stamp'}}
    if result.get('status') in ACTIVE:
        if not meta.get('worker_pid'):
            if time.time() - meta['created'] > 15:
                result.update(status='interrupted', error='后台启动未完成。查看 worker.log。')
        else:
            try:
                alive = winprocess.stamp(meta['worker_pid']) == meta.get('worker_stamp')
            except (TypeError, ValueError, OverflowError):
                alive = False
            if not alive:
                result.update(status='interrupted', error='后台进程已退出；可能发生了重启、注销或进程终止。')
    return result


def sessions(include_archived=False):
    found = []
    for folder in (data_root() / 'sessions').iterdir():
        if not folder.is_dir() or (not include_archived and (folder / 'archived').exists()):
            continue
        item = load(folder / 'session.json')
        if valid_record(item, folder):
            found.append(public(item))
    return sorted(found, key=lambda m: m['created'], reverse=True)


def resolve(selector, active_only=False):
    items = sessions(True)
    if active_only:
        items = [m for m in items if m['status'] in ACTIVE]
    exact = [m for m in items if m['id'] == selector or m['name'] == selector
             or m['id'] + '.' + m['name'] == selector]
    matched = exact or [m for m in items if m['id'].startswith(selector)]
    live = [m for m in matched if m['status'] in ACTIVE]
    if len(live) == 1:
        matched = live
    if len(matched) != 1:
        raise ValueError('找不到唯一会话，请用 screen -ls 查看并使用完整 ID。')
    meta = load(data_root() / 'sessions' / matched[0]['id'] / 'session.json')
    if meta is None:
        raise ValueError('会话记录暂时无法读取，请稍后重试或检查目录权限。')
    return meta


def worker_request(meta, path, body=None):
    if not meta.get('port') or winprocess.stamp(meta.get('worker_pid', 0)) != meta.get('worker_stamp'):
        raise ValueError('会话后台已停止。')
    request = Request('http://127.0.0.1:%s%s' % (meta['port'], path),
                      data=None if body is None else json.dumps(body).encode(),
                      headers={'Authorization': 'Bearer ' + meta['token'], 'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def create(name, cwd=None, shell='powershell', command=None, env_python=None, argv=None):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('会话名使用中英文、数字、下划线、短横线或点，长度1—64。')
    if command is not None and not isinstance(command, str):
        raise ValueError('运行命令必须是字符串。')
    if argv is not None and (not isinstance(argv, list) or not argv
                            or not all(isinstance(arg, str) and '\0' not in arg for arg in argv)
                            or not argv[0]):
        raise ValueError('程序参数必须是非空字符串列表。')
    if argv and command:
        raise ValueError('程序参数与 shell 命令不能同时指定。')
    if cwd is not None and not isinstance(cwd, (str, os.PathLike)):
        raise ValueError('工作目录必须是路径字符串。')
    if env_python is not None and not isinstance(env_python, (str, os.PathLike)):
        raise ValueError('Python 环境必须是路径字符串。')
    if importlib.util.find_spec('winpty') is None:
        raise ValueError('缺少 pywinpty。请运行 python -m pip install -r requirements.txt。')
    cwd = str(Path(cwd or os.getcwd()).expanduser().resolve())
    if not Path(cwd).is_dir():
        raise ValueError('工作目录不存在：' + cwd)
    if shell not in {'powershell', 'cmd'}:
        raise ValueError('请选择 powershell 或 cmd。')
    if env_python:
        env_python = str(Path(env_python).expanduser().resolve())
        if not Path(env_python).is_file():
            raise ValueError('Python 环境路径不存在。')
    with file_lock(data_root() / 'create.lock'):
        if any(m['name'] == name and m['status'] in ACTIVE for m in sessions(True)):
            raise ValueError('同名会话正在运行，请 screen -r ' + name)
        sid = uuid.uuid4().hex[:12]
        folder = data_root() / 'sessions' / sid
        folder.mkdir()
        meta = dict(id=sid, name=name, cwd=cwd, shell=shell, command=command, argv=argv,
                    env_python=env_python, created=time.time(), status='starting',
                    exit_code=None, attached_count=0, token=secrets.token_urlsafe(32))
        save(folder / 'session.json', meta)
        try:
            winprocess.detached([sys.executable, str(HERE / 'screen.py'), '_worker', sid], folder / 'worker.log')
        except Exception as exc:
            meta.update(status='failed', error=str(exc), ended=time.time())
            save(folder / 'session.json', meta)
            raise
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        meta = load(folder / 'session.json', meta)
        if meta['status'] != 'starting':
            if meta['status'] == 'failed':
                raise ValueError(meta.get('error', '会话启动失败。'))
            return meta
        time.sleep(.08)
    raise ValueError('后台启动超时。请查看：' + str(folder / 'worker.log'))


def read_log(meta, offset=0, limit=65536):
    path = data_root() / 'sessions' / meta['id'] / 'terminal.log'
    size = path.stat().st_size if path.exists() else 0
    offset = min(size, max(0, size - 180000 if offset < 0 else offset))
    if path.exists():
        with path.open('rb') as stream:
            stream.seek(offset)
            content = stream.read(min(262144, limit))
    else:
        content = b''
    return dict(data=base64.b64encode(content).decode(), offset=offset + len(content),
                size=size, status=public(meta)['status'], exit_code=meta.get('exit_code'))


def run_worker(sid):
    from winpty import PTY, Backend
    folder = data_root() / 'sessions' / sid
    meta = load(folder / 'session.json')
    if not meta:
        return 1
    meta.update(worker_pid=os.getpid(), worker_stamp=winprocess.stamp(os.getpid()))
    save(folder / 'session.json', meta)
    pty = None
    job = None
    clients = {}
    mutex = threading.RLock()
    stop = threading.Event()
    server = None
    log = None
    win32_keyboard = False
    mode_tail = ''
    try:
        job = winprocess.own_worker_job()
        native_powershell = Path(os.environ.get('SystemRoot', 'C:\\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        shell_exe = ((str(native_powershell) if native_powershell.is_file() else shutil.which('powershell.exe')) if meta['shell'] == 'powershell'
                     else os.environ.get('COMSPEC', 'cmd.exe'))
        if not shell_exe:
            raise ValueError('找不到选定的终端程序。')
        env = os.environ.copy()
        env.update(PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8', TERM='xterm-256color')
        if meta['shell'] == 'powershell':
            # Use the inbox PSReadLine rather than a downloaded runtime's copy.
            # Preserve user modules/profiles and do not bypass execution policy.
            defaults = [str(Path(shell_exe).parent / 'Modules'),
                        str(Path(os.environ.get('ProgramFiles', 'C:\\Program Files')) / 'WindowsPowerShell' / 'Modules')]
            inherited = env.get('PSMODULEPATH', '').split(os.pathsep)
            inherited = [p for p in inherited if p and 'codex-runtimes' not in p.lower()]
            # os.environ on Windows normalizes keys to uppercase. Do not create
            # a second case-variant key in the native environment block.
            env['PSMODULEPATH'] = os.pathsep.join(dict.fromkeys(defaults + inherited))
        if meta.get('env_python'):
            prefix = Path(meta['env_python']).parent
            env['PATH'] = os.pathsep.join(map(str, [prefix, prefix / 'Scripts', prefix / 'Library' / 'bin'])) + os.pathsep + env.get('PATH', '')
            env['CONDA_PREFIX'] = str(prefix)
        args = ['-NoLogo'] if meta['shell'] == 'powershell' else ['/d']
        if meta.get('argv'):
            # Structured argv must never be parsed again by CMD: %variables%,
            # ampersands and quoted strings are program data, not shell syntax.
            executable = meta['argv'][0]
            candidate = Path(meta['cwd']) / executable
            shell_exe = str(candidate.resolve()) if candidate.is_file() else shutil.which(executable, path=env.get('PATH'))
            if not shell_exe:
                raise ValueError('找不到可执行程序：' + executable)
            if Path(shell_exe).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
                raise ValueError('批处理或脚本请使用 --shell 与 --command 显式执行。')
            args = meta['argv'][1:]
        elif meta.get('command'):
            if meta['shell'] == 'powershell':
                code = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
                        "$OutputEncoding=[Console]::OutputEncoding; $global:LASTEXITCODE=0; "
                        "try { " + meta['command'] + "\n; $ok=$?; $rc=$LASTEXITCODE; "
                        "if (-not $ok -and $rc -eq 0) { $rc=1 }; exit $rc } "
                        "catch { Write-Error $_ -ErrorAction Continue; exit 1 }")
                args += ['-EncodedCommand', base64.b64encode(code.encode('utf-16-le')).decode()]
            else:
                script = folder / 'run.cmd'
                script.write_text('@echo off\nchcp 65001 >nul\n' + meta['command'] + '\nexit /b %errorlevel%\n', encoding='utf-8')
                args += ['/c', str(script)]
        pty = PTY(110, 30, backend=Backend.ConPTY)
        environment = '\0'.join('%s=%s' % p for p in sorted(env.items())) + '\0'
        pty.spawn(shell_exe, cmdline=' ' + subprocess.list2cmdline(args), cwd=meta['cwd'], env=environment)
        meta['child_pid'] = pty.pid
        log = (folder / 'terminal.log').open('ab', buffering=0)

        class WorkerHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authorized(self):
                return token_matches(self.headers.get('Authorization', ''), 'Bearer ' + meta['token'])

            def do_GET(self):
                if not self.authorized():
                    return self.answer(403, {'error': 'Unauthorized'})
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                if parsed.path != '/read':
                    return self.answer(404, {'error': 'Not found'})
                client = params.get('client', [''])[0]
                if client:
                    with mutex:
                        clients[client[:128]] = time.monotonic()
                try:
                    self.answer(200, read_log(meta, int(params.get('offset', ['0'])[0])))
                except ValueError:
                    self.answer(400, {'error': 'Invalid offset'})

            def do_POST(self):
                if not self.authorized():
                    return self.answer(403, {'error': 'Unauthorized'})
                try:
                    body = json_body(self)
                    with mutex:
                        if self.path == '/write':
                            text = str(body['text'])
                            pty.write(win32_input(text) if win32_keyboard else text)
                        elif self.path == '/resize':
                            pty.set_size(min(350, max(20, int(body['cols']))), min(150, max(5, int(body['rows']))))
                        elif self.path == '/detach':
                            clients.pop(body.get('client', ''), None)
                        elif self.path == '/detach-all':
                            clients.clear()
                            meta['detach_epoch'] = time.time()
                        elif self.path == '/stop':
                            stop.set()
                        else:
                            raise ValueError('Unknown action')
                    self.answer(200, {'ok': True})
                except Exception as exc:
                    self.answer(400, {'error': str(exc)})

        server = LocalServer(('127.0.0.1', 0), WorkerHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        meta.update(status='running', port=server.server_address[1], started=time.time(), detach_epoch=0)
        save(folder / 'session.json', meta)
        last_save = 0
        idle_after_exit = None
        while not stop.is_set():
            with mutex:
                try:
                    chunk = pty.read(65536, blocking=False)
                except Exception as exc:
                    if pty.isalive() and 'EOF' not in str(exc):
                        raise
                    chunk = ''
                if chunk:
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode('utf-8', errors='replace')
                    mode_text = mode_tail + chunk
                    for mode in re.finditer(r'\x1b\[\?9001([hl])', mode_text):
                        win32_keyboard = mode[1] == 'h'
                    mode_tail = mode_text[-16:]
                    # ConPTY may query the cursor before any client has attached.
                    if '\x1b[6n' in chunk:
                        pty.write('\x1b[1;1R')
                        chunk = chunk.replace('\x1b[6n', '')
                    log.write(chunk.encode('utf-8'))
                alive = pty.isalive()
                if not alive:
                    idle_after_exit = time.monotonic() if idle_after_exit is None or chunk else idle_after_exit
                    if time.monotonic() - idle_after_exit > .4:
                        break
                now = time.monotonic()
                if now - last_save > .5:
                    clients = {key: value for key, value in clients.items() if now - value < 3}
                    meta.update(heartbeat=time.time(), attached_count=len(clients))
                    save(folder / 'session.json', meta)
                    last_save = now
            time.sleep(.02)
        with mutex:
            rc = pty.get_exitstatus() if not stop.is_set() else None
            meta.update(status='stopped' if stop.is_set() else ('completed' if rc == 0 else 'failed'),
                        exit_code=rc, ended=time.time(), attached_count=0)
            save(folder / 'session.json', meta)
    except BaseException as exc:
        meta.update(status='failed', error=str(exc), ended=time.time(), attached_count=0)
        save(folder / 'session.json', meta)
        print(str(exc), file=sys.stderr, flush=True)
    finally:
        if log:
            log.close()
        # The OS closes the worker's job at process exit, ending all descendants.
        # Avoid ConPTY destructor waits on a blocked application.
        os._exit(0 if meta['status'] in {'completed', 'stopped'} else 1)


def attach(meta):
    import msvcrt
    if not sys.stdin.isatty() or not winprocess.console_input_available():
        raise ValueError('重连需要交互终端。SSH 请使用 -t；或使用 screen --panel。')
    client = uuid.uuid4().hex
    prefix = False
    attached_at = time.time()
    offset = -1
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    last_size = None
    print('正在进入 %s。Ctrl+A 后按 D 返回，任务继续运行。' % meta['name'])
    sys.stdout.flush()
    with winprocess.ConsoleModes():
        try:
            while True:
                current = resolve(meta['id'])
                if current.get('detach_epoch', 0) > attached_at:
                    break
                if public(current)['status'] not in ACTIVE:
                    result = read_log(current, offset)
                    sys.stdout.write(decoder.decode(base64.b64decode(result['data'])))
                    sys.stdout.flush()
                    break
                result = worker_request(current, '/read?offset=%s&client=%s' % (offset, client))
                offset = result['offset']
                sys.stdout.write(decoder.decode(base64.b64decode(result['data'])))
                sys.stdout.flush()
                size = shutil.get_terminal_size((110, 30))
                if size != last_size:
                    worker_request(current, '/resize', {'cols': size.columns, 'rows': size.lines})
                    last_size = size
                while msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if prefix:
                        prefix = False
                        if ch.lower() == 'd':
                            return
                        if ch == '\x01':
                            ch = '\x01'
                        else:
                            ch = '\x01' + ch
                    elif ch == '\x01':
                        prefix = True
                        continue
                    if ch in {'\x00', '\xe0'}:
                        key = msvcrt.getwch()
                        ch = {'H': '\x1b[A', 'P': '\x1b[B', 'M': '\x1b[C', 'K': '\x1b[D',
                              'G': '\x1b[H', 'O': '\x1b[F', 'S': '\x1b[3~',
                              'I': '\x1b[5~', 'Q': '\x1b[6~'}.get(key, '')
                    if ch:
                        worker_request(current, '/write', {'text': ch})
                time.sleep(.035)
        finally:
            try:
                worker_request(meta, '/detach', {'client': client})
            except (OSError, ValueError):
                pass
            print('\r\n[detached from %s.%s]' % (meta['id'], meta['name']))


def panel_info():
    info = load(data_root() / 'panel.json')
    if not isinstance(info, dict) or not all(key in info for key in ('pid', 'stamp', 'port', 'token')):
        return None
    if (not isinstance(info['pid'], int) or not isinstance(info['port'], int)
            or not 0 < info['port'] <= 65535 or not isinstance(info['token'], str)):
        return None
    if info and winprocess.stamp(info['pid']) == info['stamp']:
        try:
            with urlopen('http://127.0.0.1:%s/health' % info['port'], timeout=.6) as response:
                if json.load(response).get('app') == 'WinScreen':
                    return info
        except (OSError, ValueError):
            pass
    return None


def open_panel(browser=True, port=8765):
    info = panel_info()
    if not info:
        winprocess.detached([sys.executable, str(HERE / 'screen.py'), '_panel', '--port', str(port)], data_root() / 'panel.log')
        for _ in range(100):
            info = panel_info()
            if info:
                break
            time.sleep(.1)
    if not info:
        raise ValueError('面板启动失败。检查 ' + str(data_root() / 'panel.log'))
    url = 'http://127.0.0.1:%s/?key=%s' % (info['port'], info['token'])
    if browser:
        webbrowser.open(url)
    print('WinScreen 面板已启动： http://127.0.0.1:%s' % info['port'])
    print('关闭浏览器不会终止会话。')
    return info


class PanelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, raw, content_type='application/json; charset=utf-8', extra=None):
        if isinstance(raw, dict):
            raw = json.dumps(raw, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        # Different localhost ports are legitimate SSH forwarding endpoints.
        match = re.fullmatch(r'(?:127\.0\.0\.1|localhost):([0-9]{1,5})', self.headers.get('Host', ''))
        return bool(match) and 0 < int(match[1]) <= 65535

    def authorized(self):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            value = cookie.get('winscreen')
            return bool(value) and token_matches(value.value, self.server.token)
        except http.cookies.CookieError:
            return False

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': 'Invalid host'})
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == '/health':
            return self.send(200, {'app': 'WinScreen', 'version': VERSION})
        if parsed.path == '/' and 'key' in query:
            if not token_matches(query['key'][0], self.server.token):
                return self.send(403, {'error': 'Invalid key'})
            return self.send(302, b'', extra={'Location': '/', 'Set-Cookie': 'winscreen=%s; HttpOnly; SameSite=Strict; Path=/' % self.server.token})
        if not self.authorized():
            return self.send(403, b'<meta charset="utf-8"><p>\xe8\xaf\xb7\xe5\x85\x88\xe8\xbf\x90\xe8\xa1\x8c screen --panel \xe6\x89\x93\xe5\xbc\x80\xe9\x9d\xa2\xe6\x9d\xbf\xe3\x80\x82</p>', 'text/html; charset=utf-8')
        try:
            if parsed.path == '/api/state':
                return self.send(200, {'sessions': sessions(), 'csrf': self.server.token,
                                      'computer': socket.gethostname(), 'python': sys.executable,
                                      'cwd': str(Path.home()), 'storage': str(data_root()), 'version': VERSION})
            match = re.fullmatch(r'/api/session/([a-f0-9]{12})/(read|download)', parsed.path)
            if match:
                meta = resolve(match[1])
                if match[2] == 'download':
                    path = data_root() / 'sessions' / meta['id'] / 'terminal.log'
                    # Stream bounded chunks: training logs can be very large.
                    size = path.stat().st_size if path.exists() else 0
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(size))
                    self.send_header('Content-Disposition', 'attachment; filename="%s.log"' % meta['id'])
                    self.end_headers()
                    if size:
                        with path.open('rb') as stream:
                            remaining = size
                            while remaining:
                                chunk = stream.read(min(65536, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                    return
                offset = int(query.get('offset', ['-1'])[0])
                client = query.get('client', [''])[0]
                if public(meta)['status'] in ACTIVE and meta.get('port'):
                    result = worker_request(meta, '/read?' + urlencode({'offset': offset, 'client': client}))
                else:
                    result = read_log(meta, offset)
                result['detach_epoch'] = meta.get('detach_epoch', 0)
                return self.send(200, result)
            assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css',
                      '/vendor/xterm.js': 'vendor/xterm.js', '/vendor/xterm.css': 'vendor/xterm.css',
                      '/vendor/addon-fit.js': 'vendor/addon-fit.js'}
            if parsed.path in assets:
                path = HERE / 'static' / assets[parsed.path]
                content_type = 'text/html; charset=utf-8' if path.suffix == '.html' else (
                    'application/javascript' if path.suffix == '.js' else 'text/css')
                return self.send(200, path.read_bytes(), content_type)
            self.send(404, {'error': 'Not found'})
        except (OSError, ValueError, HTTPError) as exc:
            self.send(400, {'error': str(exc)})

    def do_POST(self):
        if not self.valid_host() or not self.authorized() or not token_matches(
                self.headers.get('X-WinScreen-Token', ''), self.server.token):
            return self.send(403, {'error': 'Unauthorized'})
        try:
            body = json_body(self)
            if self.path == '/api/create':
                meta = create(body['name'], body.get('cwd'), body.get('shell', 'powershell'),
                              body.get('command') or None, body.get('env_python') or None)
                return self.send(200, {'session': public(meta)})
            if self.path == '/api/shutdown':
                self.send(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            match = re.fullmatch(r'/api/session/([a-f0-9]{12})/(write|resize|detach|stop|rerun|archive)', self.path)
            if not match:
                return self.send(404, {'error': 'Not found'})
            meta = resolve(match[1])
            action = match[2]
            if action == 'archive':
                if public(meta)['status'] in ACTIVE:
                    raise ValueError('正在运行的会话不能归档。')
                (data_root() / 'sessions' / meta['id'] / 'archived').touch()
                result = {'ok': True}
            elif action == 'rerun':
                result = {'session': public(create(body.get('name', meta['name'] + '-again'),
                    meta['cwd'], meta['shell'], meta.get('command'), meta.get('env_python'), meta.get('argv')))}
            else:
                result = worker_request(meta, '/' + action, body)
            self.send(200, result)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, HTTPError) as exc:
            self.send(400, {'error': str(exc)})


def run_panel(port):
    with file_lock(data_root() / 'panel.lock'):
        server = LocalServer(('127.0.0.1', port), PanelHandler)
        server.token = secrets.token_urlsafe(32)
        info = dict(pid=os.getpid(), stamp=winprocess.stamp(os.getpid()), port=server.server_port, token=server.token)
        save(data_root() / 'panel.json', info)
        try:
            server.serve_forever()
        finally:
            server.server_close()


def cli(argv=None):
    parser = argparse.ArgumentParser(prog='screen', description='Windows 持久会话。Ctrl+A D 分离。')
    parser.add_argument('--version', action='version', version='WinScreen ' + VERSION)
    parser.add_argument('-S', dest='name', help='新建/指定会话名称')
    parser.add_argument('-ls', '-list', action='store_true', dest='list_sessions')
    parser.add_argument('-r', '-R', dest='resume', nargs='?', const='')
    parser.add_argument('-d', '-D', action='store_true', dest='detach')
    parser.add_argument('-m', action='store_true', dest='background')
    parser.add_argument('-dmS', '-mdS', dest='detached_name')
    parser.add_argument('-dm', action='store_true', dest='background_pair')
    parser.add_argument('-X', dest='remote_action', nargs='+', help='quit 或 stuff TEXT')
    parser.add_argument('--panel', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--cwd', default=None)
    parser.add_argument('--shell', choices=['powershell', 'cmd'], default='powershell')
    parser.add_argument('--python', dest='env_python')
    parser.add_argument('--command', dest='shell_command', help='使用指定 shell 执行一条完整命令')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='可执行程序与参数')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        raise ValueError('请在 Windows 10 1809+/Windows 11 或 Windows Server 2019+ 使用。')
    if args.panel:
        return open_panel(not args.no_browser, args.port)
    if args.list_sessions:
        entries = [m for m in sessions() if m['status'] in ACTIVE]
        print('There %s screens on:' % ('are' if len(entries) != 1 else 'is'))
        for m in entries:
            print('\t%s.%s\t(%s)' % (m['id'], m['name'], 'Attached' if m.get('attached_count', 0) else 'Detached'))
        print('%s Socket%s in %s.' % (len(entries), '' if len(entries) == 1 else 's', data_root()))
        return
    if args.remote_action:
        if not args.name:
            raise ValueError('-X 需要 -S 会话名或ID。')
        meta = resolve(args.name, active_only=True)
        action = args.remote_action[0]
        if action == 'quit':
            worker_request(meta, '/stop', {})
        elif action == 'stuff':
            worker_request(meta, '/write', {'text': ' '.join(args.remote_action[1:])})
        else:
            raise ValueError('支持 -X quit 和 -X stuff TEXT。')
        return
    if args.resume is not None:
        selector = args.resume or args.name
        if not selector:
            live = [m for m in sessions() if m['status'] in ACTIVE]
            if len(live) != 1:
                raise ValueError('多个或没有会话，请指定 screen -r NAME。')
            selector = live[0]['id']
        meta = resolve(selector, active_only=True)
        if args.detach:
            worker_request(meta, '/detach-all', {})
            time.sleep(.6)
        elif meta.get('attached_count', 0):
            raise ValueError('会话已被客户端进入。请使用 screen -d -r ' + selector + ' 接管。')
        return attach(meta)
    if args.detach and args.name and not (args.background or args.background_pair):
        return worker_request(resolve(args.name, True), '/detach-all', {})
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    launch = args.shell_command
    if command:
        if launch:
            raise ValueError('--command 与程序位置参数不能同时指定。')
        args.shell = 'cmd'
    background = bool(args.detached_name or args.background or args.background_pair or args.detach)
    if not background and (not sys.stdin.isatty() or not winprocess.console_input_available()):
        raise ValueError('新建并进入需要交互终端；后台请使用 -dmS，SSH 请使用 -t。')
    name = args.detached_name or args.name or ('session-' + time.strftime('%H%M%S'))
    meta = create(name, args.cwd, args.shell, launch, args.env_python, command or None)
    if background:
        print('[detached %s.%s]' % (meta['id'], meta['name']))
    else:
        attach(meta)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '_worker':
        return run_worker(sys.argv[2])
    if len(sys.argv) > 1 and sys.argv[1] == '_panel':
        return run_panel(int(sys.argv[sys.argv.index('--port') + 1]))
    try:
        cli()
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print('screen: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
