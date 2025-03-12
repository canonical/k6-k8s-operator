"""Helper module to handle the communication between the leader units."""

import json
import os
from typing import Any, Dict, Optional

import ops
import re
from ops.pebble import Layer
import logging

logger = logging.getLogger(__name__)


class K6(ops.Object):
    """Leader-controlled k6 workload manager for all the units."""

    _relation_name = "k6"
    _container_name = "k6"
    _layer_name = "k6"
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

    def _set_data(self, data: Dict) -> None:
        """Store data in the peer relation."""
        if not self.peers:
            return
        key = "k6"
        self.peers.data[self._charm.app][key] = json.dumps(data)

    def _get_data(self) -> Optional[Dict]:
        """Get data from the peer relation."""
        if not self.peers:
            return None
        key = "k6"
        data = self.peers.data[self._charm.app].get(key)
        return json.loads(data) if data else None

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
                        "command": f"/usr/bin/k6 run {script_path} --vus {vus}",
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
        data = self._get_data()
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

    def run(self, *, script_path: str):
        """Set a command in the Pebble layer for all units."""
        vus: int = self._get_vus_from_script(script_path=script_path)
        layer = self._pebble_layer(script_path=script_path, vus=vus)
        self._set_data(data={"layer": layer.to_dict()})

    def stop(self):
        """Stop `k6` in all the units."""
        self._set_data(data={})

    def is_running(self) -> bool:
        """Check whether k6 is currently running."""
        try:
            self.container.pebble.exec(["k6", "status"]).wait()
        except (ops.pebble.APIError, ops.pebble.ExecError):
            logger.info("k6 is not running")
            return False
        logger.info("k6 is already running a load test")
        return True
