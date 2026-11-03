import re
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from loader.config import Settings
from tests.conftest import DEFAULT_ADMIN_DSN

ROOT = Path(__file__).resolve().parents[2]


def published_postgres_host() -> str:
    """The host-side address of the compose Postgres: the only interface its port is published on."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    return re.search(r'"([0-9.]+):5432:5432"', compose).group(1)


def test_host_side_clients_default_to_the_published_postgres_address():
    host = published_postgres_host()
    profiles = (ROOT / "dbt" / "credit_risk" / "profiles.yml").read_text(encoding="utf-8")

    assert Settings.model_fields["postgres_host"].default == host
    assert conninfo_to_dict(DEFAULT_ADMIN_DSN)["host"] == host
    assert re.search(r"env_var\('POSTGRES_HOST', '([^']+)'\)", profiles).group(1) == host
