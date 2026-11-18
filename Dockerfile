FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app/tests:/app
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-pdf.txt -r requirements-langgraph.txt -r requirements-redis.txt && pip check
RUN useradd --create-home app && chown app:app /app
COPY --chown=app:app src src
COPY --chown=app:app scripts scripts
COPY --chown=app:app sql sql
COPY --chown=app:app warehouse warehouse
COPY --chown=app:app metrics metrics
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
