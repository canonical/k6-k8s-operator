#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""A Juju charm for k6 on Kubernetes."""

from types import SimpleNamespace
from typing import cast
from pathlib import Path
import logging
import os
import re

from ops import CharmBase, main, ActionEvent
from ops.model import ActiveStatus

from ops.pebble import ExecError, Layer

logger = logging.getLogger(__name__)

PORTS = SimpleNamespace(status=6565)


class K6K8sCharm(CharmBase):
    """Charm to run k6 on Kubernetes."""

    _scripts_folder = Path("/etc/k6/scripts")
    _default_script_path = "/etc/k6/scripts/juju-config-script.js"
    _ports = list(PORTS.__dict__.values())

    def __init__(self, *args):
        super().__init__(*args)
        self.container = self.unit.get_container("k6")
        if not self.container.can_connect():
            return

        self._reconcile()
        # Juju actions
        self.framework.observe(self.on.run_action, self._on_run_action)
        self.framework.observe(self.on.status_action, self._on_status_action)

    def _reconcile(self):
        """Recreate the world state for the charm."""
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

        script_path = self._default_script_path
        # Run the k6 script
        vus: int = self.get_vus(script_path=script_path) // self.app.planned_units()
        layer = self._pebble_layer(script_path=script_path, vus=vus)
        self.container.add_layer("k6", layer, combine=True)  # TODO: extract service name
        self.container.start("k6")  # TODO: extract this into service name
        event.log(f"Load test {script_path} started")

    def _on_stop_action(self, event: ActionEvent) -> None:
        if not self.unit.is_leader():
            event.fail("You can only run the action on the leader unit.")
            return

        self.container.stop("k6")

    def _on_status_action(self, event: ActionEvent) -> None:
        if not self.unit.is_leader():
            event.fail("You can only run the action on the leader unit.")
            return

        try:
            stdout, _ = self.container.pebble.exec(["k6", "status"]).wait_output()
            event.log(stdout)
        except ExecError:
            event.log("k6 is currently not running.")

    def _pebble_layer(self, script_path: str, vus: int) -> Layer:
        """Construct the Pebble layer informataion."""
        layer = Layer(
            {
                "summary": "k6-k8s layer",
                "description": "k6-k8s layer",
                "services": {
                    "k6": {
                        "override": "replace",
                        "summary": "k6 service",
                        "command": f"/usr/bin/k6 run {script_path}",
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

    def push_script_from_config(self):
        """Push the k6 script in Juju config to the container."""
        script = cast(str, self.config.get("script", None))
        if script:
            self.container.push(self._default_script_path, script, make_dirs=True)
        else:
            self.container.remove_path(self._default_script_path, recursive=True)

    def is_k6_running(self) -> bool:
        """Check whether a k6 script is already running."""
        try:
            self.container.pebble.exec(["k6", "status"]).wait()
            logger.info("k6 is already running a test")
            return True
        except ExecError:
            logger.info("k6 isn't running")
            return False

    def get_vus(self, script_path: str) -> int:
        """Extract the VUs from a script."""
        script = self.container.pull(self._default_script_path, encoding="utf-8").read()
        match = re.search(r"vus:\s*(\d+)", script)
        if not match:
            raise Exception(f"Cannot parse vus from {script_path}")

        vus = int(match.group(1))
        logger.info(f"Script {script_path} declares {vus} vus")
        return vus


if __name__ == "__main__":
    main(K6K8sCharm)
