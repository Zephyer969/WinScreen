"""Platform-independent input and release-gate units, not live terminal evidence."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import release
import screen
import verify
from terminal_input import record, win32_input


class InputUnits(unittest.TestCase):
    def test_ascii_has_key_down_and_up(self):
        self.assertEqual(win32_input('a'), record(65, 97))

    def test_enter_and_backspace(self):
        self.assertEqual(win32_input('\r\x7f'), record(13, 13) + record(8, 8))

    def test_ctrl_c_is_processed_etx(self):
        self.assertEqual(win32_input('\x03'), '\x03')

    def test_navigation_keys(self):
        self.assertEqual(win32_input('\x1b[A\x1b[3~'), record(38) + record(46))

    def test_protocol_sequences_are_not_keystrokes(self):
        text = '\x1b[I\x1b[O\x1b[1;1R\x1b[200~'
        self.assertEqual(win32_input(text), text)

    def test_existing_win32_records_not_reencoded(self):
        text = record(13, 13)
        self.assertEqual(win32_input(text), text)

    def test_unicode_surrogate_pair(self):
        self.assertEqual(win32_input('\U0001f331'), record(0, 0xd83c) + record(0, 0xdf31))

    def test_non_ascii_token_is_false(self):
        self.assertFalse(screen.token_matches('中', 'expected'))
        self.assertFalse(screen.token_matches(None, 'expected'))
        self.assertTrue(screen.token_matches('expected', 'expected'))


class VerificationConsoleUnits(unittest.TestCase):
    def test_cp1252_redirected_output_preserves_unicode(self):
        result = subprocess.run(
            [sys.executable, '-c',
             "import verify; verify.configure_console(); print('中文诊断', end='')"],
            cwd=ROOT, capture_output=True, timeout=10,
            env=dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '中文诊断'.encode('utf-8'))

    def test_in_memory_streams_are_supported(self):
        output = io.StringIO()
        with patch('sys.stdout', output), patch('sys.stderr', io.StringIO()):
            verify.configure_console()
            print('中文诊断')
        self.assertEqual(output.getvalue(), '中文诊断\n')


class ReleaseGateUnits(unittest.TestCase):
    def record(self, root):
        content = b'Ran 1 test\n\nOK\n'
        (root / 'checks').mkdir()
        (root / 'checks' / 'test_output.log').write_bytes(content)
        return dict(passed=True, skipped_count=0, source_sha256={'screen.py': 'tested'},
                    version='1.0.1', test_output_sha256=hashlib.sha256(content).hexdigest())

    def test_unchanged_inputs_pass(self):
        with tempfile.TemporaryDirectory() as temp, patch('release.implementation_hashes', return_value={'screen.py': 'tested'}), patch('release.version', return_value='1.0.1'):
            root = Path(temp)
            release.verify_fresh(root, self.record(root))

    def test_modified_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch('release.implementation_hashes', return_value={'screen.py': 'edited'}):
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'implementation changed'):
                release.verify_fresh(root, self.record(root))

    def test_skipped_tests_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            item = self.record(root)
            item['skipped_count'] = 1
            with self.assertRaises(ValueError):
                release.verify_fresh(root, item)

    def test_changed_log_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch('release.implementation_hashes', return_value={'screen.py': 'tested'}), patch('release.version', return_value='1.0.1'):
            root = Path(temp)
            item = self.record(root)
            (root / 'checks' / 'test_output.log').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'log checksum'):
                release.verify_fresh(root, item)

    def test_release_allowlist_excludes_local_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'checks').mkdir()
            (root / 'checks' / 'panel.json').write_text('{"token":"secret"}')
            (root / 'checks' / 'panel-preview.jpg').write_bytes(b'private screenshot')
            (root / 'checks' / 'verification.json').write_text('{}')
            (root / '.runtime').mkdir()
            (root / '.runtime' / 'secret.txt').write_text('secret')
            paths = [path.relative_to(root).as_posix() for path in release.release_files(root)]
            self.assertEqual(paths, ['checks/verification.json'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
