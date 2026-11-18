"""Bind only explicitly exported runtime credentials from a read-only volume."""
from pathlib import Path
import shutil

FILES = ('.env', '.env.api', '.env.agent-meta', '.env.metric-query', '.env.detail-query',
         '.env.dynamic-query', '.env.query-assets', '.env.knowledge', '.env.knowledge-admin',
         '.env.sqlshed-reader', '.document-review-key')


def bind_config():
    for name in FILES:
        source, target = Path('/run/config') / name, Path('/app') / name
        if not source.is_file():
            raise RuntimeError('Runtime configuration is incomplete')
        if target.is_symlink():
            raise RuntimeError('Unexpected runtime configuration symlink')
        if target.exists():
            if target.read_bytes() != source.read_bytes():
                raise RuntimeError('Existing runtime configuration differs')
            continue
        shutil.copyfile(source, target)
        target.chmod(0o600)
