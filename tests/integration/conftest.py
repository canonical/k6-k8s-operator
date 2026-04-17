# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant
import pytest
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = str(METADATA["name"])
K6_IMAGE = str(METADATA["resources"]["k6-image"]["upstream-source"])

# Lightweight k6 script that generates minimal load.
# Uses a simple sleep-based approach so it works without any target HTTP server.
LIGHTWEIGHT_K6_SCRIPT = """\
import { sleep } from 'k6';

export const options = {
    vus: 1,
    duration: '10s',
};

export default function () {
    sleep(1);
}
"""


def pytest_addoption(parser):
    parser.addoption("--charm_path", action="store", help="Path to the built charm file")
    parser.addoption("--model", action="store", default=None, help="Juju model to use")
    parser.addoption("--keep-models", action="store_true", default=False)


@pytest.fixture(scope="module")
def juju(request):
    model = request.config.getoption("--model")
    if model:
        juju = jubilant.Juju(model=model, wait_timeout=10 * 60)
        yield juju
        if request.session.testsfailed:
            print(juju.debug_log(limit=1000), end="")
        return

    keep = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep) as juju:
        juju.wait_timeout = 10 * 60
        yield juju
        if request.session.testsfailed:
            print(juju.debug_log(limit=1000), end="")


@pytest.fixture(scope="session")
def charm_path(request):
    path = request.config.getoption("--charm_path")
    assert path, "Please provide --charm_path"
    return Path(path).resolve()
