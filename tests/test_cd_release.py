"""Publication gates reject failed, foreign, stale and mutable releases."""
from copy import deepcopy
from pathlib import Path
import re
import os
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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

    def test_wrong_image_revision_stops_before_deployment(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            docker = temp / 'docker'
            docker.write_text("""#!/bin/sh
case "$1 $2" in
  'volume inspect') exit 1 ;;
  'image inspect') printf '%s\n' 'wrong-revision'; exit 0 ;;
  *) printf '%s\n' unexpected >> "$CD_TEST_MARKER"; exit 3 ;;
esac
""")
            docker.chmod(0o700)
            marker = temp / 'unexpected-compose'
            result = subprocess.run(['bash', str(root / 'scripts/cd/validate.sh')], cwd=temp,
                                    capture_output=True, text=True, env=os.environ | {
                'PATH': str(temp) + os.pathsep + os.environ['PATH'], 'BACKEND_IMAGE': 'backend:test',
                'FRONTEND_IMAGE': 'frontend:test', 'RELEASE_COMMIT': 'a' * 40,
                'COMPOSE_PROJECT_NAME': 'dataquery-cd-unit', 'CD_TEST_MARKER': str(marker)})
            self.assertEqual(result.returncode, 1)
            self.assertIn('Image source revision mismatch', result.stderr)
            self.assertFalse(marker.exists())

    def test_failed_rerun_does_not_hide_previous_stable_artifact(self):
        from scripts.cd.release import previous
        responses = [
            {'workflow_runs': [{'id': 20, 'conclusion': 'failure'}, {'id': 10, 'conclusion': 'success'}]},
            {'artifacts': [{'name': 'release-manifest-1', 'created_at': '2026-11-18T04:00:00Z', 'id': 1}]},
            {'artifacts': [{'name': 'release-manifest-2', 'created_at': '2026-11-18T05:00:00Z', 'id': 2}]},
        ]
        with patch('scripts.cd.release.api', side_effect=responses), patch(
                'scripts.cd.release.download_manifest', return_value={'validated': True}) as download:
            self.assertEqual(previous(), {'validated': True})
        self.assertEqual(download.call_args.args[0]['id'], 2)

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
