"""Windows integration tests. Uses isolated temporary data, never user sessions."""
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import screen
import winprocess


def wait_until(fn, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(.06)
    raise AssertionError('Timed out: ' + str(fn))


@unittest.skipUnless(os.name == 'nt', 'Windows required')
class ScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='WinScreen 测试 ')
        cls.previous = os.environ.get('WINSCREEN_DIR')
        os.environ['WINSCREEN_DIR'] = cls.temp.name

    @classmethod
    def tearDownClass(cls):
        for item in screen.sessions(True):
            if item['status'] in screen.ACTIVE:
                try:
                    screen.worker_request(screen.resolve(item['id']), '/stop', {})
                except (OSError, ValueError):
                    pass
        info = screen.panel_info()
        if info:
            request = Request('http://127.0.0.1:%s/api/shutdown' % info['port'], data=b'{}',
                              headers={'Cookie': 'winscreen=' + info['token'],
                                       'X-WinScreen-Token': info['token']})
            with urlopen(request, timeout=5):
                pass
        time.sleep(1)
        if cls.previous is None:
            os.environ.pop('WINSCREEN_DIR', None)
        else:
            os.environ['WINSCREEN_DIR'] = cls.previous
        cls.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'screen.py'), *args],
                              capture_output=True, encoding='utf-8', errors='replace', timeout=20)

    def stopped(self, sid):
        return wait_until(lambda: (m := screen.resolve(sid))['status'] not in screen.ACTIVE and m)

    def log(self, sid):
        return base64.b64decode(screen.read_log(screen.resolve(sid), 0, 262144)['data']).decode('utf-8')

    def wait_log(self, sid, marker):
        try:
            return wait_until(lambda: marker in self.log(sid))
        except AssertionError:
            self.fail('Missing ' + marker + '; terminal tail: ' + repr(self.log(sid)[-6000:]))

    def test_01_powershell_success_and_failure(self):
        a = screen.create('success', command="Write-Output '训练输出'; Start-Sleep -Milliseconds 300")
        self.assertEqual(self.stopped(a['id'])['exit_code'], 0)
        self.assertIn('训练输出', self.log(a['id']))
        b = screen.create('failure', command='exit 7')
        ended = self.stopped(b['id'])
        self.assertEqual((ended['status'], ended['exit_code']), ('failed', 7))

    def test_02_cmd_and_positional_command(self):
        result = self.cli('-dmS', 'cmd-test', sys.executable, '-u', '-c',
                          "import sys; print('CMD_OK', flush=True); sys.exit(3)")
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = screen.resolve('cmd-test')
        self.assertEqual(self.stopped(meta['id'])['exit_code'], 3)
        self.assertIn('CMD_OK', self.log(meta['id']))

    def test_03_interactive_input_and_duplicate(self):
        meta = screen.create('interactive')
        wait_until(lambda: 'PS ' in self.log(meta['id']))
        time.sleep(.5)  # Test input after the shell has negotiated Win32 mode.
        with self.assertRaises(ValueError):
            screen.create('interactive')
        self.assertEqual(self.cli('-ls').returncode, 0)
        screen.worker_request(meta, '/write', {'text': "Write-Output ('INTERACTIVE_' + 'OK')\r"})
        wait_until(lambda: 'INTERACTIVE_OK' in self.log(meta['id']))
        result = self.cli('-S', 'interactive', '-X', 'stuff', "Write-Output ('STUFF_' + 'OK')\r")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_log(meta['id'], 'STUFF_OK')
        self.assertEqual(self.cli('-S', 'interactive', '-X', 'quit').returncode, 0)
        self.assertEqual(self.stopped(meta['id'])['status'], 'stopped')

    def test_04_parent_exit_does_not_kill_session(self):
        code = "import screen,time; screen.create('parent-gone', command='Start-Sleep -Seconds 30'); time.sleep(60)"
        parent = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            meta = wait_until(lambda: next((m for m in screen.sessions()
                              if m['name'] == 'parent-gone' and m['status'] == 'running'), None))
        finally:
            parent.kill()
            parent.communicate(timeout=5)
        time.sleep(.5)
        self.assertEqual(screen.public(screen.resolve(meta['id']))['status'], 'running')
        self.assertIsNotNone(winprocess.stamp(meta['worker_pid']))
        screen.worker_request(screen.resolve(meta['id']), '/stop', {})
        self.stopped(meta['id'])

    def test_05_stop_kills_grandchild_tree(self):
        command = ("$p = Start-Process -FilePath '" + sys.executable.replace("'", "''") +
                   "' -ArgumentList '-c \"import time; time.sleep(90)\"' -PassThru -WindowStyle Hidden; "
                   "Write-Output ('GRANDCHILD=' + $p.Id); Start-Sleep -Seconds 90")
        meta = screen.create('tree-stop', command=command)
        import re
        match = wait_until(lambda: re.search(r'GRANDCHILD=(\d+)', self.log(meta['id'])))
        pid = int(match[1])
        self.assertIsNotNone(winprocess.stamp(pid))
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])
        wait_until(lambda: winprocess.stamp(pid) is None)
        wait_until(lambda: winprocess.stamp(meta['child_pid']) is None)

    def test_06_identity_and_worker_auth(self):
        meta = screen.create('auth-test')
        with self.assertRaises(HTTPError) as caught:
            urlopen('http://127.0.0.1:%s/read' % meta['port'], timeout=3)
        self.assertEqual(caught.exception.code, 403)
        fake = dict(meta, worker_stamp='wrong-creation-time')
        with self.assertRaises(ValueError):
            screen.worker_request(fake, '/stop', {})
        self.assertEqual(screen.public(fake)['status'], 'interrupted')
        self.assertNotIn('token', screen.public(meta))
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])

    def test_07_panel_restart_preserves_session_and_auth(self):
        meta = screen.create('panel-persistence')
        wait_until(lambda: 'PS ' in self.log(meta['id']))
        info = screen.open_panel(browser=False, port=0)
        base = 'http://127.0.0.1:%s' % info['port']
        with self.assertRaises(HTTPError) as caught:
            urlopen(base + '/api/state', timeout=3)
        self.assertEqual(caught.exception.code, 403)
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(base + '/?key=' + info['token'], timeout=3) as response:
            self.assertIn('WinScreen', response.read().decode('utf-8'))
        with opener.open(base + '/api/state') as response:
            state = json.load(response)
        self.assertEqual(state['csrf'], info['token'])
        with self.assertRaises(HTTPError) as caught:
            opener.open(Request(base + '/api/create', data=b'{}'))
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            opener.open(Request(base + '/health', headers={'Host': 'evil.example'}))
        self.assertEqual(caught.exception.code, 403)
        for asset in ['/app.js', '/style.css', '/vendor/xterm.js', '/vendor/addon-fit.js']:
            with opener.open(base + asset) as response:
                self.assertGreater(len(response.read()), 100)
        shutdown = Request(base + '/api/shutdown', data=b'{}',
                           headers={'X-WinScreen-Token': info['token']})
        with opener.open(shutdown):
            pass
        wait_until(lambda: screen.panel_info() is None)
        self.assertEqual(screen.public(screen.resolve(meta['id']))['status'], 'running')
        second = screen.open_panel(browser=False, port=0)
        self.assertNotEqual(second['stamp'], info['stamp'])
        screen.worker_request(meta, '/write', {'text': "Write-Output ('AFTER_PANEL_' + 'RESTART')\r"})
        wait_until(lambda: 'AFTER_PANEL_RESTART' in self.log(meta['id']))
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])

    def test_08_background_option_variants(self):
        result = self.cli('-d', '-m', '-S', 'split-flags', '--command', 'Start-Sleep -Seconds 20')
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = screen.resolve('split-flags')
        self.assertEqual(meta['status'], 'running')
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])

    def test_09_terminal_attach_ctrl_a_d_and_resume(self):
        from winpty import PTY, Backend
        meta = screen.create('attach-test')
        for args in [('-r', 'attach-test'), ('-d', '-r', 'attach-test')]:
            terminal = PTY(110, 30, backend=Backend.ConPTY)
            terminal.spawn(str(ROOT / 'screen.exe'), cmdline=' ' + subprocess.list2cmdline(args), cwd=str(ROOT))
            output = ''
            def poll():
                nonlocal output
                try:
                    chunk = terminal.read(65536, blocking=False)
                except Exception as exc:
                    if 'EOF' not in str(exc):
                        raise
                    chunk = ''
                if chunk:
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode('utf-8', errors='replace')
                    if '\x1b[6n' in chunk:
                        terminal.write('\x1b[1;1R')
                    output += chunk
                return '正在进入' in output
            wait_until(poll)
            wait_until(lambda: 'PS ' in self.log(meta['id']))
            terminal.write("Write-Output ('CLI_' + 'OK')\r")
            wait_until(lambda: (poll(), 'CLI_OK' in output)[1])
            terminal.write('\x01d')
            wait_until(lambda: (poll(), not terminal.isalive())[1])
            self.assertIn('detached from', output)
            self.assertEqual(screen.public(screen.resolve(meta['id']))['status'], 'running')
            del terminal
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])

    def test_10_spaces_unicode_working_directory(self):
        work = Path(self.temp.name) / '中文 工作目录'
        work.mkdir(exist_ok=True)
        meta = screen.create('中文会话', cwd=str(work), command="Write-Output (Get-Location).Path")
        self.stopped(meta['id'])
        # Windows CI TEMP may use RUNNER~1 (8.3); PowerShell returns its full
        # name. Compare the canonical path, not two spellings of the same path.
        self.assertIn(str(work.resolve()).casefold(), self.log(meta['id']).casefold())

    def test_11_breakaway_survives_parent_job_close(self):
        code = ("import screen,winprocess,ctypes,time; "
                "job=winprocess.own_worker_job(); limits=winprocess.ExtendedLimits(); "
                "limits.BasicLimitInformation.LimitFlags=0x2000|0x800; "
                "assert winprocess.k32.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)); "
                "screen.create('job-breakaway',command='Start-Sleep -Seconds 30'); time.sleep(60)")
        parent = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            meta = wait_until(lambda: next((m for m in screen.sessions()
                              if m['name'] == 'job-breakaway' and m['status'] == 'running'), None))
        finally:
            parent.kill()
            parent.communicate(timeout=5)
        time.sleep(.5)
        self.assertEqual(screen.public(screen.resolve(meta['id']))['status'], 'running')
        screen.worker_request(screen.resolve(meta['id']), '/stop', {})
        self.stopped(meta['id'])

    def test_12_blocked_parent_job_fails_explicitly(self):
        code = ("import screen,winprocess; job=winprocess.own_worker_job(); "
                "screen.create('job-blocked')")
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                                capture_output=True, encoding='utf-8', errors='replace', timeout=10,
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Job Object', result.stderr)
        self.assertIn('start-panel.cmd', result.stderr)
        self.assertEqual(screen.resolve('job-blocked')['status'], 'failed')

    def test_13_late_enter_and_ctrl_c_interrupt(self):
        meta = screen.create('ctrl-c-test')
        wait_until(lambda: 'PS ' in self.log(meta['id']))
        time.sleep(.5)
        command = ('& "' + sys.executable + '" -u -c "import time; '
                   "print('RUN_'+'START',flush=True); time.sleep(90)\"\r")
        screen.worker_request(meta, '/write', {'text': command})
        self.wait_log(meta['id'], 'RUN_START')
        screen.worker_request(meta, '/write', {'text': '\x03'})
        self.wait_log(meta['id'], 'KeyboardInterrupt')
        self.assertEqual(screen.public(screen.resolve(meta['id']))['status'], 'running')
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])

    def test_14_browser_focus_report_does_not_corrupt_command(self):
        meta = screen.create('focus-test')
        wait_until(lambda: 'PS ' in self.log(meta['id']))
        screen.worker_request(meta, '/write', {'text': '\x1b[I'})
        screen.worker_request(meta, '/write', {'text': "Write-Output ('FOCUS_' + 'OK')\r"})
        self.wait_log(meta['id'], 'FOCUS_OK')
        screen.worker_request(meta, '/write', {'text': '\x1b[O'})
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])

    def test_15_native_launcher_preserves_stuff_carriage_return(self):
        launcher = ROOT / 'screen.exe'
        self.assertTrue(launcher.is_file(), 'Run install.ps1 to compile the native launcher.')
        meta = screen.create('native-launcher')
        wait_until(lambda: 'PS ' in self.log(meta['id']))
        result = subprocess.run([str(launcher), '-S', meta['name'], '-X', 'stuff',
                                 "Write-Output ('NATIVE_'+'OK')\r"],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_log(meta['id'], 'NATIVE_OK')
        screen.worker_request(meta, '/stop', {})
        self.stopped(meta['id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
