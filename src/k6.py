"""Helper module to handle the communication between the leader units."""

import json
import logging
import os
import re
import socket
import typing
import urllib.request
import uuid
from datetime import datetime
from enum import Enum
from types import SimpleNamespace
from typing import Dict, List, Optional

import ops
from cosl import JujuTopology
from ops import Application, Unit
from ops.pebble import Layer

logger = logging.getLogger(__name__)


PORTS = SimpleNamespace(status=6565)


class K6Status(Enum):
    """Helper class to represent the status of k6 units."""

    idle = "idle"  # ready to accept a new test
    busy = "busy"  # currently executing `k6 run` (even if paused)


class K6Api:
    """Helper class to interact with the k6 HTTP API."""

    @staticmethod
    def _request(url: str, method: str, payload: Dict) -> str:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method=method,
        )
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request) as response:
            if retcode := response.getcode() != 200:
                raise Exception(f"Cannot start test on {url}: received {retcode}")
            response_data = response.read()
            return response_data.decode("utf-8")

    @staticmethod
    def resume(endpoint: str) -> None:
        """Resume a paused load test."""
        k6_resume_payload = {"data": {"attributes": {"paused": True}}}
        K6Api._request(url=f"http://{endpoint}/status", method="PATCH", payload=k6_resume_payload)


class K6(ops.Object):
    """Leader-controlled k6 workload manager for all the units."""

    _container_name = "k6"
    _layer_name = "k6"
    _relation_name = "k6"
    _service_name = "k6"
    _pebble_notice_done = "k6.com/done"
    _default_script_path = "/etc/k6/scripts/juju-config-script.js"

    def __init__(
        self,
        *,
        charm: ops.CharmBase,
        prometheus_endpoint: Optional[str] = None,
        loki_endpoint: Optional[str] = None,
    ):
        """Construct the workload manager."""
        super().__init__(charm, self._relation_name)
        self._charm = charm
        self.peers: Optional[ops.Relation] = self._charm.model.get_relation(self._relation_name)
        self.container: ops.Container = self._charm.unit.get_container(self._container_name)
        self.prometheus_endpoint: Optional[str] = prometheus_endpoint
        self.loki_endpoint: Optional[str] = loki_endpoint

        self._initialize()

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
        """Store data in the peer relation under the 'k6' key."""
        if not self.peers:
            return
        self.peers.data[databag]["k6"] = json.dumps(data)

    def clear_peer_data(self, databag: Unit | Application) -> None:
        """Clear the data stored in peer relation under the 'k6' key."""
        if not self.peers:
            return
        self.peers.data[databag]["k6"] = ""

    def get_peer_data(self, databag: Unit | Application) -> Optional[Dict]:
        """Get data from the peer relation under the 'k6' key."""
        if not self.peers:
            return None
        data = self.peers.data[databag].get("k6")
        return json.loads(data) if data else None

    def get_all_peer_unit_data(self) -> Optional[Dict]:
        """Get data from the peer relation for all units."""
        if not self.peers:
            return None
        data = {}
        for unit in [*self.peers.units, self._charm.unit]:
            data[unit.name] = self.get_peer_data(unit)
        return data

    def _pebble_layer(self) -> Layer:
        """Construct the Pebble layer information."""
        data = self.get_peer_data(self._charm.app)
        if not data:
            return Layer()

        labels = self.labels or {}
        # Build labels for Prometheus
        labels_args: List[str] = [f"--tag {key}={value}" for key, value in labels.items()]
        # Build Loki argument
        loki_arg: str = ""
        if self.loki_endpoint:
            loki_labels = ",".join([f"label.{key}={value}" for key, value in labels.items()])
            loki_arg = f"--log-output=loki={self.loki_endpoint},{loki_labels}"
        # Get information from peer data
        script_path = data["script_path"]
        vus = data["vus"]
        # Build the environment args
        environment_args: List[str] = [
            f"-e {key}={value}" for key, value in self.environment.items()
        ]

        # Build the Pebble layer
        layer = Layer(
            {
                "summary": "k6-k8s layer",
                "description": "k6-k8s layer",
                "services": {
                    "k6": {
                        "override": "replace",
                        "summary": "k6 service",
                        "command": (
                            f"/bin/bash -c 'k6 run {script_path} "
                            f"--vus {vus} "
                            f"--address {self.endpoint} "
                            f"{' '.join(labels_args)} "
                            f"{' '.join(environment_args)} "
                            "-o experimental-prometheus-rw "
                            f"{loki_arg} "
                            f"; pebble notify k6.com/done'"
                        ),
                        "startup": "disabled",
                        "environment": {
                            "https_proxy": os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
                            "http_proxy": os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
                            "no_proxy": os.environ.get("JUJU_CHARM_NO_PROXY", ""),
                            "K6_PROMETHEUS_RW_SERVER_URL": self.prometheus_endpoint or "",
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
        app_data = self.get_peer_data(self._charm.app)
        # If the necessary information is not in peer data, stop whatever is running
        if not app_data or not app_data.get("script_path") or not app_data.get("vus"):
            try:
                self.container.stop(self._service_name)
                self.set_peer_data(
                    self._charm.unit,
                    {"endpoint": self.endpoint, "status": K6Status.idle.value},
                )
            except ops.pebble.APIError:
                logger.info("k6 is not running")
            return

        app_status = app_data.get("status")
        # If app and unit 'status' are 'idle', build the layer and start the tests (from the leader)
        if app_status == K6Status.idle.value:
            layer = self._pebble_layer()
            self.container.add_layer(self._layer_name, layer, combine=True)
            self.container.replan()
            self.container.start(self._service_name)
            self.set_peer_data(
                self._charm.unit,
                {"endpoint": self.endpoint, "status": K6Status.busy.value},
            )
            if self._charm.unit.is_leader():
                self._start_test_if_ready()

        # If the leader finds all units in 'idle' and the app status is 'busy',
        # it means a test just finished: set the app status to 'idle' and empty
        # the peer app databag.
        if self._charm.unit.is_leader() and app_status == K6Status.busy.value:
            if self.are_all_units_in_status(K6Status.idle):
                self.clear_peer_data(self._charm.app)

    def _on_pebble_custom_notice(self, event: ops.PebbleCustomNoticeEvent) -> None:
        """React to a Pebble notice."""
        # When the k6 command finished running
        if event.notice.key == self._pebble_notice_done:
            # Set the unit back to 'idle'
            self.set_peer_data(
                self._charm.unit,
                data={"endpoint": self.endpoint, "status": K6Status.idle.value},
            )

    def _get_vus_from_script(self, script_path: str) -> int:
        """Extract the VUs from a script."""
        script = self.container.pull(self._default_script_path, encoding="utf-8").read()
        match = re.search(r"vus:\s*(\d+)", script)
        if not match:
            raise ValueError(f"Cannot parse vus from {script_path}")

        vus = int(match.group(1))
        logger.info(f"Script {script_path} declares {vus} vus")
        return vus

    @property
    def endpoint(self) -> str:
        """The endpoint of the k6 HTTP API."""
        return f"{socket.getfqdn()}:{PORTS.status}"

    @property
    def labels(self) -> Optional[Dict[str, str]]:
        """The labels to attach to a k6 load test."""
        # Get the test_uuid from peer relation data
        data = self.get_peer_data(self._charm.app)
        if not data or "labels" not in data:
            return None
        # Generate the other labels from Juju topology
        topology: JujuTopology = JujuTopology.from_charm(self._charm)
        labels = {
            "test_uuid": data["labels"].get("test_uuid") or "",
            "date": data["labels"].get("date") or "",
            "script": data.get("script_path") or "",
            "juju_charm": topology.charm_name,
            "juju_model": topology.model,
            "juju_model_uuid": topology.model_uuid,
            "juju_application": topology.application,
            "juju_unit": topology.unit,
        }
        return labels

    @property
    def environment(self) -> Dict[str, str]:
        """Get the environment variables for the current k6 script."""
        data = self.get_peer_data(self._charm.app)
        if not data:
            return {}
        environment_raw: str = typing.cast(str, self._charm.config.get("environment", ""))
        if not environment_raw:
            return {}
        environment = dict(item.split("=") for item in environment_raw.split(","))
        return environment

    def _initialize(self):
        """Set 'idle' status in each unit if they have no other status."""
        data = self.get_peer_data(self._charm.unit)
        if not data or "status" not in data:
            self.set_peer_data(
                self._charm.unit,
                {"endpoint": self.endpoint, "status": K6Status.idle.value},
            )

    def _start_test_if_ready(self):
        """Have the leader start a k6 load test in all units."""
        if (
            not self.peers
            or not (peer_data := self.get_all_peer_unit_data())
            or not self.are_all_units_in_status(K6Status.busy)
        ):
            return
        # Update the app 'status' to 'busy'
        app_data = self.get_peer_data(self._charm.app) or {}
        app_data["status"] = K6Status.busy.value
        self.set_peer_data(self._charm.app, data=app_data)
        # Start the load tests on each unit
        endpoints = [unit_data.get("endpoint") for unit_data in peer_data.values()]
        for endpoint in endpoints:
            K6Api.resume(endpoint=endpoint)

    def run(self, *, script_path: str):
        """Set the Pebble layer building blocks in peer data for all units."""
        vus: int = self._get_vus_from_script(script_path=script_path)
        # TODO: also split 'iterations' if present in the script
        # because it's the total shared across all VUs
        test_uuid: str = str(uuid.uuid4())
        start_time: str = datetime.now().isoformat()
        self.set_peer_data(
            self._charm.app,
            data={
                "script_path": script_path,
                "vus": vus,
                "labels": {
                    "test_uuid": test_uuid,
                    "date": start_time,
                },
                "status": K6Status.idle.value,
            },
        )

    def stop(self):
        """Stop `k6` in all the units."""
        self.clear_peer_data(self._charm.app)

    def are_all_units_in_status(self, status: K6Status) -> bool:
        """Check whether all k6 have the provided status."""
        peer_data = self.get_all_peer_unit_data()
        if not peer_data:
            return False
        return all(unit_data.get("status") == status.value for unit_data in peer_data.values())

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
