FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY orchestrator/ orchestrator/
COPY config/ config/

EXPOSE 8080

CMD ["uvicorn", "orchestrator.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
