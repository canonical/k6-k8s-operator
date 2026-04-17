# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for k6-k8s with Prometheus remote write."""

import json
import time

import jubilant
import pytest

from .conftest import APP_NAME, K6_IMAGE, LIGHTWEIGHT_K6_SCRIPT

PROMETHEUS_APP = "prometheus-k8s"


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


def _assert_prometheus_has_k6_metrics(juju: jubilant.Juju, retries=30, delay=10):
    """Query Prometheus for k6 metrics, retrying until found."""
    for attempt in range(retries):
        try:
            output = juju.ssh(
                f"{PROMETHEUS_APP}/0",
                "curl",
                "-s",
                "http://localhost:9090/api/v1/query?query=k6_iterations_total",
                container="prometheus",
            )
            data = json.loads(output)
            if data.get("data", {}).get("result"):
                return
        except Exception:
            pass
        time.sleep(delay)
    raise AssertionError("No k6 metrics found in Prometheus")


# ---------------------------------------------------------------------------
# Single k6 unit + Prometheus
# ---------------------------------------------------------------------------


@pytest.mark.juju_setup
@pytest.mark.abort_on_fail
def test_deploy_prometheus(juju: jubilant.Juju, charm_path):
    """Deploy k6 and prometheus, integrate them."""
    juju.deploy(charm_path, APP_NAME, resources={"k6-image": K6_IMAGE})
    juju.deploy(PROMETHEUS_APP, channel="latest/stable", trust=True)
    juju.wait(
        lambda s: jubilant.all_active(s, APP_NAME, PROMETHEUS_APP),
        timeout=600,
    )
    juju.integrate(f"{APP_NAME}:send-remote-write", PROMETHEUS_APP)
    juju.wait(lambda s: jubilant.all_active(s), timeout=300)


def test_run_single_unit(juju: jubilant.Juju):
    """Configure and run a lightweight test, then verify Prometheus has k6 metrics."""
    juju.config(APP_NAME, values={"load-test": LIGHTWEIGHT_K6_SCRIPT})
    juju.wait(lambda s: jubilant.all_active(s), timeout=120)

    task = juju.run(unit=f"{APP_NAME}/leader", action="start")
    task.raise_on_failure()

    _wait_for_idle(juju)
    _assert_prometheus_has_k6_metrics(juju)


# ---------------------------------------------------------------------------
# Multiple k6 units + Prometheus
# ---------------------------------------------------------------------------


def test_scale_up_and_run_multi_unit(juju: jubilant.Juju):
    """Scale k6 to 3 units, run test, verify Prometheus metrics."""
    juju.cli("scale-application", APP_NAME, "3")
    juju.wait(lambda s: jubilant.all_active(s, APP_NAME), timeout=600)

    task = juju.run(unit=f"{APP_NAME}/leader", action="start")
    task.raise_on_failure()

    _wait_for_idle(juju)
    _assert_prometheus_has_k6_metrics(juju)
