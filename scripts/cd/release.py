"""Gate publication and preserve immutable image pairs for manual rollback."""
import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen
from zipfile import ZipFile

REPOSITORY = os.getenv('GITHUB_REPOSITORY', 'Boxer-M/dataquery-agent').lower()
API = 'https://api.github.com/repos/' + REPOSITORY
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def api(path):
    request = Request(API + path, headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                                           'Accept': 'application/vnd.github+json', 'User-Agent': 'dataquery-cd'})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def check_ci(run, sha, main):
    required = {'status': 'completed', 'conclusion': 'success', 'event': 'push', 'head_branch': 'main',
                'head_sha': sha, 'path': '.github/workflows/ci.yml'}
    if (not SHA.fullmatch(sha) or main != sha or any(run.get(key) != value for key, value in required.items())
            or run.get('head_repository', {}).get('full_name', '').lower() != REPOSITORY):
        raise ValueError('Only current-main commits with successful push CI may publish')


def manifest(value):
    if (not isinstance(value, dict) or value.get('version') != 1 or value.get('validated') is not True
            or value.get('repository') != REPOSITORY or not SHA.fullmatch(value.get('source_commit', ''))):
        raise ValueError('Invalid stable release manifest')
    for role in ('backend', 'frontend'):
        prefix = f'ghcr.io/{REPOSITORY}-{role}@'
        image = value.get('images', {}).get(role, '')
        if not image.startswith(prefix) or not DIGEST.fullmatch(image[len(prefix):]):
            raise ValueError('Release images must be repository-scoped immutable digests')
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_manifest(artifact):
    if artifact.get('expired'):
        raise ValueError('Previous stable manifest expired; restore the saved release manifest first')
    request = Request(artifact['archive_download_url'], headers={
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'User-Agent': 'dataquery-cd'})
    try:
        response = build_opener(NoRedirect).open(request, timeout=20)
    except HTTPError as error:
        if error.code != 302 or not error.headers['Location'].startswith('https://'):
            raise
        # The signed artifact URL receives no GitHub Authorization header.
        response = urlopen(error.headers['Location'], timeout=20)
    with response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('Release artifact too large')
    with ZipFile(BytesIO(raw)) as archive:
        if archive.getinfo('release.json').file_size > 100_000:
            raise ValueError('Release manifest too large')
        return manifest(json.loads(archive.read('release.json')))


def previous():
    runs = api('/actions/workflows/cd-release.yml/runs?per_page=100')['workflow_runs']
    candidates = []
    for run in runs:
        artifacts = api(f"/actions/runs/{run['id']}/artifacts?per_page=100")['artifacts']
        candidates.extend(item for item in artifacts if re.fullmatch(r'release-manifest-[0-9]+', item['name']))
    if candidates:
        # A failed later attempt must not hide an earlier completed, validated manifest.
        return download_manifest(max(candidates, key=lambda item: item['created_at']))
    if len(runs) == 100:
        raise ValueError('Previous stable search exhausted; inspect older releases explicitly')
    return None


def reference(tag):
    result = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', tag, '--format', '{{.Manifest.Digest}}'],
                            capture_output=True, text=True)
    if result.returncode:
        if 'not found' in result.stderr.lower() or 'manifest unknown' in result.stderr.lower():
            return None
        raise ValueError('Registry inspection failed; credentials or connectivity require attention')
    digest = result.stdout.strip()
    if not DIGEST.fullmatch(digest):
        raise ValueError('Registry returned an invalid digest')
    return tag.split(':sha-', 1)[0] + '@' + digest


def gate():
    sha = os.environ['RELEASE_COMMIT']
    run = api('/actions/runs/' + str(int(os.environ['SOURCE_CI_RUN'])))
    check_ci(run, sha, api('/commits/main')['sha'])
    jobs = api(f"/actions/runs/{run['id']}/jobs?per_page=100")['jobs']
    if not all(any(j['name'] == name and j['conclusion'] == 'success' for j in jobs)
               for name in ('python', 'frontend', 'integration-smoke')):
        raise ValueError('Required CI jobs did not pass')
    return run


def prepare():
    run = gate()
    sha = os.environ['RELEASE_COMMIT']
    pending = {'version': 1, 'repository': REPOSITORY, 'source_commit': sha,
               'ci_run_id': run['id'], 'previous_stable': previous()}
    pending['tags'] = {}
    for role in ('backend', 'frontend'):
        tag = (f'ghcr.io/{REPOSITORY}-{role}:sha-{sha}'
               f"-run-{int(os.environ['GITHUB_RUN_ID'])}-{int(os.environ['GITHUB_RUN_ATTEMPT'])}")
        pending['tags'][role] = tag
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'{role}_tag={tag}\n')
    Path('/tmp/dataquery-release-pending.json').write_text(json.dumps(pending))


def resolve_images():
    pending = json.loads(Path('/tmp/dataquery-release-pending.json').read_text())
    images = {}
    for role in ('backend', 'frontend'):
        images[role] = reference(pending['tags'][role])
        if not images[role]:
            raise ValueError('Candidate image missing')
        subprocess.run(['docker', 'pull', images[role]], check=True)
        with open(os.environ['GITHUB_ENV'], 'a') as output:
            output.write(f'{role.upper()}_IMAGE={images[role]}\n')
    pending = json.loads(Path('/tmp/dataquery-release-pending.json').read_text())
    pending['images'] = images
    Path('/tmp/dataquery-release-pending.json').write_text(json.dumps(pending))


def finalize():
    pending = json.loads(Path('/tmp/dataquery-release-pending.json').read_text())
    proof = json.loads(Path('artifacts/cd-smoke.json').read_text())
    if (not all(proof.get(key) for key in ('frontend_assets', 'api_health', 'history_trace_equal', 'proxy_boundaries',
                                           'token_absent_from_browser_assets'))
            or proof.get('external_model_calls') != 0 or proof.get('metric_service_order_count') != '2312'
            or not proof.get('catalog_citations') or proof.get('source_commit') != pending['source_commit']
            or proof.get('images') != pending['images']):
        raise ValueError('Deployment smoke did not pass')
    if api('/commits/main')['sha'] != pending['source_commit']:
        raise ValueError('Main advanced; stale release cannot become stable')
    pending.update(validated=True, workflow_run_id=int(os.environ['GITHUB_RUN_ID']),
                   workflow_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']))
    # Retain one prior stable pair, without growing a chain of historical manifests.
    if pending['previous_stable']:
        pending['previous_stable'].pop('previous_stable', None)
    Path('artifacts/release.json').write_text(json.dumps(manifest(pending), indent=2) + '\n')


def rollback(path, project):
    release = manifest(json.loads(path.read_text()))
    older = release.get('previous_stable')
    if not older:
        raise ValueError('First release has no previous stable version')
    older = manifest(older)
    for image in older['images'].values():
        subprocess.run(['docker', 'pull', image], check=True)
    subprocess.run(['bash', 'scripts/cd/validate.sh'], check=True, env=os.environ | {
        'BACKEND_IMAGE': older['images']['backend'], 'FRONTEND_IMAGE': older['images']['frontend'],
        'RELEASE_COMMIT': older['source_commit'], 'COMPOSE_PROJECT_NAME': project})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('gate', 'prepare', 'resolve', 'finalize', 'rollback'))
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--project', default='dataquery-cd-rollback-demo')
    args = parser.parse_args()
    if args.command == 'rollback' and args.manifest is None:
        parser.error('--manifest is required for rollback')
    {'gate': gate, 'prepare': prepare, 'resolve': resolve_images, 'finalize': finalize,
     'rollback': lambda: rollback(args.manifest, args.project)}[args.command]()
