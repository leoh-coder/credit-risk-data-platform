FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    ARTIFACTS_DIR=/artifacts

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# The artifacts volume is initialized from this directory, owned by the non-root user.
RUN useradd --create-home --uid 1000 app && mkdir /artifacts && chown app:app /artifacts
USER app

COPY src/ ./src/

ENTRYPOINT ["python", "-m", "ml.train"]
