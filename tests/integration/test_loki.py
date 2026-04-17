# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for k6-k8s with Loki log shipping."""

import json
import time

import jubilant
import pytest

from .conftest import APP_NAME, K6_IMAGE, LIGHTWEIGHT_K6_SCRIPT

LOKI_APP = "loki-k8s"


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


def _assert_loki_has_k6_logs(juju: jubilant.Juju, retries=30, delay=10):
    """Query Loki for k6 log entries, retrying until found."""
    now = int(time.time())
    start = now - 600  # look back 10 minutes
    end = now + 60
    query = '{' + f'juju_application="{APP_NAME}"' + '}'
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
    raise AssertionError("No k6 logs found in Loki")


# ---------------------------------------------------------------------------
# Single k6 unit + Loki
# ---------------------------------------------------------------------------


@pytest.mark.juju_setup
@pytest.mark.abort_on_fail
def test_deploy_loki(juju: jubilant.Juju, charm_path):
    """Deploy k6 and loki, integrate them."""
    juju.deploy(charm_path, APP_NAME, resources={"k6-image": K6_IMAGE})
    juju.deploy(LOKI_APP, channel="latest/stable", trust=True)
    juju.wait(
        lambda s: jubilant.all_active(s, APP_NAME, LOKI_APP),
        timeout=600,
    )
    juju.integrate(f"{APP_NAME}:logging", LOKI_APP)
    juju.wait(lambda s: jubilant.all_active(s), timeout=300)


def test_run_single_unit(juju: jubilant.Juju):
    """Configure and run a lightweight test, then verify Loki has k6 logs."""
    juju.config(APP_NAME, values={"load-test": LIGHTWEIGHT_K6_SCRIPT})
    juju.wait(lambda s: jubilant.all_active(s), timeout=120)

    task = juju.run(unit=f"{APP_NAME}/leader", action="start")
    task.raise_on_failure()

    _wait_for_idle(juju)
    _assert_loki_has_k6_logs(juju)


# ---------------------------------------------------------------------------
# Multiple k6 units + Loki
# ---------------------------------------------------------------------------


def test_scale_up_and_run_multi_unit(juju: jubilant.Juju):
    """Scale k6 to 3 units, run test, verify Loki logs."""
    juju.cli("scale-application", APP_NAME, "3")
    juju.wait(lambda s: jubilant.all_active(s, APP_NAME), timeout=600)

    task = juju.run(unit=f"{APP_NAME}/leader", action="start")
    task.raise_on_failure()

    _wait_for_idle(juju)
    _assert_loki_has_k6_logs(juju)
