"""Fail on tracked private files, large payloads and recognizable private credentials."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(rb"\bgh[pousr]_[0-9A-Za-z]{30,}\b"),
)


def inspect(name, raw):
    parts = Path(name).parts
    private = ((Path(name).name.startswith('.env') and Path(name).name != '.env.example')
               or name.startswith(('data/raw/', 'docs/local/', 'learning/'))
               or Path(name).name in {'PROJECT_PLAN.md', 'LEARNING_MAP.md', '.document-review-key'}
               or Path(name).suffix in {'.dump', '.backup', '.parquet'} or '.venv' in parts)
    if private or len(raw) > 5 * 1024 * 1024 or any(pattern.search(raw) for pattern in PATTERNS):
        raise ValueError(f"Public repository check failed: {name}")


def main():
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    for name in filter(None, files):
        inspect(name, (ROOT / name).read_bytes())
    print('Tracked credential patterns, private paths, raw data and large-file checks passed')


if __name__ == '__main__':
    main()
