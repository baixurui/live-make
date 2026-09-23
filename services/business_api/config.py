import os
import re
from pathlib import Path


def load_env(path: Path | None = None):
    path = path or Path(__file__).resolve().parents[2] / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        match = re.fullmatch(r'\s*([A-Z][A-Z0-9_]*)=(.*)', line)
        if match:
            value = match[2].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(match[1], value)
