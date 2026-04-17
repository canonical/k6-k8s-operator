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
    mock_charm.unit.name = "k6/0"
    mock_charm.app.planned_units.return_value = 1

    k6 = K6.__new__(K6)
    k6._charm = mock_charm
    k6.container = mock_charm.unit.get_container.return_value
    k6.peers = None
    k6.prometheus_endpoint = None
    k6.loki_endpoint = None
    return k6


def _make_mock_unit(name: str) -> MagicMock:
    """Create a mock Juju unit with the given name."""
    unit = MagicMock()
    unit.name = name
    return unit


class TestExecutionSegmentArgs:
    def test_single_unit_no_peers(self, k6_instance):
        """A single unit (no peers) returns empty string — no segmentation."""
        assert k6_instance._execution_segment_args() == ""

    def test_single_unit_with_peer_relation(self, k6_instance):
        """A single unit with an empty peer relation returns empty string."""
        k6_instance.peers = MagicMock()
        k6_instance.peers.units = set()
        assert k6_instance._execution_segment_args() == ""

    def test_three_units_index_0(self, k6_instance):
        """First unit of 3 gets the first segment."""
        k6_instance._charm.unit = _make_mock_unit("k6/0")
        peer1 = _make_mock_unit("k6/1")
        peer2 = _make_mock_unit("k6/2")
        k6_instance.peers = MagicMock()
        k6_instance.peers.units = {peer1, peer2}

        result = k6_instance._execution_segment_args()
        assert "--execution-segment '0/3:1/3'" in result
        assert "--execution-segment-sequence '0,1/3,2/3,1'" in result

    def test_three_units_index_1(self, k6_instance):
        """Middle unit of 3 gets the second segment."""
        k6_instance._charm.unit = _make_mock_unit("k6/1")
        peer0 = _make_mock_unit("k6/0")
        peer2 = _make_mock_unit("k6/2")
        k6_instance.peers = MagicMock()
        k6_instance.peers.units = {peer0, peer2}

        result = k6_instance._execution_segment_args()
        assert "--execution-segment '1/3:2/3'" in result
        assert "--execution-segment-sequence '0,1/3,2/3,1'" in result

    def test_three_units_index_2(self, k6_instance):
        """Last unit of 3 gets the third segment."""
        k6_instance._charm.unit = _make_mock_unit("k6/2")
        peer0 = _make_mock_unit("k6/0")
        peer1 = _make_mock_unit("k6/1")
        k6_instance.peers = MagicMock()
        k6_instance.peers.units = {peer0, peer1}

        result = k6_instance._execution_segment_args()
        assert "--execution-segment '2/3:3/3'" in result
        assert "--execution-segment-sequence '0,1/3,2/3,1'" in result

    def test_non_contiguous_unit_numbers(self, k6_instance):
        """Non-contiguous unit numbers (e.g. after scale-down) are mapped to contiguous indices."""
        # k6/0, k6/1, k6/3 — k6/2 was removed
        k6_instance._charm.unit = _make_mock_unit("k6/3")
        peer0 = _make_mock_unit("k6/0")
        peer1 = _make_mock_unit("k6/1")
        k6_instance.peers = MagicMock()
        k6_instance.peers.units = {peer0, peer1}

        # k6/3 sorts last → index 2 out of 3
        result = k6_instance._execution_segment_args()
        assert "--execution-segment '2/3:3/3'" in result
        assert "--execution-segment-sequence '0,1/3,2/3,1'" in result

    def test_two_units(self, k6_instance):
        """Two units split the test in half."""
        k6_instance._charm.unit = _make_mock_unit("k6/0")
        peer1 = _make_mock_unit("k6/1")
        k6_instance.peers = MagicMock()
        k6_instance.peers.units = {peer1}

        result = k6_instance._execution_segment_args()
        assert "--execution-segment '0/2:1/2'" in result
        assert "--execution-segment-sequence '0,1/2,1'" in result
