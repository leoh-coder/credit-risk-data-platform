import inspect
import re
from pathlib import Path

from ingestion.config import Settings
from ingestion.generator import generate_batch

ROOT = Path(__file__).resolve().parents[2]


def test_batch_size_default_is_the_same_in_code_env_template_and_compose():
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    documented = {
        int(re.search(r"^CUSTOMERS_PER_BATCH=(\d+)$", template, re.M).group(1)),
        int(re.search(r"CUSTOMERS_PER_BATCH:-(\d+)", compose).group(1)),
    }
    in_code = {
        Settings.model_fields["customers_per_batch"].default,
        inspect.signature(generate_batch).parameters["customers_per_batch"].default,
    }
    assert in_code == documented
    assert len(documented) == 1


def test_open_finance_history_is_the_same_in_code_compose_and_dbt():
    # The dbt features turn the history into monthly amounts; a shorter history would understate them.
    compose = re.search(r"HISTORY_DAYS:-(\d+)", (ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose, "docker-compose.yml does not pass HISTORY_DAYS to the ingestion job"
    project = (ROOT / "dbt" / "credit_risk" / "dbt_project.yml").read_text(encoding="utf-8")
    dbt_var = re.search(r"^\s*open_finance_history_days:\s*(\d+)", project, re.M)
    values = {
        Settings.model_fields["history_days"].default,
        inspect.signature(generate_batch).parameters["history_days"].default,
        int(compose.group(1)),
        int(dbt_var.group(1)),
    }
    assert len(values) == 1, values
