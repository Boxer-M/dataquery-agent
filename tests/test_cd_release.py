"""Publication gates reject failed, foreign, stale and mutable releases."""
from copy import deepcopy
from pathlib import Path
import re
import unittest

from scripts.cd.release import REPOSITORY, check_ci, manifest


class ReleaseSafetyTests(unittest.TestCase):
    def test_failed_or_unmerged_ci_cannot_publish(self):
        sha = 'a' * 40
        run = {'status': 'completed', 'conclusion': 'success', 'event': 'push', 'head_branch': 'main',
               'head_sha': sha, 'path': '.github/workflows/ci.yml', 'head_repository': {'full_name': REPOSITORY}}
        check_ci(run, sha, sha)
        for mutation in ({'conclusion': 'failure'}, {'head_branch': 'feature/demo'},
                         {'event': 'pull_request'}, {'path': '.github/workflows/other.yml'},
                         {'head_repository': {'full_name': 'foreign/repo'}}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                check_ci(run | mutation, sha, sha)
        with self.assertRaises(ValueError):
            check_ci(run, sha, 'b' * 40)

    def test_stable_manifest_requires_validated_immutable_pair(self):
        release = {'version': 1, 'validated': True, 'repository': REPOSITORY, 'source_commit': 'a' * 40,
                   'images': {role: f'ghcr.io/{REPOSITORY}-{role}@sha256:' + 'b' * 64
                              for role in ('backend', 'frontend')}}
        manifest(release)
        for key, value in [('validated', False), ('source_commit', 'main'), ('repository', 'foreign/repo')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                manifest(release | {key: value})
        altered = deepcopy(release)
        altered['images']['backend'] = f'ghcr.io/{REPOSITORY}-backend:latest'
        with self.assertRaises(ValueError):
            manifest(altered)

    def test_container_lock_covers_existing_runtime_pins(self):
        root = Path(__file__).resolve().parents[1]
        normalize = lambda name: re.sub(r'[-_.]+', '-', name.split('[')[0].lower())
        def pins(path):
            return {normalize(name): version for name, version in
                    re.findall(r'^([^#\s=]+)==([^\s]+)$', path.read_text(), flags=re.M)}
        locked = pins(root / 'requirements-container.lock')
        for path in ('requirements.txt', 'requirements-langgraph.txt', 'requirements-pdf.txt', 'requirements-redis.txt'):
            for name, version in pins(root / path).items():
                self.assertEqual(locked[name], version, name)
