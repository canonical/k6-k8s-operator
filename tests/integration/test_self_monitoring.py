# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for k6-k8s self-monitoring (Loki logs + Prometheus metrics).

The test exercises the charm's ``environment`` config option to template
the Loki and Prometheus endpoints into the k6 script at runtime.
"""

import time

import jubilant
import pytest
import requests
from tenacity import retry, retry_if_exception_type, retry_if_result, stop_after_attempt, wait_fixed

from .conftest import APP_NAME, K6_IMAGE, RESOURCES_DIR

LOKI_APP = "loki"
PROMETHEUS_APP = "prometheus"
LOAD_TEST_SCRIPT = (RESOURCES_DIR / "load_test.js").read_text()

# Kubernetes in-cluster DNS for headless services created by Juju
LOKI_URL = f"http://{LOKI_APP}-0.{LOKI_APP}-endpoints:3100"
PROMETHEUS_URL = f"http://{PROMETHEUS_APP}-0.{PROMETHEUS_APP}-endpoints:9090"
PROMETHEUS_RW_URL = f"{PROMETHEUS_URL}/api/v1/write"


def _wait_for_idle(juju: jubilant.Juju, timeout=300, poll_interval=10):
    """Poll juju status until the k6 app status message contains 'idle'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = juju.status()
        app_status = status.apps.get(APP_NAME)
        if app_status and "idle" in (app_status.app_status.message or "").lower():
            return
        time.sleep(poll_interval)
    raise TimeoutError(f"{APP_NAME} did not become idle within {timeout}s")


def _get_unit_address(juju: jubilant.Juju, app: str, unit_num: int = 0) -> str:
    """Get the address of a Juju unit from status."""
    status = juju.status()
    unit = status.apps[app].units[f"{app}/{unit_num}"]
    return unit.address


@retry(
    stop=stop_after_attempt(30),
    wait=wait_fixed(10),
    retry=retry_if_exception_type(Exception) | retry_if_result(lambda result: not result),
)
def _query_loki_for_logs(loki_url: str):
    """Query Loki for any log entries pushed by xk6-loki."""
    now = int(time.time())
    start = now - 600  # look back 10 minutes
    end = now + 60
    query = '{instance=~".+"}'
    response = requests.get(
        f"{loki_url}/loki/api/v1/query_range",
        params={"query": query, "start": start, "end": end, "limit": 10},
    )
    response.raise_for_status()
    data = response.json()
    return data.get("data", {}).get("result")


@retry(
    stop=stop_after_attempt(30),
    wait=wait_fixed(10),
    retry=retry_if_exception_type(Exception) | retry_if_result(lambda result: not result),
)
def _query_prometheus_for_k6_metrics(prometheus_url: str):
    """Query Prometheus for k6 metrics."""
    response = requests.get(
        f"{prometheus_url}/api/v1/query",
        params={"query": "k6_iterations_total"},
    )
    response.raise_for_status()
    data = response.json()
    return data.get("data", {}).get("result")


def _assert_loki_has_logs(juju: jubilant.Juju):
    """Query Loki for any log entries pushed by xk6-loki, retrying until found."""
    address = _get_unit_address(juju, LOKI_APP)
    _query_loki_for_logs(f"http://{address}:3100")


def _assert_prometheus_has_k6_metrics(juju: jubilant.Juju):
    """Query Prometheus for k6 metrics, retrying until found."""
    address = _get_unit_address(juju, PROMETHEUS_APP)
    _query_prometheus_for_k6_metrics(f"http://{address}:9090")


# ---------------------------------------------------------------------------
# Single k6 unit + Loki + Prometheus
# ---------------------------------------------------------------------------


@pytest.mark.juju_setup
@pytest.mark.abort_on_fail
def test_deploy(juju: jubilant.Juju, charm_path):
    """Deploy k6, Loki and Prometheus, then integrate them."""
    juju.deploy(charm_path, APP_NAME, resources={"k6-image": K6_IMAGE})
    juju.deploy("loki-k8s", LOKI_APP, revision=221, channel="dev/edge", trust=True)
    juju.deploy("prometheus-k8s", PROMETHEUS_APP, channel="dev/edge", trust=True)
    juju.wait(
        lambda s: jubilant.all_active(s, APP_NAME, LOKI_APP, PROMETHEUS_APP),
        timeout=600,
    )
    juju.integrate(f"{APP_NAME}:logging", LOKI_APP)
    juju.integrate(f"{APP_NAME}:send-remote-write", PROMETHEUS_APP)
    juju.wait(lambda s: jubilant.all_active(s), timeout=300)


def test_run_single_unit(juju: jubilant.Juju):
    """Configure endpoints via environment config, run load test, verify observability."""
    # Exercise the charm's environment config to template the endpoints
    env_config = (
        f"LOKI_URL={LOKI_URL}"
        f",K6_PROMETHEUS_RW_SERVER_URL={PROMETHEUS_RW_URL}"
        f",TARGET_URL={PROMETHEUS_URL}"
    )
    juju.config(APP_NAME, values={"load-test": LOAD_TEST_SCRIPT, "environment": env_config})
    juju.wait(lambda s: jubilant.all_active(s), timeout=120)

    task = juju.run(unit=f"{APP_NAME}/leader", action="start")
    task.raise_on_failure()

    _wait_for_idle(juju)
    _assert_loki_has_logs(juju)
    _assert_prometheus_has_k6_metrics(juju)


# ---------------------------------------------------------------------------
# Multiple k6 units + Loki + Prometheus
# ---------------------------------------------------------------------------


def test_scale_up_and_run_multi_unit(juju: jubilant.Juju):
    """Scale k6 to 3 units, run load test, verify Loki logs and Prometheus metrics."""
    juju.cli("scale-application", APP_NAME, "3")
    juju.wait(lambda s: jubilant.all_active(s, APP_NAME), timeout=600)

    task = juju.run(unit=f"{APP_NAME}/leader", action="start")
    task.raise_on_failure()

    _wait_for_idle(juju)
    _assert_loki_has_logs(juju)
    _assert_prometheus_has_k6_metrics(juju)
