"""Keep CI failure checks effective and paid evaluation outside PR triggers."""
from pathlib import Path
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from scripts.ci.security import inspect

ROOT = Path(__file__).resolve().parents[1]


class CiSafetyTests(unittest.TestCase):
    def test_public_content_check_rejects_private_paths_and_credentials(self):
        for name, content in [('.env.api', b'local'), ('data/raw/sample.csv', b'1'),
                              ('PROJECT_PLAN.md', b'private'), ('backup.dump', b'db'),
                              ('config.txt', b'-----BEGIN ' + b'PRIVATE KEY-----')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                inspect(name, content)
        inspect('.env.example', b'GEMINI_API_KEY=\n')
        inspect('README.md', b'Public project description')

    @unittest.skipUnless(yaml, "Install requirements-ci.txt for Workflow contract checks")
    def test_pr_workflow_has_no_model_secret_and_manual_job_is_gated(self):
        automatic = (ROOT / '.github/workflows/ci.yml').read_text()
        workflows = yaml.load(automatic, Loader=yaml.BaseLoader)
        self.assertIn('pull_request', workflows['on'])
        self.assertNotIn('secrets.', automatic)
        self.assertNotIn('continue-on-error', automatic)
        manual = yaml.load((ROOT / '.github/workflows/agent-evaluation.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(manual['on']), {'workflow_dispatch'})
        self.assertIn('approve_external_calls', manual['jobs']['evaluate']['if'])
        self.assertIn("refs/heads/main", manual['jobs']['evaluate']['if'])
