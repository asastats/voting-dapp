"""Testing module for :py:mod:`dapp.network` module.

The calls themselves run against localnet in `test_integration`.
"""

import base64

import pytest
from algosdk.error import AlgodHTTPError, IndexerHTTPError

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

    def test_network_permission_votes_decodes_the_box_as_votes_of(self, mocker):
        client = mocker.MagicMock()
        value = base64.b64encode(
            (12).to_bytes(8, "big") + bytes(40)
        )  # the Permission box holds base64 text
        client.application_box_by_name.return_value = {
            "value": base64.b64encode(value).decode()
        }
        assert network.permission_votes(client, 9, ADDRESS) == 12
        client.application_box_by_name.assert_called_once_with(
            9, network.decode_address(ADDRESS)
        )

    def test_network_permission_votes_is_zero_without_a_box(self, mocker):
        client = mocker.MagicMock()
        client.application_box_by_name.side_effect = AlgodHTTPError("box not found")
        assert network.permission_votes(client, 9, ADDRESS) == 0


class TestNetworkCall:
    """Testing class for :py:func:`dapp.network._call`."""

    def _failing_call(self, mocker, message):
        mocked_atc = mocker.patch("dapp.network.AtomicTransactionComposer")
        mocked_atc.return_value.execute.side_effect = AlgodHTTPError("rejected", 400)
        mocker.patch("dapp.network.error_message", return_value=message)
        return network._call(mocker.MagicMock(), 5, mocker.MagicMock(), "vote", [])

    def test_network_call_names_the_failed_assert(self, mocker):
        with pytest.raises(AlgodHTTPError, match="Voting has ended .rejected.") as info:
            self._failing_call(mocker, "Voting has ended")
        assert info.value.code == 400

    def test_network_call_raises_other_errors_unchanged(self, mocker):
        with pytest.raises(AlgodHTTPError, match="^rejected$"):
            self._failing_call(mocker, None)


class _Indexer:
    """Indexer stand-in: `timestamps` by round, unknown rounds not confirmed."""

    def __init__(self, timestamps):
        self.timestamps, self.calls = timestamps, []

    def block_info(self, block):
        self.calls.append(block)
        if block not in self.timestamps:
            raise IndexerHTTPError("error fetching block")
        return {"timestamp": self.timestamps[block]}


class TestNetworkHoldingsRounds:
    """Testing class for the functions mapping session rounds to holdings rounds."""

    MAINNET = {block: 1000 + 3 * block for block in range(1, 101)}  # 3 s a round

    def test_network_block_timestamp(self):
        assert network.block_timestamp(_Indexer({7: 99}), 7) == 99

    @pytest.mark.parametrize(
        "timestamp,expected",
        [(1003, 1), (1150, 50), (1151, 50), (1300, 100), (9e9, 100)],
    )
    def test_network_round_at_timestamp(self, timestamp, expected):
        indexer = _Indexer(self.MAINNET)
        assert network.round_at_timestamp(indexer, timestamp, 100) == expected
        assert len(indexer.calls) < 10

    def test_network_holdings_rounds_maps_times(self):
        voting = _Indexer({20: 1060, 30: 1151})
        returned = network.holdings_rounds(
            {"start_round": 20, "end_round": 30},
            voting,
            _Indexer(self.MAINNET),
            100,
            False,
        )
        assert returned == (20, 50)

    def test_network_holdings_rounds_same_network(self):
        voting = _Indexer({20: 1, 30: 2})
        returned = network.holdings_rounds(
            {"start_round": 20, "end_round": 30}, voting, None, 100, True
        )
        assert returned == (20, 30)

    def test_network_holdings_rounds_before_the_end(self):
        returned = network.holdings_rounds(
            {"start_round": 20, "end_round": 30},
            _Indexer({20: 1060}),
            _Indexer(self.MAINNET),
            100,
            False,
        )
        assert returned == (20, None)
