"""Testing module for :py:mod:`dapp.network` module.

The calls themselves run against localnet in `test_integration`.
"""

import base64

import pytest
from algosdk.error import AlgodHTTPError

from dapp import network

ADDRESS = "2EVGZ4BGOSL3J64UYDE2BUGTNTBZZZLI54VUQQNZZLYCDODLY33UGXNSIU"


class TestNetworkReading:
    """Testing class for the functions reading the dApp's boxes."""

    def test_network_box_value_raises_other_node_errors(self, mocker):
        client = mocker.MagicMock()
        client.application_box_by_name.side_effect = AlgodHTTPError("timeout")
        with pytest.raises(AlgodHTTPError, match="timeout"):
            network._box_value(client, 1, b"s")

    def test_network_session_ballots_skips_box_deleted_meanwhile(self, mocker):
        client = mocker.MagicMock()
        name = network.ballot_box_name(1, ADDRESS)
        client.application_boxes.return_value = {
            "boxes": [{"name": base64.b64encode(name).decode()}]
        }
        client.application_box_by_name.side_effect = AlgodHTTPError("box not found")
        assert network.session_ballots(client, 5, 1) == {}

    def test_network_vote_pays_for_the_inner_call(self, mocker):
        client = mocker.MagicMock()
        client.suggested_params.return_value.min_fee = 1000
        mocked_call = mocker.patch("dapp.network._call")
        parameters = {"sender": ADDRESS}

        network.vote(client, 5, 9, parameters, 1, 2)

        kwargs = mocked_call.call_args.kwargs
        assert kwargs["sp"].fee == 2000
        assert kwargs["sp"].flat_fee is True
        assert kwargs["foreign_apps"] == [9]
        assert kwargs["other_boxes"] == [(9, network.decode_address(ADDRESS))]
