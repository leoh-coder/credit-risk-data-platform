FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DBT_PROFILES_DIR=/app/credit_risk

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# dbt writes dbt_packages/, target/ and logs/ inside the project directory.
RUN useradd --create-home --uid 1000 app && install -d -o app -g app /app/credit_risk
WORKDIR /app/credit_risk
COPY --chown=app:app credit_risk/ ./
USER app

# Packages are pinned by package-lock.yml and baked in: runs need no access to the package hub.
RUN dbt deps

ENTRYPOINT ["dbt"]
CMD ["build"]
