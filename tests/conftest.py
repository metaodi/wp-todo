from __future__ import annotations

from pathlib import Path

import pytest

from wp_todo.config import MetaConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def meta() -> MetaConfig:
    return MetaConfig(contact="https://github.com/metaodi/wp-todo", user_agent_product="wp-todo-tests")
