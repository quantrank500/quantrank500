# One image serves both the API and the worker; compose picks the command.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[databento]"
COPY scripts ./scripts

# api:    python scripts/run_api.py       (QR500_API_HOST=0.0.0.0)
# worker: python scripts/run_worker.py
CMD ["python", "scripts/run_api.py"]
