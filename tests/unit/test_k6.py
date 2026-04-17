# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Unit tests for the K6 helper module."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def k6_instance():
    """Return a K6 instance with a mocked charm and container."""
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))
    from k6 import K6

    mock_charm = MagicMock()
    mock_charm.model.get_relation.return_value = None
    mock_charm.unit.get_container.return_value = MagicMock()
    mock_charm.app.planned_units.return_value = 1

    k6 = K6.__new__(K6)
    k6._charm = mock_charm
    k6.container = mock_charm.unit.get_container.return_value
    k6.peers = None
    k6.prometheus_endpoint = None
    k6.loki_endpoint = None
    return k6


def _mock_pull(content: str):
    """Return a mock pebble pull object that returns the given content."""
    mock_fileobj = MagicMock()
    mock_fileobj.read.return_value = content
    return mock_fileobj


class TestGetVusFromScript:
    def test_constant_vus_executor(self, k6_instance):
        """VUs are parsed from 'vus:' when no constant-arrival-rate executor is used."""
        script = """
        import http from 'k6/http';
        export const options = {
          vus: 10,
          duration: '30s',
        };
        """
        k6_instance.container.pull.return_value = _mock_pull(script)
        assert k6_instance._get_vus_from_script("/path/to/script.js") == 10

    def test_constant_arrival_rate_executor(self, k6_instance):
        """MaxVUs is parsed when constant-arrival-rate executor is used."""
        script = """
        import http from 'k6/http';
        export const options = {
          scenarios: {
            constant_rate_test: {
              executor: 'constant-arrival-rate',
              rate: 2000,
              timeUnit: '1m',
              duration: '20m',
              preAllocatedVUs: 10,
              maxVUs: 30,
            },
          },
        };
        """
        k6_instance.container.pull.return_value = _mock_pull(script)
        assert k6_instance._get_vus_from_script("/path/to/script.js") == 30

    def test_constant_arrival_rate_without_max_vus_raises(self, k6_instance):
        """ValueError is raised when constant-arrival-rate executor has no maxVUs."""
        script = """
        import http from 'k6/http';
        export const options = {
          scenarios: {
            constant_rate_test: {
              executor: 'constant-arrival-rate',
              rate: 2000,
              timeUnit: '1m',
              duration: '20m',
              preAllocatedVUs: 10,
            },
          },
        };
        """
        k6_instance.container.pull.return_value = _mock_pull(script)
        with pytest.raises(ValueError, match="Cannot parse maxVUs from"):
            k6_instance._get_vus_from_script("/path/to/script.js")

    def test_no_vus_raises(self, k6_instance):
        """ValueError is raised when no vus can be found in the script."""
        script = """
        import http from 'k6/http';
        export const options = {
          duration: '30s',
        };
        """
        k6_instance.container.pull.return_value = _mock_pull(script)
        with pytest.raises(ValueError, match="Cannot parse vus from"):
            k6_instance._get_vus_from_script("/path/to/script.js")

    def test_constant_vus_with_whitespace(self, k6_instance):
        """VUs are parsed correctly when there is whitespace around the colon."""
        script = "export const options = { vus:   42, duration: '10s' };"
        k6_instance.container.pull.return_value = _mock_pull(script)
        assert k6_instance._get_vus_from_script("/path/to/script.js") == 42

    def test_constant_arrival_rate_max_vus_with_whitespace(self, k6_instance):
        """MaxVUs is parsed correctly when there is whitespace around the colon."""
        script = (
            "export const options = { scenarios: { t: { executor: 'constant-arrival-rate',"
            " maxVUs:   50, preAllocatedVUs: 5 } } };"
        )
        k6_instance.container.pull.return_value = _mock_pull(script)
        assert k6_instance._get_vus_from_script("/path/to/script.js") == 50

    def test_partial_executor_name_does_not_trigger_arrival_rate_branch(self, k6_instance):
        """Scripts with 'constant-arrival-rate' as substring but not the executor do not trigger maxVUs parsing."""
        script = (
            "// This test uses a custom-constant-arrival-rate-style executor\n"
            "export const options = { vus: 5, duration: '10s' };"
        )
        k6_instance.container.pull.return_value = _mock_pull(script)
        assert k6_instance._get_vus_from_script("/path/to/script.js") == 5
