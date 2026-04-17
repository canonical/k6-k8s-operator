# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = str(METADATA["name"])
K6_IMAGE = str(METADATA["resources"]["k6-image"]["upstream-source"])
RESOURCES_DIR = Path(__file__).parent.parent / "resources"


def pytest_addoption(parser):
    parser.addoption("--charm_path", action="store", help="Path to the built charm file")
    # Alias for --no-juju-teardown; the shared observability CI passes --keep-models in debug mode.
    parser.addoption("--keep-models", action="store_true", default=False)


def pytest_configure(config):
    if config.getoption("--keep-models", default=False):
        config.option.no_juju_teardown = True


@pytest.fixture(scope="session")
def charm_path(request):
    path = request.config.getoption("--charm_path") or os.environ.get("CHARM_PATH")
    assert path, "Please provide --charm_path or set CHARM_PATH env var"
    return Path(path).resolve()
