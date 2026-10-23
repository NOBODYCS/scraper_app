# job-alerts worker — managed deployment Mistral.
# Platform requirement: mistralai-workflows >= 3.10 (pulls mistralai automatically).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir "mistralai-workflows[mistralai]>=3.10"

COPY worker.py job_alerts_workflow.py scrapers.py matcher.py email_builder.py ./
COPY data/cv ./data/cv

CMD ["python", "worker.py"]
