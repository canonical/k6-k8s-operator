# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Scenario-based unit tests for K6K8sCharm."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# The service-mesh charm library imports modules not available in the unit-test
# environment.  Mock them before any charm code is imported.
_svc_mesh = MagicMock()
_svc_mesh.ServiceMeshConsumer = MagicMock
_svc_mesh.UnitPolicy = MagicMock
sys.modules.setdefault("charms.istio_beacon_k8s", MagicMock())
sys.modules.setdefault("charms.istio_beacon_k8s.v0", MagicMock())
sys.modules.setdefault("charms.istio_beacon_k8s.v0.service_mesh", _svc_mesh)

import pytest  # noqa: E402
from ops import testing  # noqa: E402

from charm import K6K8sCharm  # noqa: E402

CHARM_ROOT = Path(__file__).parent.parent.parent

# charmcraft.yaml uses 'properties' for action parameters (JSON Schema style),
# but Scenario's consistency checker expects the Juju 'params' key.  When we
# pass ``actions`` to Context it ignores the charm_root metadata, so we also
# need to provide ``meta`` and ``config`` explicitly.
import yaml as _yaml  # noqa: E402

_CHARMCRAFT = _yaml.safe_load((CHARM_ROOT / "charmcraft.yaml").read_text())

_META = {
    "name": _CHARMCRAFT["name"],
    "containers": _CHARMCRAFT.get("containers", {}),
    "resources": _CHARMCRAFT.get("resources", {}),
    "peers": _CHARMCRAFT.get("peers", {}),
    "requires": _CHARMCRAFT.get("requires", {}),
    "provides": _CHARMCRAFT.get("provides", {}),
}

_ACTIONS = {
    "start": {
        "params": {
            "app": {"type": "string"},
            "test": {"type": "string"},
        },
    },
    "stop": {},
    "list": {},
}

_CONFIG = _CHARMCRAFT.get("config", {})

K6_VERSION_EXEC = testing.Exec(
    command_prefix=["k6", "--version"],
    stdout="k6 v0.57.0 (go1.22.12, linux/amd64)",
)


def _base_container(*, execs=None):
    """Return a connected k6 container with the version exec mock."""
    exec_set = {K6_VERSION_EXEC}
    if execs:
        exec_set.update(execs)
    return testing.Container("k6", can_connect=True, execs=exec_set)


def _base_state(
    *,
    config: dict[str, str | int | float | bool] | None = None,
    leader=True,
    peer=None,
    relations=None,
    container=None,
):
    """Build a minimal viable State for the charm."""
    if config is None:
        config = {"load-test": "// default script"}
    if container is None:
        container = _base_container()
    if peer is None:
        peer = testing.PeerRelation(endpoint="k6")
    rels = [peer]
    if relations:
        rels.extend(relations)
    return testing.State(
        containers={container},
        relations=rels,
        config=config,
        leader=leader,
    )


@pytest.fixture
def ctx():
    return testing.Context(
        K6K8sCharm,
        charm_root=CHARM_ROOT,
        meta=_META,
        actions=_ACTIONS,
        config=_CONFIG,
    )


class TestContainerNotConnected:
    def test_no_crash_when_disconnected(self, ctx):
        """Charm early-returns without error when container cannot connect."""
        container = testing.Container("k6", can_connect=False)
        state = testing.State(containers={container}, leader=True)
        ctx.run(ctx.on.update_status(), state)


class TestReconcile:
    def test_sets_active_status(self, ctx):
        state_out = ctx.run(ctx.on.update_status(), _base_state())
        assert state_out.unit_status == testing.ActiveStatus()

    def test_sets_workload_version(self, ctx):
        state_out = ctx.run(ctx.on.update_status(), _base_state())
        assert state_out.workload_version == "0.57.0"

    def test_unrecognised_version_string(self, ctx):
        bad_exec = testing.Exec(
            ["k6", "--version"], stdout="unknown version output"
        )
        container = testing.Container("k6", can_connect=True, execs={bad_exec})
        state_out = ctx.run(ctx.on.update_status(), _base_state(container=container))
        assert state_out.workload_version == ""

    def test_config_script_pushed_to_container(self, ctx):
        script = 'import http from "k6/http"; export default function() {}'
        state_out = ctx.run(
            ctx.on.update_status(), _base_state(config={"load-test": script})
        )
        container_out = state_out.get_container("k6")
        fs = container_out.get_filesystem(ctx)
        pushed = fs / "etc" / "k6" / "scripts" / "juju-config-script.js"
        assert pushed.read_text() == script

    def test_relation_tests_pushed_to_container(self, ctx):
        test_content = "// load test from relation"
        k6_data = json.dumps({"tests": {"test1.js": test_content}})
        rel = testing.Relation(
            endpoint="receive-k6-tests",
            remote_app_data={"k6": k6_data},
        )
        state_out = ctx.run(ctx.on.update_status(), _base_state(relations=[rel]))
        container_out = state_out.get_container("k6")
        fs = container_out.get_filesystem(ctx)
        matches = list((fs / "etc" / "k6" / "scripts").rglob("test1.js"))
        assert len(matches) == 1
        assert matches[0].read_text() == test_content


class TestStartAction:
    def test_non_leader_fails(self, ctx):
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("start"), _base_state(leader=False))
        assert "leader" in exc_info.value.message.lower()

    def test_already_running_fails(self, ctx):
        peer = testing.PeerRelation(
            endpoint="k6",
            local_unit_data={
                "k6": json.dumps({"status": "busy", "endpoint": "host:6565"})
            },
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("start"), _base_state(peer=peer))
        assert "already running" in exc_info.value.message.lower()

    def test_no_script_at_default_path(self, ctx):
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("start"), _base_state(config={}))
        assert "no script found" in exc_info.value.message.lower()

    def test_named_test_not_found(self, ctx):
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(
                ctx.on.action("start", params={"app": "myapp", "test": "missing.js"}),
                _base_state(),
            )
        assert "no script found" in exc_info.value.message.lower()

    def test_succeeds_with_config_script(self, ctx):
        ctx.run(ctx.on.action("start"), _base_state(config={"load-test": "// script"}))


class TestStopAction:
    def test_non_leader_fails(self, ctx):
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("stop"), _base_state(leader=False))
        assert "leader" in exc_info.value.message.lower()

    def test_leader_stops(self, ctx):
        ctx.run(ctx.on.action("stop"), _base_state())


class TestListAction:
    def test_non_leader_fails(self, ctx):
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("list"), _base_state(leader=False))
        assert "leader" in exc_info.value.message.lower()

    def test_leader_lists(self, ctx):
        ctx.run(ctx.on.action("list"), _base_state())
