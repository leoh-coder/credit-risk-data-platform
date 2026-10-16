FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DBT_PROFILES_DIR=/app/credit_risk

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# The project dir must belong to `app`: dbt writes dbt_packages/, target/ and logs/ next to dbt_project.yml.
RUN useradd --create-home --uid 1000 app && install -d -o app -g app /app/credit_risk
WORKDIR /app/credit_risk
COPY --chown=app:app credit_risk/ ./
USER app

# Bake dbt packages into the image (pinned by package-lock.yml): runs need no access to hub.getdbt.com.
RUN dbt deps

ENTRYPOINT ["dbt"]
CMD ["build"]
