# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common helper functions for integration tests."""

import jubilant


def get_unit_address(juju: jubilant.Juju, app: str, unit_num: int = 0) -> str:
    """Get the address of a Juju unit from status."""
    status = juju.status()
    unit = status.apps[app].units[f"{app}/{unit_num}"]
    return unit.address
