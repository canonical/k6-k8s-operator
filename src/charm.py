#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""A Juju charm for k6 on Kubernetes."""

import logging
from pathlib import Path
import re
from typing import Optional, cast

from charms.loki_k8s.v1.loki_push_api import LokiPushApiConsumer
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteConsumer
from ops import ActionEvent, CharmBase, main
from ops.model import ActiveStatus

from k6 import K6, PORTS

logger = logging.getLogger(__name__)


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

        self.prometheus = PrometheusRemoteWriteConsumer(self)
        self.loki = LokiPushApiConsumer(self)
        prometheus_endpoints = self.prometheus.endpoints
        loki_endpoints = self.loki.loki_endpoints
        self.k6 = K6(
            charm=self,
            prometheus_endpoint=prometheus_endpoints[0]["url"] if prometheus_endpoints else None,
            loki_endpoint=loki_endpoints[0]["url"] if loki_endpoints else None,
        )
        self._reconcile()
        # Juju actions
        self.framework.observe(self.on.start_action, self._on_start_action)
        self.framework.observe(self.on.stop_action, self._on_stop_action)

    def _reconcile(self):
        """Recreate the world state for the charm."""
        self.unit.set_ports(*self._ports)
        self.unit.set_workload_version(self._k6_version or "")
        self.push_script_from_config()
        self.unit.status = ActiveStatus()

    def _on_start_action(self, event: ActionEvent) -> None:
        """Run a load test script with `k6 run`."""
        if not self.unit.is_leader():
            event.fail("You can only run the action on the leader unit.")
            return
        if self.k6.is_running():
            event.fail("A load test is already running; please wait for it to finish.")
            return

        script_path = self._default_script_path
        if not self.container.exists(script_path):
            event.fail("No script found; set a script via `juju config load-test=@file.js`")
            return

        # Run the k6 script
        self.k6.run(script_path=script_path)
        event.log(f"Load test {script_path} started on all units")

    def _on_stop_action(self, event: ActionEvent) -> None:
        if not self.unit.is_leader():
            event.fail("You can only run this action on the leader unit.")
            return
        self.k6.stop()

    def push_script_from_config(self):
        """Push the k6 script in Juju config to the container."""
        script = cast(str, self.config.get("load-test", None))
        if script:
            self.container.push(self._default_script_path, script, make_dirs=True)
        else:
            self.container.remove_path(self._default_script_path, recursive=True)

    @property
    def _k6_version(self) -> Optional[str]:
        """Returns the version of k6."""
        version_output, _ = self.container.exec(["k6", "--version"]).wait_output()
        # k6 v0.57.0 (go1.22.12, linux/amd64) ...
        result = re.search(r"k6 v(\d+\.\d+\.\d+)", version_output)
        return result.group(1) if result else None


if __name__ == "__main__":
    main(K6K8sCharm)
