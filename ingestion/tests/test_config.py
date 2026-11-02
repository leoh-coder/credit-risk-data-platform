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
