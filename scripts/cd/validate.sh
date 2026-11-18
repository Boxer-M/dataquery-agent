#!/usr/bin/env bash
set -euo pipefail
: "${BACKEND_IMAGE:?}" "${FRONTEND_IMAGE:?}" "${RELEASE_COMMIT:?}"
: "${COMPOSE_PROJECT_NAME:?Use a new dataquery-cd-* project}"
[[ "$COMPOSE_PROJECT_NAME" =~ ^dataquery-cd-[a-z0-9_-]+$ ]] || exit 2
export POSTGRES_USER=dataquery_ci_admin POSTGRES_PASSWORD=cd-only-not-production POSTGRES_DB=olist
export COMPOSE_FILE=compose.yaml:compose.rag.yaml:compose.redis.yaml:compose.ci.yaml:compose.cd.yaml
if docker volume inspect "${COMPOSE_PROJECT_NAME}_postgres_data" >/dev/null 2>&1; then
  echo 'Existing test volume refused' >&2
  exit 2
fi
mkdir -p artifacts
rm -f artifacts/cd-smoke.json
chmod 777 artifacts
for image in "$BACKEND_IMAGE" "$FRONTEND_IMAGE"; do
  [[ "$(docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" == "$RELEASE_COMMIT" ]]
done
trap 'docker compose down --volumes --remove-orphans' EXIT
docker compose up -d --wait db redis
docker compose run --rm init
docker compose up -d --wait backend frontend
docker compose exec -T frontend node --input-type=module - <<'JS'
import { readFileSync, readdirSync, statSync } from 'node:fs'
const token = /^DATAQUERY_HTTP_TOKEN=([a-fA-F0-9]{64})/m.exec(readFileSync('/run/frontend/.env.api', 'utf8'))?.[1]
if (!token) throw new Error('Frontend token mount unavailable')
function scan(directory) {
  for (const name of readdirSync(directory)) {
    const file = directory + '/' + name
    if (statSync(file).isDirectory()) scan(file)
    else if (readFileSync(file).includes(Buffer.from(token))) throw new Error('Token in static artifact')
  }
}
scan('/work/dist')
console.log('All production assets verified without runtime token')
JS
docker compose run --rm --no-deps check
