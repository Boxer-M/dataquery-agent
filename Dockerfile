FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app/tests
WORKDIR /app
COPY requirements*.txt requirements-container.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 60 --retries 2 -r requirements-container.lock && pip check
ENV PYTHONPATH=/app/src:/app/tests:/app
RUN useradd --create-home app && mkdir -p /run/config /run/frontend && chown app:app /app /run/config /run/frontend
COPY --chown=app:app src src
COPY --chown=app:app scripts scripts
COPY --chown=app:app sql sql
COPY --chown=app:app warehouse warehouse
COPY --chown=app:app metrics metrics
COPY --chown=app:app skills skills
COPY --chown=app:app docs docs
COPY --chown=app:app reports reports
COPY --chown=app:app output/pdf/document-extraction-sample.pdf output/pdf/document-extraction-sample.pdf
COPY --chown=app:app tests tests
COPY --chown=app:app pytest.ini ./
USER app
CMD ["python", "-m", "dataquery_agent.api"]

FROM runtime AS ci
RUN pip install --no-cache-dir --user -r requirements-ci.txt && pip check
CMD ["python", "scripts/ci/run.py"]
