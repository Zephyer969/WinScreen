"""Build a verified source/asset ZIP, never a local runtime or session directory."""
from pathlib import Path
from release import build

if __name__ == '__main__':
    try:
        out, count, digest = build(Path(__file__).resolve().parent)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
    print(out)
    print('Files:', count, 'Bytes:', out.stat().st_size)
    print('SHA256:', digest)
