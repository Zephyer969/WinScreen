"""Explicit release allowlist and a fresh-source verification gate."""
import ast
import hashlib
import json
from pathlib import Path


TOP = ['screen.py', 'winprocess.py', 'terminal_input.py', 'screen.cmd', 'screen.exe', 'launcher.cs',
       'install.cmd', 'install.ps1', 'start-panel.cmd', 'requirements.txt', 'README.md',
       'THIRD_PARTY.md', 'SECURITY.md', 'CONTRIBUTING.md', 'CHANGELOG.md',
       'verify.py', 'package.py', 'release.py', '.gitignore']
CHECKS = ['verification.json', 'test_output.log', 'release-audit.json']


def version(root):
    tree = ast.parse((Path(root) / 'screen.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'VERSION'
                                                for n in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing version')


def implementation_hashes(root):
    root = Path(root)
    names = ['screen.py', 'winprocess.py', 'terminal_input.py', 'screen.exe', 'launcher.cs',
             'install.cmd', 'install.ps1', 'start-panel.cmd', 'screen.cmd', 'requirements.txt',
             'verify.py', 'package.py', 'release.py']
    files = [root / name for name in names]
    for folder in ['static', 'tests']:
        files.extend(path for path in (root / folder).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix != '.pyc')
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


def release_files(root):
    root = Path(root)
    files = [root / name for name in TOP if (root / name).is_file()]
    if (root / 'LICENSE').is_file():
        files.append(root / 'LICENSE')
    for folder in ['static', 'tests', 'docs', '.github']:
        files.extend(path for path in (root / folder).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix != '.pyc')
    files.extend(root / 'checks' / name for name in CHECKS if (root / 'checks' / name).is_file())
    return sorted(set(files))


def verify_fresh(root, record):
    if not record.get('passed') or record.get('skipped_count', 0) or not record.get('source_sha256'):
        raise ValueError('Refusing to package: missing or unsuccessful Windows verification.')
    if record['source_sha256'] != implementation_hashes(root):
        raise ValueError('Refusing to package: implementation changed. Run verify.py again.')
    if record.get('version') != version(root):
        raise ValueError('Refusing to package: version mismatch.')
    log_path = Path(root) / 'checks' / 'test_output.log'
    if record.get('test_output_sha256') != hashlib.sha256(log_path.read_bytes()).hexdigest():
        raise ValueError('Refusing to package: verification log checksum mismatch.')


def build(root):
    import zipfile
    root = Path(root)
    record = json.loads((root / 'checks' / 'verification.json').read_text(encoding='utf-8'))
    verify_fresh(root, record)
    files = release_files(root)
    manifest = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in files}
    out = root / 'dist' / ('WinScreen-Windows-v' + version(root) + '.zip')
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, 'WinScreen/' + path.relative_to(root).as_posix())
        archive.writestr('WinScreen/PACKAGE_MANIFEST.json', json.dumps(manifest, indent=2))
    with zipfile.ZipFile(out) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC verification failed')
        for filename, digest in manifest.items():
            if hashlib.sha256(archive.read('WinScreen/' + filename)).hexdigest() != digest:
                raise ValueError('ZIP manifest mismatch: ' + filename)
    return out, len(manifest) + 1, hashlib.sha256(out.read_bytes()).hexdigest()
