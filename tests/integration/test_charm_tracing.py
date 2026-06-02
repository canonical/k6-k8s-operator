# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for charm tracing with Tempo.

This test validates that the k6 charm can integrate with Tempo for distributed
tracing of charm operations via the charm-tracing relation.
"""

import logging

import jubilant
import pytest
import requests
from tenacity import retry, stop_after_attempt, wait_fixed

from .conftest import APP_NAME, K6_IMAGE
from .helpers import get_unit_address

logger = logging.getLogger(__name__)

TEMPO_APP = "tempo"
TEMPO_WORKER_APP = "tempo-worker"
SEAWEEDFS_APP = "seaweedfs"


@retry(stop=stop_after_attempt(15), wait=wait_fixed(10))
def _assert_traces_in_tempo(tempo_ip: str, service_name: str):
    """Query Tempo for traces from a specific service, retrying until found."""
    url = f"http://{tempo_ip}:3200/api/search"
    params = {"tags": f"service.name={service_name}"}
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    traces = response.json().get("traces", [])
    assert len(traces) > 0, f"No traces found for service {service_name}"
    return traces


def _deploy_tempo_stack(juju: jubilant.Juju):
    """Deploy the Tempo distributed tracing stack with SeaweedFS storage."""
    # Deploy Tempo coordinator and worker
    juju.deploy("tempo-coordinator-k8s", TEMPO_APP, channel="dev/edge", trust=True)
    juju.deploy("tempo-worker-k8s", TEMPO_WORKER_APP, channel="dev/edge", trust=True)

    # Deploy SeaweedFS for S3-compatible storage
    juju.deploy("seaweedfs-k8s", SEAWEEDFS_APP, channel="edge", config={"bucket": "tempo"})

    # Integrate Tempo components
    juju.integrate(TEMPO_APP, TEMPO_WORKER_APP)
    juju.integrate(SEAWEEDFS_APP, TEMPO_APP)

    # Wait for Tempo stack to be active
    juju.wait(
        lambda s: jubilant.all_active(s, TEMPO_APP, TEMPO_WORKER_APP, SEAWEEDFS_APP),
        timeout=600,
    )


@pytest.mark.juju_setup
@pytest.mark.abort_on_fail
def test_deploy_with_tracing(juju: jubilant.Juju, charm_path):
    """Deploy k6 and Tempo, then integrate for charm tracing."""
    # Deploy k6 charm
    juju.deploy(charm_path, APP_NAME, resources={"k6-image": K6_IMAGE})
    juju.wait(lambda s: jubilant.all_active(s, APP_NAME), timeout=300)

    # Deploy Tempo stack
    _deploy_tempo_stack(juju)

    # Integrate k6 with Tempo for charm tracing
    juju.integrate(f"{APP_NAME}:charm-tracing", f"{TEMPO_APP}:tracing")
    juju.wait(
        lambda s: jubilant.all_active(s, APP_NAME, TEMPO_APP, TEMPO_WORKER_APP)
        and jubilant.all_agents_idle(s),
        timeout=300,
    )


def test_charm_tracing_produces_traces(juju: jubilant.Juju):
    """Verify that charm operations produce traces in Tempo."""
    # Trigger a charm event by running an action
    juju.config(APP_NAME, values={"load-test": "// test script"})
    juju.wait(lambda s: jubilant.all_active(s, APP_NAME), timeout=120)

    # Query Tempo for traces from the k6 charm
    tempo_ip = get_unit_address(juju, TEMPO_APP)
    traces = _assert_traces_in_tempo(tempo_ip, service_name=APP_NAME)
    logger.info("Found %d traces for %s", len(traces), APP_NAME)
