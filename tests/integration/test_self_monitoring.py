# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for k6-k8s self-monitoring (Loki logs + Prometheus metrics).

The test exercises the charm's ``environment`` config option to template
the Loki and Prometheus endpoints into the k6 script at runtime.
"""

import json
import time

import jubilant
import pytest

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


def _assert_loki_has_logs(juju: jubilant.Juju, retries=30, delay=10):
    """Query Loki for any log entries pushed by xk6-loki, retrying until found."""
    now = int(time.time())
    start = now - 600  # look back 10 minutes
    end = now + 60
    # xk6-loki creates streams with built-in labels; query broadly
    query = '{instance=~".+"}'
    for attempt in range(retries):
        try:
            output = juju.ssh(
                f"{LOKI_APP}/0",
                "curl",
                "-sG",
                "http://localhost:3100/loki/api/v1/query_range",
                "--data-urlencode", f"query={query}",
                "-d", f"start={start}",
                "-d", f"end={end}",
                "-d", "limit=10",
                container="loki",
            )
            data = json.loads(output)
            results = data.get("data", {}).get("result", [])
            if results:
                return
        except Exception:
            pass
        time.sleep(delay)
    raise AssertionError("No logs found in Loki after xk6-loki push")


def _assert_prometheus_has_k6_metrics(juju: jubilant.Juju, retries=30, delay=10):
    """Query Prometheus for k6 metrics, retrying until found."""
    for _ in range(retries):
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
# Single k6 unit + Loki + Prometheus
# ---------------------------------------------------------------------------


@pytest.mark.juju_setup
@pytest.mark.abort_on_fail
def test_deploy(juju: jubilant.Juju, charm_path):
    """Deploy k6, Loki and Prometheus, then integrate them."""
    juju.deploy(charm_path, APP_NAME, resources={"k6-image": K6_IMAGE})
    juju.deploy("loki-k8s", LOKI_APP, channel="dev/edge", trust=True)
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
