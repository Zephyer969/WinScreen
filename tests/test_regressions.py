"""Release regressions; all workers and panels use an isolated temporary root."""
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import screen
import winprocess
from test_screen import wait_until


@unittest.skipUnless(os.name == 'nt', 'Windows required')
class ReleaseRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='WinScreen release tests ')
        cls.previous = os.environ.get('WINSCREEN_DIR')
        os.environ['WINSCREEN_DIR'] = cls.temp.name

    @classmethod
    def tearDownClass(cls):
        for item in screen.sessions(True):
            if item['status'] in screen.ACTIVE:
                meta = screen.resolve(item['id'])
                try:
                    screen.worker_request(meta, '/stop', {})
                    wait_until(lambda: winprocess.stamp(meta['worker_pid']) is None)
                except (OSError, ValueError):
                    pass
        info = screen.panel_info()
        if info:
            request = Request('http://127.0.0.1:%s/api/shutdown' % info['port'], data=b'{}',
                              headers={'Cookie': 'winscreen=' + info['token'],
                                       'X-WinScreen-Token': info['token']})
            with urlopen(request, timeout=5):
                pass
            wait_until(lambda: winprocess.stamp(info['pid']) is None)
        if cls.previous is None:
            os.environ.pop('WINSCREEN_DIR', None)
        else:
            os.environ['WINSCREEN_DIR'] = cls.previous
        cls.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'screen.py'), *args],
                              stdin=subprocess.DEVNULL, capture_output=True,
                              encoding='utf-8', errors='replace', timeout=20)

    def log(self, selector):
        meta = screen.resolve(selector)
        return base64.b64decode(screen.read_log(meta, 0, 262144)['data']).decode('utf-8')

    def panel(self):
        info = screen.open_panel(browser=False, port=0)
        base = 'http://127.0.0.1:%s' % info['port']
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(base + '/?key=' + info['token'], timeout=5):
            pass
        return info, base, opener

    def test_16_positional_arguments_are_not_reparsed_by_cmd(self):
        # Even quoted %NAME% is expanded by CMD; these must be literal argv.
        payload = '%WINSCREEN_DIR%'
        result = self.cli('-dmS', 'literal-argv', sys.executable, '-u', '-c',
                          'import sys; print(repr(sys.argv[1]), flush=True)', payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_until(lambda: screen.resolve('literal-argv')['status'] not in screen.ACTIVE)
        self.assertIn(repr(payload), self.log('literal-argv'))
        for number, payload in enumerate(['a&b', 'a|b', 'a^b', 'quoted "text"', 'two words', '末尾\\']):
            with self.subTest(payload=payload):
                name = 'argv-extra-' + str(number)
                result = self.cli('-dmS', name, sys.executable, '-u', '-c',
                                  'import sys; print(repr(sys.argv[1]), flush=True)', payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                wait_until(lambda: screen.resolve(name)['status'] not in screen.ACTIVE)
                self.assertIn(repr(payload), self.log(name))

    def test_17_non_object_json_has_controlled_error(self):
        info, base, opener = self.panel()
        for data in (b'[]', b'null', b'"invalid"', b'{"name": 12}'):
            with self.subTest(data=data):
                request = Request(base + '/api/create', data=data,
                                  headers={'X-WinScreen-Token': info['token']})
                with self.assertRaises(HTTPError) as caught:
                    opener.open(request, timeout=5)
                self.assertEqual(caught.exception.code, 400)
                self.assertIn('error', json.load(caught.exception))
        with opener.open(base + '/api/state', timeout=5) as response:
            self.assertIsInstance(json.load(response)['sessions'], list)

    def test_18_non_ascii_credentials_are_rejected_not_crashed(self):
        info, base, _ = self.panel()
        for path, headers in [('/?key=%E4%B8%AD', {}),
                              ('/api/state', {'Cookie': 'winscreen="\xe9"'}),
                              ('/api/state', {'Cookie': 'winscreen="unterminated'})]:
            with self.subTest(path=path, headers=headers):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + path, headers=headers), timeout=5)
                self.assertEqual(caught.exception.code, 403)
        meta = screen.create('invalid-bearer')
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request('http://127.0.0.1:%s/read' % meta['port'],
                            headers={'Authorization': 'Bearer \xe9'}), timeout=5)
        self.assertEqual(caught.exception.code, 403)
        screen.worker_request(meta, '/stop', {})

    def test_19_corrupted_metadata_does_not_break_listing(self):
        root = screen.data_root() / 'sessions'
        bad = root / 'aaaaaaaaaaaa'
        bad.mkdir()
        try:
            for value in (['not-a-record'], {'id': '../outside'}, {'id': 'aaaaaaaaaaaa'}):
                screen.save(bad / 'session.json', value)
                self.assertIsInstance(screen.sessions(), list)
        finally:
            (bad / 'session.json').unlink()
            bad.rmdir()

    def test_20_closing_cmd_launcher_preserves_output(self):
        heartbeat = "for($i=0;$i -lt 150;$i++){Write-Output ('CMDKEEP_'+$i);Start-Sleep -Milliseconds 100}"
        command = 'call ' + subprocess.list2cmdline([str(ROOT / 'screen.exe'), '-dmS',
                                                    'cmd-close-release', '--command', heartbeat])
        # CMD does not use the C-runtime backslash quoting convention. Build
        # its command tail directly, rather than list2cmdline of the whole tail.
        cmdline = subprocess.list2cmdline([os.environ['COMSPEC']]) + ' /d /k ' + command
        parent = subprocess.Popen(cmdline, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_until(lambda: any(m['name'] == 'cmd-close-release' and m['status'] == 'running'
                                   for m in screen.sessions()))
            def ticks():
                return [int(n) for n in re.findall(r'CMDKEEP_(\d+)', self.log('cmd-close-release'))]
            before = wait_until(lambda: (seq := ticks()) and len(seq) >= 3 and max(seq))
            self.assertIsNone(parent.poll())
            parent.terminate()
            parent.wait(timeout=5)
            wait_until(lambda: (seq := ticks()) and max(seq) >= before + 5)
            meta = screen.resolve('cmd-close-release')
            self.assertEqual(screen.public(meta)['status'], 'running')
            self.assertIn('cmd-close-release', self.cli('-ls').stdout)
            screen.worker_request(meta, '/stop', {})
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            parent.stdin.close()

    def test_21_non_interactive_creation_does_not_leave_worker(self):
        result = self.cli('-S', 'needs-terminal')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('交互终端', result.stderr)
        self.assertFalse(any(m['name'] == 'needs-terminal' for m in screen.sessions()))

    def test_22_concurrent_create_is_serialized(self):
        code = "import screen; screen.create('race-name', command='Start-Sleep -Seconds 20')"
        children = [subprocess.Popen([sys.executable, '-c', code], cwd=ROOT,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        try:
            outputs = [child.communicate(timeout=20) for child in children]
            self.assertEqual(sum(child.returncode == 0 for child in children), 1)
            for child, (_, error) in zip(children, outputs):
                if child.returncode:
                    self.assertIn('同名会话'.encode('utf-8'), error)
            screen.worker_request(screen.resolve('race-name'), '/stop', {})
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)

    def test_23_download_log_and_archive_preserves_file(self):
        meta = screen.create('download-test', command="Write-Output 'DOWNLOAD_OK'")
        wait_until(lambda: screen.resolve(meta['id'])['status'] not in screen.ACTIVE)
        info, base, opener = self.panel()
        with opener.open(base + '/api/session/' + meta['id'] + '/download', timeout=5) as response:
            self.assertIn(b'DOWNLOAD_OK', response.read())
            self.assertIn('attachment', response.headers['Content-Disposition'])
        request = Request(base + '/api/session/' + meta['id'] + '/archive', data=b'{}',
                          headers={'X-WinScreen-Token': info['token']})
        with opener.open(request, timeout=5):
            pass
        self.assertFalse(any(m['id'] == meta['id'] for m in screen.sessions()))
        self.assertIn('DOWNLOAD_OK', self.log(meta['id']))

    def test_24_rerun_retains_structured_arguments(self):
        meta = screen.create('rerun-original', shell='cmd',
                             argv=[sys.executable, '-c', "print('RERUN_%WINSCREEN_DIR%')"])
        wait_until(lambda: screen.resolve(meta['id'])['status'] not in screen.ACTIVE)
        info, base, opener = self.panel()
        request = Request(base + '/api/session/' + meta['id'] + '/rerun',
                          data=b'{"name":"rerun-copied"}', headers={'X-WinScreen-Token': info['token']})
        with opener.open(request, timeout=5) as response:
            repeated = json.load(response)['session']
        wait_until(lambda: screen.resolve(repeated['id'])['status'] not in screen.ACTIVE)
        self.assertEqual(repeated['argv'], meta['argv'])
        self.assertIn('RERUN_%WINSCREEN_DIR%', self.log(repeated['id']))

    def test_25_invalid_worker_requests_and_bounds(self):
        meta = screen.create('worker-invalid')
        base = 'http://127.0.0.1:%s' % meta['port']
        for path, data in [('/write', b'[]'), ('/resize', b'{"cols":"wrong","rows":20}'),
                           ('/write', b'{}'), ('/unknown', b'{}')]:
            with self.subTest(path=path, data=data):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + path, data=data,
                                    headers={'Authorization': 'Bearer ' + meta['token']}), timeout=5)
                self.assertEqual(caught.exception.code, 400)
        self.assertTrue(screen.worker_request(meta, '/resize', {'cols': -10, 'rows': 1000})['ok'])
        info, panel_base, opener = self.panel()
        with self.assertRaises(HTTPError) as caught:
            opener.open(Request(panel_base + '/api/create', data=b'{}',
                                headers={'X-WinScreen-Token': info['token'], 'Content-Length': '999999'}), timeout=5)
        self.assertEqual(caught.exception.code, 400)
        screen.worker_request(meta, '/stop', {})

    def test_26_failed_program_does_not_claim_running(self):
        with self.assertRaises(ValueError):
            screen.create('missing-executable', argv=['does-not-exist-winscreen.exe'])
        self.assertEqual(screen.resolve('missing-executable')['status'], 'failed')
        with self.assertRaises(ValueError):
            screen.create('empty-argv', argv=[])

    def test_27_corrupt_panel_record_is_ignored(self):
        # Stop only this isolated panel before exercising its discovery record.
        info = screen.panel_info()
        if info:
            request = Request('http://127.0.0.1:%s/api/shutdown' % info['port'], data=b'{}',
                              headers={'Cookie': 'winscreen=' + info['token'],
                                       'X-WinScreen-Token': info['token']})
            with urlopen(request, timeout=5):
                pass
            wait_until(lambda: winprocess.stamp(info['pid']) is None)
        for value in (['invalid'], {'pid': 1}, {'pid': 'wrong', 'stamp': 'x', 'port': 2, 'token': 'x'}):
            screen.save(screen.data_root() / 'panel.json', value)
            self.assertIsNone(screen.panel_info())


if __name__ == '__main__':
    unittest.main(verbosity=2)
