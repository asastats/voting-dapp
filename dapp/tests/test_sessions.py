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
def final_file(tmp_path):
    filename = tmp_path / "final.json"
    filename.write_text(
        json.dumps(
            {
                "start_round": 55,
                "end_round": 99,
                "seats": {"A": {"total": 30}, "B": {"total": 5}, "C": {"total": 1}},
            }
        )
    )
    return filename


def _ballots(**amounts):
    """Ballots of voters; an amount of None means not weighed yet."""
    return {
        address: {
            "option": 0,
            "max_votes": 10,
            "eligible": amount or 0,
            "weight": 0,
            "weighed": amount is not None,
        }
        for address, amount in amounts.items()
    }


@pytest.fixture()
def ended(mocker, connection):
    """An ended session whose holdings rounds are 55 to 99."""
    mocker.patch("dapp.sessions.session_values", return_value={"status": 0})
    mocker.patch("dapp.sessions._holdings", return_value=(55, 99, 120))


class TestSessionsHelpers:
    """Testing class for :py:mod:`dapp.sessions` helpers."""

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

    @pytest.mark.parametrize("network,same", [("testnet", False), ("mainnet", True)])
    def test_sessions_holdings(self, mocker, network, same):
        env = mocker.patch("dapp.sessions.environment_variables").return_value
        indexers = {"mainnet": mocker.MagicMock(), "testnet": mocker.MagicMock()}
        indexers["mainnet"].health.return_value = {"round": 120}
        mocker.patch(
            "dapp.sessions.indexer_client", side_effect=lambda _, name: indexers[name]
        )
        mocked_rounds = mocker.patch(
            "dapp.sessions.holdings_rounds", return_value=(55, 99)
        )

        assert sessions._holdings(network, {"start_round": 1}) == (55, 99, 120)

        mocked_rounds.assert_called_once_with(
            {"start_round": 1}, indexers[network], indexers["mainnet"], 120, same
        )
        assert env is not None

    def test_sessions_pending_final_writes(self):
        ballots = _ballots(A=30, B=4, C=None)
        assert sessions._pending_final_writes(ballots, {"A": 30, "B": 5, "C": 0}) == {
            "B": 5,
            "C": 0,
        }


class TestSessionsCommands:
    """Testing class for :py:mod:`dapp.sessions` commands."""

    def test_sessions_create_hashes_the_proposal(self, mocker, connection, tmp_path):
        proposal = tmp_path / "proposal.md"
        proposal.write_bytes(b"proposal")
        mocked_create = mocker.patch("dapp.sessions.create_session", return_value=3)

        returned = sessions.create("testnet", proposal, 10, 20, 30, quorum=7)

        assert returned == 3
        mocked_create.assert_called_once_with(
            *connection, 10, 20, 30, sessions.sha256_of_file(proposal), quorum=7
        )

    def test_sessions_final_writes_only_the_differences(
        self, mocker, ended, connection, final_file
    ):
        mocker.patch("dapp.sessions.session_ballots", return_value=_ballots(A=30, B=4))
        mocked_set = mocker.patch("dapp.sessions.set_final")

        assert sessions.final("testnet", 1, final_file) == 1

        mocked_set.assert_called_once_with(*connection, 1, {"B": 5})

    def test_sessions_final_refuses_missing_session(self, mocker, connection):
        mocker.patch("dapp.sessions.session_values", return_value=None)
        with pytest.raises(ValueError, match="There is no such session"):
            sessions.final("testnet", 1, "final.json")

    def test_sessions_final_refuses_before_the_end(self, mocker, connection):
        mocker.patch("dapp.sessions.session_values", return_value={"status": 0})
        mocker.patch("dapp.sessions._holdings", return_value=(55, None, 120))
        with pytest.raises(ValueError, match="Voting hasn't ended yet"):
            sessions.final("testnet", 1, "final.json")

    def test_sessions_final_refuses_file_for_other_rounds(
        self, mocker, connection, final_file
    ):
        mocker.patch("dapp.sessions.session_values", return_value={"status": 0})
        mocker.patch("dapp.sessions._holdings", return_value=(55, 98, 120))
        with pytest.raises(ValueError, match="covers rounds 55-99, the session's"):
            sessions.final("testnet", 1, final_file)

    def test_sessions_final_refuses_file_missing_a_voter(
        self, mocker, ended, final_file
    ):
        mocker.patch(
            "dapp.sessions.session_ballots", return_value=_ballots(A=None, D=None)
        )
        with pytest.raises(ValueError, match=r"1 voters are missing.*\['D'\]"):
            sessions.final("testnet", 1, final_file)

    def test_sessions_seal_seals_with_the_file_hash(
        self, mocker, ended, connection, final_file
    ):
        mocker.patch("dapp.sessions.session_ballots", return_value=_ballots(A=30, B=5))
        mocked_seal = mocker.patch("dapp.sessions.seal_final")

        sessions.seal("testnet", 1, final_file)

        mocked_seal.assert_called_once_with(
            *connection, 1, sessions.sha256_of_file(final_file)
        )

    def test_sessions_seal_refuses_boxes_differing_from_file(
        self, mocker, ended, final_file
    ):
        mocker.patch(
            "dapp.sessions.session_ballots", return_value=_ballots(A=30, B=None)
        )
        mocked_seal = mocker.patch("dapp.sessions.seal_final")

        with pytest.raises(ValueError, match="1 voters differ from the file"):
            sessions.seal("testnet", 1, final_file)

        mocked_seal.assert_not_called()

    def test_sessions_period_final(self, mocker, ended, capsys):
        assert sessions.period("testnet", 1) == (55, 99)
        assert capsys.readouterr().out == (
            "python manage.py daosnapshot 55 seats.json /tmp/final.json --to 99 "
            "--settings=engine.settings\n"
        )

    @pytest.mark.parametrize(
        "holdings,end", [((55, None, 80), 79), ((55, 99, 120), 99)]
    )
    def test_sessions_period_so_far(self, mocker, connection, capsys, holdings, end):
        mocker.patch("dapp.sessions.session_values", return_value={"status": 0})
        mocker.patch("dapp.sessions._holdings", return_value=holdings)

        assert sessions.period("testnet", 1, so_far=True) == (55, end)

        assert f"/tmp/sofar.json --to {end} " in capsys.readouterr().out

    def test_sessions_period_refuses_missing_session(self, mocker, connection):
        mocker.patch("dapp.sessions.session_values", return_value=None)
        with pytest.raises(ValueError, match="There is no such session"):
            sessions.period("testnet", 1)

    def test_sessions_period_refuses_before_the_start(self, mocker, connection):
        mocker.patch("dapp.sessions.session_values", return_value={"status": 0})
        mocker.patch("dapp.sessions._holdings", return_value=(None, None, 120))
        with pytest.raises(ValueError, match="Voting hasn't started yet"):
            sessions.period("testnet", 1, so_far=True)

    def test_sessions_period_final_refuses_before_the_end(self, mocker, connection):
        mocker.patch("dapp.sessions.session_values", return_value={"status": 0})
        mocker.patch("dapp.sessions._holdings", return_value=(55, None, 120))
        with pytest.raises(ValueError, match="use --so-far"):
            sessions.period("testnet", 1)

    def test_sessions_close_finalizes_and_shows(self, mocker, connection):
        mocked_finalize = mocker.patch("dapp.sessions.finalize")
        mocked_show = mocker.patch("dapp.sessions.show")

        sessions.close("testnet", 1)

        mocked_finalize.assert_called_once_with(*connection, 1)
        mocked_show.assert_called_once_with("testnet", 1)

    def test_sessions_show_prints_session_and_ballots(self, mocker, connection, capsys):
        mocker.patch("dapp.sessions.session_values", return_value={"status": 1})
        ballots = _ballots(A=30, B=None)
        ballots["A"].update({"option": 2, "weight": 7})
        mocker.patch("dapp.sessions.session_ballots", return_value=ballots)

        sessions.show("testnet", 1)

        output = capsys.readouterr().out
        assert '"status": "sealed"' in output
        assert "A 2 10 30 7\nB 0 10 - -\n" in output

    def test_sessions_show_for_missing_session(self, mocker, connection, capsys):
        mocker.patch("dapp.sessions.session_values", return_value=None)

        sessions.show("testnet", 1)

        assert "There is no such session!" in capsys.readouterr().out

    def test_sessions_current_round(self, mocker, capsys):
        env = mocker.patch("dapp.sessions.environment_variables").return_value
        mocked_client = mocker.patch("dapp.sessions.algod_client")
        mocked_client.return_value.status.return_value = {"last-round": 777}

        assert sessions.current_round("testnet") == 777

        mocked_client.assert_called_once_with(env, "testnet")
        assert "The latest round on testnet is 777" in capsys.readouterr().out


class TestSessionsMain:
    """Testing class for :py:func:`dapp.sessions.main`."""

    @pytest.mark.parametrize(
        "extra,abstain",
        [("", 2), ("--abstain 0", 0), ("--no-abstain", 255), ("--options-count 4", 3)],
    )
    def test_sessions_main_create(self, mocker, extra, abstain):
        mocked = mocker.patch("dapp.sessions.create")
        returned = sessions.main(
            f"create testnet p.md 10 20 30 --quorum 7 --threshold 2 3 {extra}".split()
        )
        assert returned == mocked.return_value
        mocked.assert_called_once_with(
            network="testnet",
            proposal_file="p.md",
            start_round=10,
            end_round=20,
            seal_round=30,
            options_count=4 if "options-count" in extra else 3,
            abstain=abstain,
            quorum=7,
            threshold=(2, 3),
        )

    @pytest.mark.parametrize(
        "command,function", [("final", "final"), ("seal-final", "seal")]
    )
    def test_sessions_main_final_commands(self, mocker, command, function):
        mocked = mocker.patch(f"dapp.sessions.{function}")
        sessions.main([command, "mainnet", "4", "final.json"])
        mocked.assert_called_once_with(
            network="mainnet", session_id=4, final_file="final.json"
        )

    def test_sessions_main_period(self, mocker):
        mocked = mocker.patch("dapp.sessions.period")
        sessions.main(["period", "testnet", "4", "--so-far"])
        mocked.assert_called_once_with(network="testnet", session_id=4, so_far=True)

    @pytest.mark.parametrize("command", ["close", "show"])
    def test_sessions_main_session_commands(self, mocker, command):
        mocked = mocker.patch(f"dapp.sessions.{command}")
        sessions.main([command, "testnet", "4"])
        mocked.assert_called_once_with(network="testnet", session_id=4)

    def test_sessions_main_round(self, mocker):
        mocked = mocker.patch("dapp.sessions.current_round")
        sessions.main(["round", "mainnet"])
        mocked.assert_called_once_with(network="mainnet")
