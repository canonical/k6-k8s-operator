# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Unit tests for charm tracing integration."""

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
import yaml as _yaml  # noqa: E402
from ops import testing  # noqa: E402

from charm import K6K8sCharm  # noqa: E402

CHARM_ROOT = Path(__file__).parent.parent.parent
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

# Sample tracing relation data (OTLP HTTP endpoint)
_TRACING_HTTP_APP_DATA = json.dumps(
    [
        {
            "protocol": {"name": "otlp_http", "type": "http"},
            "url": "http://tempo-k8s-0.tempo-k8s-endpoints:4318/v1/traces",
        }
    ]
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


class TestCharmTracingRelation:
    """Tests for charm-tracing relation handling."""

    def test_charm_starts_without_tracing_relation(self, ctx):
        """Charm should start and become active without tracing relation."""
        state_out = ctx.run(ctx.on.update_status(), _base_state())
        assert state_out.unit_status == testing.ActiveStatus()
        assert state_out.workload_version == "0.57.0"

    def test_charm_starts_with_tracing_relation(self, ctx):
        """Charm should start and become active with tracing relation."""
        tracing_rel = testing.Relation(
            endpoint="charm-tracing",
            remote_app_data={"receivers": _TRACING_HTTP_APP_DATA},
        )
        state_out = ctx.run(
            ctx.on.update_status(), _base_state(relations=[tracing_rel])
        )
        assert state_out.unit_status == testing.ActiveStatus()
        assert state_out.workload_version == "0.57.0"
        # Verify tracing relation is still present
        tracing_relations = [r for r in state_out.relations if r.endpoint == "charm-tracing"]
        assert len(tracing_relations) == 1

    def test_tracing_relation_joined(self, ctx):
        """Charm handles tracing relation-joined event."""
        tracing_rel = testing.Relation(
            endpoint="charm-tracing",
            remote_app_data={"receivers": _TRACING_HTTP_APP_DATA},
        )
        state_out = ctx.run(
            ctx.on.relation_joined(tracing_rel),
            _base_state(relations=[tracing_rel]),
        )
        assert state_out.unit_status == testing.ActiveStatus()
        assert state_out.workload_version == "0.57.0"

    def test_tracing_relation_changed(self, ctx):
        """Charm handles tracing relation-changed event."""
        tracing_rel = testing.Relation(
            endpoint="charm-tracing",
            remote_app_data={"receivers": _TRACING_HTTP_APP_DATA},
        )
        state_out = ctx.run(
            ctx.on.relation_changed(tracing_rel),
            _base_state(relations=[tracing_rel]),
        )
        assert state_out.unit_status == testing.ActiveStatus()
        assert state_out.workload_version == "0.57.0"

    def test_tracing_relation_broken(self, ctx):
        """Charm handles tracing relation-broken event gracefully."""
        tracing_rel = testing.Relation(
            endpoint="charm-tracing",
            remote_app_data={},
        )
        state_out = ctx.run(
            ctx.on.relation_broken(tracing_rel),
            _base_state(relations=[tracing_rel]),
        )
        assert state_out.unit_status == testing.ActiveStatus()
        # Charm should remain functional after tracing relation is broken
        assert state_out.workload_version == "0.57.0"


class TestCaCertRelation:
    """Tests for receive-ca-cert relation handling."""

    def test_charm_starts_with_ca_cert_relation(self, ctx):
        """Charm should start and become active with CA cert relation."""
        ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_data={"ca": "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----"},
        )
        state_out = ctx.run(
            ctx.on.update_status(), _base_state(relations=[ca_rel])
        )
        assert state_out.unit_status == testing.ActiveStatus()
        assert state_out.workload_version == "0.57.0"

    def test_charm_starts_with_both_tracing_and_ca_relations(self, ctx):
        """Charm should start with both tracing and CA cert relations."""
        tracing_rel = testing.Relation(
            endpoint="charm-tracing",
            remote_app_data={"receivers": _TRACING_HTTP_APP_DATA},
        )
        ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_data={"ca": "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----"},
        )
        state_out = ctx.run(
            ctx.on.update_status(),
            _base_state(relations=[tracing_rel, ca_rel]),
        )
        assert state_out.unit_status == testing.ActiveStatus()
        assert state_out.workload_version == "0.57.0"
        # Verify both relations are present
        tracing_relations = [r for r in state_out.relations if r.endpoint == "charm-tracing"]
        ca_relations = [r for r in state_out.relations if r.endpoint == "receive-ca-cert"]
        assert len(tracing_relations) == 1
        assert len(ca_relations) == 1
