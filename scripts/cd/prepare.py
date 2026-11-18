"""Prepare an isolated smoke dataset and export least-privilege runtime files."""
from pathlib import Path
import shutil
import sys

sys.path.insert(0, '/app/scripts/ci')
from bootstrap import bootstrap
from config import FILES

bootstrap()
for name in FILES:
    target = Path('/run/config') / name
    if target.exists():
        raise RuntimeError('Existing exported configuration is refused')
    if name == '.env':
        target.write_text('GEMINI_MODEL=models/gemini-3.5-flash-lite\nGEMINI_API_KEY=\n')
    else:
        shutil.copyfile(Path('/app') / name, target)
    target.chmod(0o600)
shutil.copyfile('/app/.env.api', '/run/frontend/.env.api')
Path('/run/frontend/.env.api').chmod(0o600)
print('Temporary dataset and least-privilege runtime configuration exported')
