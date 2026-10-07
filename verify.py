"""Save actual test results and hashes; skipped Windows tests cannot pass release."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from release import implementation_hashes, version


def configure_console():
    # English Windows runners default redirected stdout to cp1252. Preserve
    # Unicode test diagnostics without hiding the real test failure.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            reconfigure(encoding='utf-8', errors='backslashreplace')


def verify(root):
    configure_console()
    zone = timezone(timedelta(hours=8))
    started = datetime.now(zone).isoformat()
    hashes_before = implementation_hashes(root)
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    returncode = 1
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=300,
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        output = result.stderr + '\n' + result.stdout
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        output = 'Verification timed out after 300 seconds. Release rejected.\n'
    count = re.search(r'Ran (\d+) tests?', output)
    skipped = len(re.findall(r'\.\.\. skipped ', output))
    hashes_after = implementation_hashes(root)
    passed = (os.name == 'nt' and returncode == 0 and '\nOK\n' in output
              and count is not None and int(count[1]) > 0 and skipped == 0
              and hashes_before == hashes_after)
    checks = root / 'checks'
    checks.mkdir(exist_ok=True)
    (checks / 'test_output.log').write_text(output, encoding='utf-8')
    record = dict(version=version(root), started=started, ended=datetime.now(zone).isoformat(),
                  timezone='Asia/Shanghai', platform=platform.platform(), python=sys.version,
                  pywinpty=importlib.metadata.version('pywinpty'),
                  command='python -m unittest discover -s tests -v', returncode=returncode,
                  test_count=int(count[1]) if count else 0, skipped_count=skipped,
                  passed=passed, implementation_unchanged=hashes_before == hashes_after,
                  source_sha256=hashes_after,
                  test_output_sha256=hashlib.sha256((checks / 'test_output.log').read_bytes()).hexdigest(),
                  scope='Local Windows integration/unit tests; no live remote SSH disconnect or GPU training test.')
    (checks / 'verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output)
    print('Verification record:', checks / 'verification.json')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(verify(Path(__file__).resolve().parent))
