# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Scenario-based unit tests for the K6 helper module (src/k6.py)."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from ops import testing

# The service_mesh charm library requires httpx/lightkube/pydantic at import
# time, which are not available in the unit-test venv. Stub the module so that
# ``charm.py`` can be imported without those heavy dependencies.
_svc_mesh = MagicMock()
_svc_mesh.ServiceMeshConsumer = MagicMock
_svc_mesh.UnitPolicy = MagicMock
sys.modules.setdefault("charms.istio_beacon_k8s", MagicMock())
sys.modules.setdefault("charms.istio_beacon_k8s.v0", MagicMock())
sys.modules.setdefault("charms.istio_beacon_k8s.v0.service_mesh", _svc_mesh)

from charm import K6K8sCharm  # noqa: E402

CHARM_ROOT = "/home/aegis/Repositories/Canonical/k6-k8s-operator"
K6_VERSION_EXEC = testing.Exec(
    command_prefix=["k6", "--version"],
    stdout="k6 v0.57.0 (go1.22.12, linux/amd64)",
)


def _peer(
    *,
    local_app_data=None,
    local_unit_data=None,
    peers_data=None,
):
    """Build a PeerRelation for the 'k6' endpoint with sensible defaults."""
    return testing.PeerRelation(
        endpoint="k6",
        local_app_data=local_app_data or {},
        local_unit_data=local_unit_data or {},
        peers_data=peers_data or {},
    )


def _container(*, execs=None, layers=None):
    """Build a connectable k6 Container with the version exec mock."""
    return testing.Container(
        "k6",
        can_connect=True,
        execs=execs or {K6_VERSION_EXEC},
        layers=layers or {},
    )


def _ctx():
    return testing.Context(K6K8sCharm, charm_root=CHARM_ROOT)


def _unit_data(status="idle", endpoint="zeus:6565"):
    return {"k6": json.dumps({"endpoint": endpoint, "status": status})}


def _app_data(script_path="/etc/k6/scripts/test.js", status="idle", labels=None):
    data = {
        "script_path": script_path,
        "labels": labels or {"test_uuid": "abc-123", "date": "2025-01-01"},
        "status": status,
    }
    return {"k6": json.dumps(data)}


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
class TestInitialization:
    def test_peer_data_set_to_idle_on_init(self):
        """When the charm starts with a peer relation, the unit databag gets idle status."""
        ctx = _ctx()
        peer = _peer()
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        out = ctx.run(ctx.on.start(), state)
        rel = out.get_relations("k6")[0]
        unit_data = json.loads(rel.local_unit_data["k6"])
        assert unit_data["status"] == "idle"
        assert "endpoint" in unit_data

    def test_existing_unit_data_preserved(self):
        """If the unit already has peer data, _initialize does not overwrite it."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data(status="busy"))
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        out = ctx.run(ctx.on.start(), state)
        rel = out.get_relations("k6")[0]
        unit_data = json.loads(rel.local_unit_data["k6"])
        assert unit_data["status"] == "busy"


# ---------------------------------------------------------------------------
# relation-changed
# ---------------------------------------------------------------------------
class TestRelationChanged:
    def test_idle_app_data_starts_service_and_sets_busy(self):
        """When app data has script_path + idle status, the layer is added and unit becomes busy."""
        ctx = _ctx()
        peer = _peer(
            local_app_data=_app_data(status="idle"),
            local_unit_data=_unit_data(status="idle"),
            peers_data={1: _unit_data(status="idle")},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        with patch("k6.K6Api.resume"):
            out = ctx.run(ctx.on.relation_changed(peer, remote_unit=1), state)

        rel = out.get_relations("k6")[0]
        unit_data = json.loads(rel.local_unit_data["k6"])
        assert unit_data["status"] == "busy"

        # Verify the pebble layer was added with the k6 run command
        container = out.get_container("k6")
        assert "k6" in container.layers
        svc = container.layers["k6"].services["k6"]
        assert "k6 run /etc/k6/scripts/test.js" in svc.command
        assert "pebble notify k6.com/done" in svc.command

    def test_no_app_data_stops_service_and_sets_idle(self):
        """When app data is empty, the unit is set back to idle."""
        from ops.pebble import Layer

        ctx = _ctx()
        base_layer = Layer(
            {
                "services": {
                    "k6": {"override": "replace", "command": "/bin/true", "startup": "disabled"}
                }
            }
        )
        peer = _peer(
            local_app_data={},
            local_unit_data=_unit_data(status="busy"),
            peers_data={1: _unit_data(status="idle")},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container(layers={"k6": base_layer})],
        )
        out = ctx.run(ctx.on.relation_changed(peer, remote_unit=1), state)
        rel = out.get_relations("k6")[0]
        unit_data = json.loads(rel.local_unit_data["k6"])
        assert unit_data["status"] == "idle"

    def test_leader_clears_app_data_when_all_idle_and_app_busy(self):
        """When leader finds all units idle but app status is busy, it clears app data."""
        ctx = _ctx()
        peer = _peer(
            local_app_data=_app_data(status="busy"),
            local_unit_data=_unit_data(status="idle"),
            peers_data={1: _unit_data(status="idle")},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        out = ctx.run(ctx.on.relation_changed(peer, remote_unit=1), state)
        rel = out.get_relations("k6")[0]
        assert rel.local_app_data.get("k6") is None


# ---------------------------------------------------------------------------
# Pebble layer content
# ---------------------------------------------------------------------------
class TestPebbleLayer:
    def test_layer_contains_script_path(self):
        """The pebble layer command references the script from peer app data."""
        ctx = _ctx()
        peer = _peer(
            local_app_data=_app_data(script_path="/etc/k6/scripts/custom.js"),
            local_unit_data=_unit_data(),
            peers_data={1: _unit_data()},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        with patch("k6.K6Api.resume"):
            out = ctx.run(ctx.on.relation_changed(peer, remote_unit=1), state)
        container = out.get_container("k6")
        cmd = container.layers["k6"].services["k6"].command
        assert "k6 run /etc/k6/scripts/custom.js" in cmd

    def test_layer_contains_prometheus_rw_flag(self):
        """The pebble layer always includes the prometheus remote-write output flag."""
        ctx = _ctx()
        peer = _peer(
            local_app_data=_app_data(),
            local_unit_data=_unit_data(),
            peers_data={1: _unit_data()},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        with patch("k6.K6Api.resume"):
            out = ctx.run(ctx.on.relation_changed(peer, remote_unit=1), state)
        container = out.get_container("k6")
        cmd = container.layers["k6"].services["k6"].command
        assert "-o experimental-prometheus-rw" in cmd

    def test_layer_includes_tag_labels(self):
        """The pebble layer includes --tag arguments derived from peer data labels."""
        ctx = _ctx()
        peer = _peer(
            local_app_data=_app_data(
                labels={"test_uuid": "uuid-1", "date": "2025-06-01"},
            ),
            local_unit_data=_unit_data(),
            peers_data={1: _unit_data()},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        with patch("k6.K6Api.resume"):
            out = ctx.run(ctx.on.relation_changed(peer, remote_unit=1), state)
        container = out.get_container("k6")
        cmd = container.layers["k6"].services["k6"].command
        assert "--tag test_uuid=uuid-1" in cmd
        assert "--tag date=2025-06-01" in cmd

    def test_layer_includes_environment_variables(self):
        """When the 'environment' config is set, -e flags appear in the layer command."""
        ctx = _ctx()
        peer = _peer(
            local_app_data=_app_data(),
            local_unit_data=_unit_data(),
            peers_data={1: _unit_data()},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
            config={"environment": "BASE_URL=http://example.com,TIMEOUT=30"},
        )
        with patch("k6.K6Api.resume"):
            out = ctx.run(ctx.on.relation_changed(peer, remote_unit=1), state)
        container = out.get_container("k6")
        cmd = container.layers["k6"].services["k6"].command
        assert "-e BASE_URL=http://example.com" in cmd
        assert "-e TIMEOUT=30" in cmd


# ---------------------------------------------------------------------------
# Pebble custom notice (k6.com/done)
# ---------------------------------------------------------------------------
class TestPebbleNotice:
    def test_done_notice_sets_unit_idle(self):
        """When the k6.com/done notice fires, the unit status reverts to idle."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data(status="busy"))
        notice = testing.Notice(key="k6.com/done")
        container = _container()
        # Reconstruct with the notice attached
        container = testing.Container(
            "k6", can_connect=True, execs={K6_VERSION_EXEC}, notices=[notice]
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[container],
        )
        out = ctx.run(ctx.on.pebble_custom_notice(container, notice), state)
        rel = out.get_relations("k6")[0]
        unit_data = json.loads(rel.local_unit_data["k6"])
        assert unit_data["status"] == "idle"


# ---------------------------------------------------------------------------
# collect-unit-status / collect-app-status
# ---------------------------------------------------------------------------
class TestStatusCollection:
    def test_unit_status_idle(self):
        """An idle unit reports plain ActiveStatus."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data(status="idle"))
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        out = ctx.run(ctx.on.collect_unit_status(), state)
        assert out.unit_status == testing.ActiveStatus("")

    def test_unit_status_busy(self):
        """A busy unit reports ActiveStatus with 'k6 status: busy'."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data(status="busy"))
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        out = ctx.run(ctx.on.collect_unit_status(), state)
        assert out.unit_status == testing.ActiveStatus("k6 status: busy")

    def test_app_status_idle(self):
        """When all units are idle, app status reports idle."""
        ctx = _ctx()
        peer = _peer(
            local_unit_data=_unit_data(status="idle"),
            peers_data={1: _unit_data(status="idle")},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
            planned_units=2,
        )
        out = ctx.run(ctx.on.collect_unit_status(), state)
        assert out.app_status == testing.ActiveStatus("k6 status: idle")

    def test_app_status_busy(self):
        """When some units are busy, app status reports busy count."""
        ctx = _ctx()
        peer = _peer(
            local_unit_data=_unit_data(status="busy"),
            peers_data={1: _unit_data(status="busy")},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
            planned_units=2,
        )
        out = ctx.run(ctx.on.collect_unit_status(), state)
        assert out.app_status == testing.ActiveStatus("k6 status: busy (2/2 units)")

    def test_app_status_partial_busy(self):
        """When only some units are busy, app status shows the correct count."""
        ctx = _ctx()
        peer = _peer(
            local_unit_data=_unit_data(status="idle"),
            peers_data={1: _unit_data(status="busy")},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
            planned_units=2,
        )
        out = ctx.run(ctx.on.collect_unit_status(), state)
        assert out.app_status == testing.ActiveStatus("k6 status: busy (1/2 units)")


# ---------------------------------------------------------------------------
# run() method (via start action)
# ---------------------------------------------------------------------------
class TestRunAction:
    def test_start_action_sets_app_peer_data(self):
        """The 'start' action writes script_path, labels, and idle status to app databag."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data())
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
            config={"load-test": "export default function() {}"},
        )
        out = ctx.run(ctx.on.action("start"), state)
        rel = out.get_relations("k6")[0]
        app_data = json.loads(rel.local_app_data["k6"])
        assert app_data["script_path"] == "/etc/k6/scripts/juju-config-script.js"
        assert app_data["status"] == "idle"
        assert "test_uuid" in app_data["labels"]
        assert "date" in app_data["labels"]

    def test_start_action_fails_on_non_leader(self):
        """The 'start' action fails when run on a non-leader unit."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data())
        state = testing.State(
            leader=False,
            relations=[peer],
            containers=[_container()],
            config={"load-test": "export default function() {}"},
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("start"), state)
        assert "leader" in exc_info.value.message.lower()

    def test_start_action_fails_when_already_running(self):
        """The 'start' action fails if k6 is already busy on any unit."""
        ctx = _ctx()
        peer = _peer(
            local_unit_data=_unit_data(status="busy"),
            peers_data={1: _unit_data(status="busy")},
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
            config={"load-test": "export default function() {}"},
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("start"), state)
        assert "already running" in exc_info.value.message.lower()

    def test_start_action_fails_without_script(self):
        """The 'start' action fails when no load-test script is configured."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data())
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("start"), state)
        assert "no script found" in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# stop() method (via stop action)
# ---------------------------------------------------------------------------
class TestStopAction:
    def test_stop_action_clears_app_data(self):
        """The 'stop' action clears the app peer databag."""
        ctx = _ctx()
        peer = _peer(
            local_app_data=_app_data(status="busy"),
            local_unit_data=_unit_data(status="busy"),
        )
        state = testing.State(
            leader=True,
            relations=[peer],
            containers=[_container()],
        )
        out = ctx.run(ctx.on.action("stop"), state)
        rel = out.get_relations("k6")[0]
        assert rel.local_app_data.get("k6") is None

    def test_stop_action_fails_on_non_leader(self):
        """The 'stop' action fails when run on a non-leader unit."""
        ctx = _ctx()
        peer = _peer(local_unit_data=_unit_data())
        state = testing.State(
            leader=False,
            relations=[peer],
            containers=[_container()],
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("stop"), state)
        assert "leader" in exc_info.value.message.lower()
