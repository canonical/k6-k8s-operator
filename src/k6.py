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
import socket
import requests

logger = logging.getLogger(__name__)


class K6Status(Enum):
    """Helper class to represent the status of k6 units."""

    idle = "idle"  # ready to accept a new test
    busy = "busy"  # currently executing `k6 run` (even if paused)


class K6Api:
    """Helper class to interact with the k6 HTTP API."""

    @staticmethod
    def resume(endpoint: str) -> None:
        """Resume a paused load test."""
        k6_resume_payload = {"data": {"attributes": {"paused": True}}}
        response = requests.patch(f"{endpoint}/status", json=k6_resume_payload)
        if response.status_code != 200:
            raise Exception(f"Cannot start test on {endpoint}: received {response.status_code}")


class K6(ops.Object):
    """Leader-controlled k6 workload manager for all the units."""

    _container_name = "k6"
    _layer_name = "k6"
    _relation_name = "k6"
    _service_name = "k6"
    _pebble_notice_done = "k6.com/done"
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
        # Pebble notices only wake up the unit emitting them
        self.framework.observe(
            self._charm.on[self._service_name].pebble_custom_notice,
            self._on_pebble_custom_notice,
        )
        self.framework.observe(self._charm.on.collect_unit_status, self._collect_unit_status)
        self.framework.observe(self._charm.on.collect_app_status, self._collect_app_status)

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
                data[key] = json.loads(f'"{str(value)}"')
        return data if data else None

    def get_all_peer_unit_data(self) -> Optional[Dict]:
        """Get data from the peer relation for all units."""
        if not self.peers:
            return None
        data = {}
        for unit in self.peers.units:
            data[unit.name] = self.get_peer_data(unit)
        return data

    def _pebble_layer(self, script_path: str, vus: int) -> Layer:
        """Construct the Pebble layer information."""
        command = f"""#!/usr/bin/env bash
/usr/bin/k6 run {script_path} --vus {vus} --paused; pebble notify {self._pebble_notice_done}
        """
        self.container.push("/etc/k6/start.sh", command, make_dirs=True, permissions=0o755)
        layer = Layer(
            {
                "summary": "k6-k8s layer",
                "description": "k6-k8s layer",
                "services": {
                    "k6": {
                        "override": "replace",
                        "summary": "k6 service",
                        "command": "/etc/k6/start.sh",
                        "startup": "disabled",
                        "environment": {
                            "_command": command,
                            "https_proxy": os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
                            "http_proxy": os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
                            "no_proxy": os.environ.get("JUJU_CHARM_NO_PROXY", ""),
                        },
                    },
                },
            }
        )
        return layer

    def _collect_unit_status(self, event: ops.CollectStatusEvent) -> None:
        """Set the status for each unit based on the peer relation databag."""
        data = self.get_peer_data(self._charm.unit)
        if not data or "status" not in data or data["status"] == K6Status.idle.value:
            event.add_status(ops.ActiveStatus())
            return
        event.add_status(ops.ActiveStatus(f"k6 status: {data.get('status')}"))

    def _collect_app_status(self, event: ops.CollectStatusEvent) -> None:
        """Set the status for the application based on peer data."""
        peer_data = self.get_all_peer_unit_data()
        if not peer_data:
            event.add_status(ops.ActiveStatus("k6 status: idle"))
            return
        units = self._charm.app.planned_units()
        busy_units = len([d for d in peer_data.values() if d.get("status") == K6Status.busy.value])
        if busy_units == 0:
            event.add_status(ops.ActiveStatus("k6 status: idle"))
            return
        event.add_status(ops.ActiveStatus(f"k6 status: busy ({busy_units}/{units} units)"))

    def _on_relation_changed(self, _: ops.RelationChangedEvent) -> None:
        """Set the Pebble layer from peer data."""
        data = self.get_peer_data(self._charm.app)
        layer_dict = data.get("layer") if data else None
        # If there is no layer in peer data, stop whatever is running
        if not layer_dict:
            try:
                self.container.stop(self._service_name)
                self.set_peer_data(self._charm.unit, {"status": K6Status.idle.value})
            except ops.pebble.APIError:
                logger.info("k6 is not running")
            return
        # Prepare the start.sh script
        command = layer_dict["services"][self._service_name]["environment"]["_command"]
        self.container.push("/etc/k6/start.sh", command, make_dirs=True, permissions=0o755)
        # Set the layer, replan and start the load tests from the leader
        self.container.add_layer(self._layer_name, Layer(layer_dict), combine=True)
        self.container.replan()
        self.container.start(self._service_name)
        self.set_peer_data(self._charm.unit, {"status": K6Status.busy.value})
        self._start_test_if_ready()

    def _on_pebble_custom_notice(self, event: ops.PebbleCustomNoticeEvent) -> None:
        """React to a Pebble notice."""
        # When the k6 command finished running
        if event.notice.key == self._pebble_notice_done:
            # Set the unit back to 'idle'
            self.set_peer_data(self._charm.unit, data={"layer": {}, "status": K6Status.idle.value})

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
            self.set_peer_data(
                self._charm.unit,
                {"status": K6Status.idle.value, "endpoint": f"http://{socket.getfqdn()}:6565"},
            )

    def _start_test_if_ready(self):
        """Have the leader start a k6 load test in all units."""
        if (
            not self._charm.unit.is_leader()
            or not self.peers
            or not (peer_data := self.get_all_peer_unit_data())
            or not self.are_all_units_ready()
        ):
            return
        endpoints = [unit_data.get("endpoint") for unit_data in peer_data.values()]
        for endpoint in endpoints:
            K6Api.resume(endpoint=endpoint)

    def run(self, *, script_path: str):
        """Set a command in the Pebble layer for all units."""
        vus: int = self._get_vus_from_script(script_path=script_path)
        layer = self._pebble_layer(script_path=script_path, vus=vus)
        self.set_peer_data(self._charm.app, data={"layer": layer.to_dict()})

    def stop(self):
        """Stop `k6` in all the units."""
        self.set_peer_data(self._charm.app, data={"layer": {}})

    def are_all_units_ready(self) -> bool:
        """Check whether all k6 units are ready for tests to be started.

        This means that the Pebble layer has been set with the (--paused) flag,
        and that all the units set their status as "busy" in peer data.
        """
        peer_data = self.get_all_peer_unit_data()
        busy = K6Status.busy.value
        if not peer_data:
            return False
        return all(unit_data.get("status") == busy for unit_data in peer_data.values())

    def is_running(self) -> bool:
        """Check whether k6 is currently running in any unit."""
        peer_data = self.get_all_peer_unit_data()
        busy = K6Status.busy.value
        if not peer_data:
            return False
        return any(unit_data.get("status") == busy for unit_data in peer_data.values())

    def is_running_on_unit(self) -> bool:
        """Check whether k6 is currently running in the current unit."""
        try:
            service = self.container.get_service(self._service_name)
            if not service:
                return False
            return service.is_running()
        except ops.ModelError:
            return False
