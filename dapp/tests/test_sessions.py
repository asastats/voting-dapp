"""Testing module for :py:mod:`dapp.sessions` module."""

import json

import pytest

from dapp import sessions


@pytest.fixture()
def connection(mocker):
    """Mock the connection; return its client, app ID and admin parameters."""
    returned = (mocker.MagicMock(), 505, {"sender": "ADMIN"})
    mocker.patch("dapp.sessions._connection", return_value=returned)
    return returned


@pytest.fixture()
def snapshot_file(tmp_path):
    filename = tmp_path / "snapshot.json"
    filename.write_text(
        json.dumps(
            {
                "snapshot_round": 55,
                "seats": {"A": {"total": 30}, "B": {"total": 5}},
            }
        )
    )
    return filename


def _ballots(**eligible):
    return {
        address: {"eligible": amount, "option": 255, "max_votes": 0, "weight": 0}
        for address, amount in eligible.items()
    }


class TestSessionsCommands:
    """Testing class for :py:mod:`dapp.sessions` commands."""

    def test_sessions_connection(self, mocker):
        env = mocker.patch("dapp.sessions.environment_variables").return_value
        mocked_client = mocker.patch("dapp.sessions.algod_client")
        mocked_id = mocker.patch("dapp.sessions.voting_dapp_id")
        mocked_parameters = mocker.patch("dapp.sessions.admin_parameters")

        returned = sessions._connection("mainnet")

        assert returned == (
            mocked_client.return_value,
            mocked_id.return_value,
            mocked_parameters.return_value,
        )
        mocked_client.assert_called_once_with(env, "mainnet")
        mocked_id.assert_called_once_with(network="mainnet")
        mocked_parameters.assert_called_once_with(env, network="mainnet")

    def test_sessions_create_hashes_the_proposal(self, mocker, connection, tmp_path):
        proposal = tmp_path / "proposal.md"
        proposal.write_bytes(b"proposal")
        mocked_create = mocker.patch("dapp.sessions.create_session", return_value=3)

        returned = sessions.create("testnet", proposal, 10, 20, 30, quorum=7)

        assert returned == 3
        mocked_create.assert_called_once_with(
            *connection, 10, 20, 30, sessions.sha256_of_file(proposal), quorum=7
        )

    def test_sessions_snapshot_writes_only_the_differences(
        self, mocker, connection, snapshot_file
    ):
        mocker.patch(
            "dapp.sessions.session_ballots", return_value=_ballots(A=30, B=4, C=9)
        )
        mocked_set = mocker.patch("dapp.sessions.set_snapshot")

        returned = sessions.snapshot("testnet", 1, snapshot_file)

        assert returned == 2
        mocked_set.assert_called_once_with(*connection, 1, {"B": 5, "C": 0})

    def test_sessions_seal_seals_with_the_file_hash(
        self, mocker, connection, snapshot_file
    ):
        mocker.patch(
            "dapp.sessions.session_values", return_value={"snapshot_round": 55}
        )
        mocker.patch("dapp.sessions.session_ballots", return_value=_ballots(A=30, B=5))
        mocked_seal = mocker.patch("dapp.sessions.seal_snapshot")

        sessions.seal("testnet", 1, snapshot_file)

        mocked_seal.assert_called_once_with(
            *connection, 1, sessions.sha256_of_file(snapshot_file)
        )

    @pytest.mark.parametrize("session", [None, {"snapshot_round": 54}])
    def test_sessions_seal_refuses_file_for_another_round(
        self, mocker, connection, snapshot_file, session
    ):
        mocker.patch("dapp.sessions.session_values", return_value=session)
        mocked_seal = mocker.patch("dapp.sessions.seal_snapshot")

        with pytest.raises(ValueError, match="isn't for this session's snapshot"):
            sessions.seal("testnet", 1, snapshot_file)

        mocked_seal.assert_not_called()

    def test_sessions_seal_refuses_boxes_differing_from_file(
        self, mocker, connection, snapshot_file
    ):
        mocker.patch(
            "dapp.sessions.session_values", return_value={"snapshot_round": 55}
        )
        mocker.patch("dapp.sessions.session_ballots", return_value=_ballots(A=30))
        mocked_seal = mocker.patch("dapp.sessions.seal_snapshot")

        with pytest.raises(ValueError, match="1 seats differ from the file"):
            sessions.seal("testnet", 1, snapshot_file)

        mocked_seal.assert_not_called()

    def test_sessions_close_finalizes_and_shows(self, mocker, connection):
        mocked_finalize = mocker.patch("dapp.sessions.finalize")
        mocked_show = mocker.patch("dapp.sessions.show")

        sessions.close("testnet", 1)

        mocked_finalize.assert_called_once_with(*connection, 1)
        mocked_show.assert_called_once_with("testnet", 1)

    def test_sessions_show_prints_session_and_ballots(self, mocker, connection, capsys):
        mocker.patch("dapp.sessions.session_values", return_value={"status": 1})
        ballots = _ballots(A=30, B=5)
        ballots["B"].update({"option": 2, "weight": 5})
        mocker.patch("dapp.sessions.session_ballots", return_value=ballots)

        sessions.show("testnet", 1)

        output = capsys.readouterr().out
        assert '"status": "sealed"' in output
        assert "A 30 - 0\nB 5 2 5\n" in output

    def test_sessions_show_for_missing_session(self, mocker, connection, capsys):
        mocker.patch("dapp.sessions.session_values", return_value=None)

        sessions.show("testnet", 1)

        assert "There is no such session!" in capsys.readouterr().out


class TestSessionsMain:
    """Testing class for :py:func:`dapp.sessions.main`."""

    def test_sessions_main_create(self, mocker):
        mocked = mocker.patch("dapp.sessions.create")
        # argparse binds the function at parser build time
        returned = sessions.main(
            "create testnet p.md 10 20 30 --quorum 7 --threshold 2 3".split()
        )
        assert returned == mocked.return_value
        mocked.assert_called_once_with(
            network="testnet",
            proposal_file="p.md",
            snapshot_round=10,
            start_round=20,
            end_round=30,
            options_count=3,
            quorum=7,
            threshold=(2, 3),
        )

    @pytest.mark.parametrize("command", ["snapshot", "seal"])
    def test_sessions_main_snapshot_commands(self, mocker, command):
        mocked = mocker.patch(f"dapp.sessions.{command}")
        sessions.main([command, "mainnet", "4", "snapshot.json"])
        mocked.assert_called_once_with(
            network="mainnet", session_id=4, snapshot_file="snapshot.json"
        )

    @pytest.mark.parametrize("command", ["close", "show"])
    def test_sessions_main_session_commands(self, mocker, command):
        mocked = mocker.patch(f"dapp.sessions.{command}")
        sessions.main([command, "testnet", "4"])
        mocked.assert_called_once_with(network="testnet", session_id=4)
