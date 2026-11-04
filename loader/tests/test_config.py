import re
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from loader.config import Settings
from tests.conftest import ADMIN_HOST, admin_dsn, connection_skip_reason

ROOT = Path(__file__).resolve().parents[2]


def published_postgres_host() -> str:
    """The host-side address of the compose Postgres: the only interface its port is published on."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    return re.search(r'"([0-9.]+):5432:5432"', compose).group(1)


def test_host_side_clients_default_to_the_published_postgres_address():
    host = published_postgres_host()
    profiles = (ROOT / "dbt" / "credit_risk" / "profiles.yml").read_text(encoding="utf-8")

    assert Settings.model_fields["postgres_host"].default == host
    assert host == ADMIN_HOST
    assert re.search(r"env_var\('POSTGRES_HOST', '([^']+)'\)", profiles).group(1) == host


ADMIN_VARIABLES = ("LOADER_TEST_PG_DSN", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")


@pytest.fixture
def clean_environment(monkeypatch):
    for name in ADMIN_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def env_values(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def admin_credentials(dsn: str) -> tuple:
    values = conninfo_to_dict(dsn)
    return values["host"], values["port"], values["dbname"], values["user"], values["password"]


def test_admin_dsn_uses_the_credentials_of_the_root_env_file(clean_environment, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_DB=dw\nPOSTGRES_USER=admin\nPOSTGRES_PASSWORD=a changed secret\n")

    assert admin_credentials(admin_dsn(env_file)) == (ADMIN_HOST, "5432", "dw", "admin", "a changed secret")


def test_environment_variables_override_the_env_file(clean_environment, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_USER=admin\nPOSTGRES_PASSWORD=from_file\n")
    clean_environment.setenv("POSTGRES_PASSWORD", "from_environment")

    assert conninfo_to_dict(admin_dsn(env_file))["password"] == "from_environment"


def test_explicit_test_dsn_takes_priority(clean_environment, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=from_file\n")
    clean_environment.setenv("LOADER_TEST_PG_DSN", "host=db port=6543 dbname=t user=u password=p")

    assert admin_dsn(env_file) == "host=db port=6543 dbname=t user=u password=p"


def test_without_env_file_the_template_credentials_are_used(clean_environment, tmp_path):
    template = env_values(ROOT / ".env.example")

    assert admin_credentials(admin_dsn(tmp_path / "missing.env")) == (
        ADMIN_HOST,
        "5432",
        template["POSTGRES_DB"],
        template["POSTGRES_USER"],
        template["POSTGRES_PASSWORD"],
    )


# Messages as libpq reports them (psycopg leaves sqlstate empty for connection errors).
SERVER = 'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
REFUSED = 'FATAL:  password authentication failed for user "dw_admin"'
REFUSED_PASSWORD = psycopg.OperationalError(SERVER + REFUSED)
SERVER_DOWN = psycopg.OperationalError(SERVER + "Connection refused (0x0000274D/10061)")


def test_skip_reason_tells_a_refused_password_from_a_server_that_is_down():
    refused, down = connection_skip_reason(REFUSED_PASSWORD), connection_skip_reason(SERVER_DOWN)

    assert "refused the credentials" in refused
    assert "POSTGRES_PASSWORD" in refused
    assert "not reachable" not in refused
    assert "not reachable" in down
    assert "docker compose up -d --wait" in down
