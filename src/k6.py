"""Helper module to handle the communication between the leader units."""

import json
import os
from typing import Dict, Optional

from enum import Enum
import ops
import re
from ops.pebble import Layer
import logging
from ops import Application, Unit

logger = logging.getLogger(__name__)


class K6Status(Enum):
    """Helper class to represent the status of k6 units."""

    idle = "idle"  # ready to accept a new test
    active = "active"  # currently executing `k6 run`


class K6(ops.Object):
    """Leader-controlled k6 workload manager for all the units."""

    _container_name = "k6"
    _layer_name = "k6"
    _relation_name = "k6"
    _service_name = "k6"
    _default_script_path = "/etc/k6/scripts/juju-config-script.js"

    def __init__(self, *, charm: ops.CharmBase):
        """Construct the workload manager."""
        super().__init__(charm, self._relation_name)
        self._charm = charm
        self.peers: Optional[ops.Relation] = self._charm.model.get_relation(self._relation_name)
        self.container: ops.Container = self._charm.unit.get_container(self._container_name)

        self.framework.observe(
            self._charm.on[self._relation_name].relation_changed,
            self._on_relation_changed,
        )

    def set_peer_data(self, databag: Unit | Application, data: Dict):
        """Store data in the peer relation."""
        if not self.peers:
            return
        for key, value in data.items():
            self.peers.data[databag][key] = json.dumps(value)

    def get_peer_data(self, databag: Unit | Application) -> Optional[Dict]:
        """Get data from the peer relation."""
        if not self.peers:
            return None
        data = {}
        for key, value in self.peers.data[databag].items():
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                data[key] = json.loads(str(value))
        return data if data else None

    def get_all_peer_data(self) -> Optional[Dict]:
        if not self.peers:
            return None
        data = {}
        for unit in self.peers.units:
            data[unit.name] = self.get_peer_data(unit)
        return data

    def _pebble_layer(self, script_path: str, vus: int) -> Layer:
        """Construct the Pebble layer information."""
        layer = Layer(
            {
                "summary": "k6-k8s layer",
                "description": "k6-k8s layer",
                "services": {
                    "k6": {
                        "override": "replace",
                        "summary": "k6 service",
                        "command": f"/usr/bin/k6 run {script_path} --vus {vus} --paused",
                        "startup": "disabled",
                        "environment": {
                            "https_proxy": os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
                            "http_proxy": os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
                            "no_proxy": os.environ.get("JUJU_CHARM_NO_PROXY", ""),
                        },
                    }
                },
            }
        )
        return layer

    def _on_relation_changed(self, _: ops.RelationChangedEvent) -> None:
        """Set the Pebble layer from peer data."""
        data = self.get_peer_data(self._charm.app)
        layer_dict = data.get("layer") if data else None
        # If there is no layer in peer data, stop whatever is running
        if not layer_dict:
            try:
                self.container.stop(self._service_name)
            except ops.pebble.APIError:
                logger.info("k6 is not running")
            return
        # Else, set it and replan
        self.container.add_layer(self._layer_name, Layer(layer_dict), combine=True)
        self.container.replan()
        self.container.start(self._service_name)

    def _get_vus_from_script(self, script_path: str) -> int:
        """Extract the VUs from a script."""
        script = self.container.pull(self._default_script_path, encoding="utf-8").read()
        match = re.search(r"vus:\s*(\d+)", script)
        if not match:
            raise ValueError(f"Cannot parse vus from {script_path}")

        vus = int(match.group(1))
        logger.info(f"Script {script_path} declares {vus} vus")
        return vus

    def initialize(self):
        """Set 'idle' status in each unit if they have no other status."""
        data = self.get_peer_data(self._charm.unit)
        if not data or "status" not in data:
            # TODO: add unit fqdn to peer data for /start call
            self.set_peer_data(self._charm.unit, {"status": K6Status.idle})

    def _start(self):
        """Aaa."""
        if not self._charm.unit.is_leader():
            return
        data = {}
        for unit in self.peers.units:
            pass  # TODO: below
        # Get all the peer unit databags
        # checks for paused in all of them
        # tenacity-retry this method if they are not all "paused" (active)
        # if yes, get their IPs and /start the tests on each one

    def run(self, *, script_path: str):
        """Set a command in the Pebble layer for all units."""
        vus: int = self._get_vus_from_script(script_path=script_path)
        layer = self._pebble_layer(script_path=script_path, vus=vus)
        self.set_peer_data(
            self._charm.app, data={"layer": layer.to_dict(), "status": K6Status.active}
        )

    def stop(self):
        """Stop `k6` in all the units."""
        self.set_peer_data(self._charm.app, data={})

    def is_running(self) -> bool:
        """Check whether k6 is currently running."""
        try:
            service = self.container.get_service(self._service_name)
            if not service:
                return False
            return service.is_running()
        except ops.ModelError:
            return False
