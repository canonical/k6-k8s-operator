#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""A Juju charm for k6 on Kubernetes."""

from enum import Enum
from typing import cast
from pathlib import Path
import logging
import os

from ops import CharmBase, main, ActionEvent
from ops.model import ActiveStatus

from ops.pebble import ExecError, Layer
from collections import namedtuple

logger = logging.getLogger(__name__)
RulesMapping = namedtuple("RulesMapping", ["src", "dest"])


class Ports(Enum):
    """Ports used by the k6 charm."""

    STATUS = 6565


class K6K8sCharm(CharmBase):
    """Charm to run k6 on Kubernetes."""

    _scripts_folder = Path("/etc/k6/scripts")
    _default_script_path = "/etc/k6/scripts/juju-config-script.js"
    _container_name = "k6"
    _ports = [port.value for port in Ports]

    def __init__(self, *args):
        super().__init__(*args)
        if not self.unit.get_container(self._container_name).can_connect():
            return

        self._reconcile()
        # Juju actions
        self.framework.observe(self.on.run_action, self._on_run_action)
        self.framework.observe(self.on.status_action, self._on_status_action)

    def _reconcile(self):
        """Recreate the world state for the charm."""
        container = self.unit.get_container(self._container_name)
        self.unit.set_ports(*self._ports)

        self.push_script_from_config()

        self.unit.status = ActiveStatus()

    def _on_run_action(self, event: ActionEvent) -> None:
        """Run a load test script with `k6 run`."""
        if not self.unit.is_leader():
            event.fail("You can only run the action on the leader unit.")
            return
        if self.is_k6_running():
            event.fail("A load test is already running; please wait for it to finish.")
            return

        container = self.unit.get_container(self._container_name)
        script_path = self._default_script_path
        # Assemble the script path if provided via the action
        if script := event.params.get("script"):
            script_app = event.params.get("app")
            script_path = f"{self._scripts_folder}/{script_app}/{script}"
            if not script_path.endswith(".js") or not script_path.endswith(".ts"):
                script_path = f"{script_path}.js"
        # Run the k6 script
        layer = self._pebble_layer(script_path=script_path)
        container.add_layer(self._container_name, layer, combine=True)
        container.start("charmed-k6")  # TODO: extract this into service name

    def _on_stop_action(self, event: ActionEvent) -> None:
        if not self.unit.is_leader():
            event.fail("You can only run the action on the leader unit.")
            return

        container = self.unit.get_container(self._container_name)
        if not self.is_k6_running():
            event.log("k6 is not running, no need to stop anything smh")
        container.stop()

    def _on_status_action(self, event: ActionEvent) -> None:
        if not self.unit.is_leader():
            event.fail("You can only run the action on the leader unit.")
            return

        container = self.unit.get_container(self._container_name)
        try:
            stdout, _ = container.pebble.exec(["k6", "status"]).wait()
            event.log(stdout)
        except ExecError:
            event.log("k6 is currently not running.")

    def _pebble_layer(self, script_path: str) -> Layer:
        """Construct the Pebble layer informataion."""
        layer = Layer(
            {
                "summary": "k6-k8s layer",
                "description": "k6-k8s layer",
                "services": {
                    "charmed-k6": {
                        "override": "replace",
                        "summary": "k6 service",
                        "command": f"/usr/bin/k6 run {script_path}",
                        "startup": "disabled",
                        "environment": {
                            # TODO: put the hash of the script
                            "https_proxy": os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
                            "http_proxy": os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
                            "no_proxy": os.environ.get("JUJU_CHARM_NO_PROXY", ""),
                        },
                    }
                },
            }
        )

        return layer

    def push_script_from_config(self):
        """Push the k6 script in Juju config to the container."""
        script = cast(str, self.config.get("script", None))
        container = self.unit.get_container(self._container_name)
        if script:
            container.push(self._default_script_path, script, make_dirs=True)
        else:
            container.remove_path(self._default_script_path, recursive=True)

    def is_k6_running(self) -> bool:
        """Check whether a k6 script is already running."""
        container = self.unit.get_container(self._container_name)
        try:
            container.pebble.exec(["k6", "status"]).wait()
            logger.info("k6 is already running a test")
            return True
        except ExecError:
            logger.info("k6 isn't running")
            return False


if __name__ == "__main__":
    main(K6K8sCharm)
