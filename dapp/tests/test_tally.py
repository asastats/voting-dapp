"""Testing module for :py:mod:`dapp.tally` module.

A whole session is tallied against localnet in `test_integration`.
"""

import base64
import json

import pytest
from algosdk.encoding import decode_address

from dapp import tally
from dapp.configuration import MICRO, NOT_VOTED

SEAT1 = "2EVGZ4BGOSL3J64UYDE2BUGTNTBZZZLI54VUQQNZZLYCDODLY33UGXNSIU"
SEAT2 = "VW55KZ3NF4GDOWI7IPWLGZDFWNXWKSRD5PETRLDABZVU5XPKRJJRK3CBSU"
SEAT3 = "LXJ3Q6RZ2TJ6VCJDFMSM4ZVNYYYE4KVSL3N2TYR23PLNCJCIXBM3NYTBYE"


def _log(session_id, seat, option, weight):
    return (
        tally.VOTED_SELECTOR
        + session_id.to_bytes(8, "big")
        + decode_address(seat)
        + bytes([option])
        + weight.to_bytes(8, "big")
    )


def _event(seat, option, weight, round_=110, session_id=1, txid="T"):
    return {
        "session_id": session_id,
        "seat": seat,
        "option": option,
        "weight": weight,
        "round": round_,
        "txid": txid,
    }


def _session(**kwargs):
    session = {
        "snapshot_round": 100,
        "snapshot_hash": "aa" * 32,
        "start_round": 105,
        "end_round": 120,
        "options_count": 3,
        "quorum": 0,
        "threshold_num": 1,
        "threshold_den": 2,
        "proposal_hash": "bb" * 32,
        "status": 1,
        "winner": NOT_VOTED,
        "voters_count": 2,
        "totals": [4 * MICRO, 3 * MICRO, 0],
    }
    session.update(kwargs)
    return session


def _ballot(eligible, option=NOT_VOTED, max_votes=0, weight=0):
    return {
        "eligible": eligible,
        "option": option,
        "max_votes": max_votes,
        "weight": weight,
    }


def _ballots():
    return {
        SEAT1: _ballot(4 * MICRO, 0, 10, 4 * MICRO),  # capped by the snapshot
        SEAT2: _ballot(9 * MICRO, 1, 3, 3 * MICRO),  # capped by votes
        SEAT3: _ballot(2 * MICRO),  # not voted
    }


def _snapshot(**kwargs):
    snapshot = {
        "round": 100,
        "hash": "aa" * 32,
        "amounts": {SEAT1: 4 * MICRO, SEAT2: 9 * MICRO, SEAT3: 2 * MICRO, "ZERO": 0},
        "proposal_hash": None,
    }
    snapshot.update(kwargs)
    return snapshot


def _events():
    return [
        _event(SEAT1, 1, 4 * MICRO, 106),  # changed later
        _event(SEAT2, 1, 3 * MICRO, 107),
        _event(SEAT1, 0, 4 * MICRO, 108),
        _event(SEAT3, 2, 9, session_id=2),  # another session
    ]


def _run(session=None, ballots=None, snapshot=None, events=None, votes=None, now=115):
    return tally.tally_session(
        1,
        _session() if session is None else session,
        _ballots() if ballots is None else ballots,
        _snapshot() if snapshot is None else snapshot,
        _events() if events is None else events,
        {SEAT1: 10, SEAT2: 3} if votes is None else votes,
        now,
    )


class TestTallyEvents:
    """Testing class for reading the `Voted` events."""

    def test_tally_decode_voted(self):
        assert tally.decode_voted(_log(7, SEAT1, 2, 5 * MICRO)) == {
            "session_id": 7,
            "seat": SEAT1,
            "option": 2,
            "weight": 5 * MICRO,
        }

    def test_tally_decode_voted_ignores_other_logs(self):
        assert tally.decode_voted(b"\x00" * 53) is None
        assert tally.decode_voted(_log(7, SEAT1, 2, 5)[:-1]) is None

    def test_tally_voted_events_walks_pages_and_inner_calls(self, mocker):
        def logs(*items):
            return [base64.b64encode(item).decode() for item in items]

        first = {
            "id": "A",
            "confirmed-round": 106,
            "application-transaction": {"application-id": 5},
            "logs": logs(b"other", _log(1, SEAT1, 0, 3)),
        }
        second = {
            "id": "B",
            "confirmed-round": 108,
            "application-transaction": {"application-id": 77},
            "logs": logs(_log(1, SEAT2, 1, 4)),  # another app's log is not ours
            "inner-txns": [
                {
                    "application-transaction": {"application-id": 5},
                    "logs": logs(_log(1, SEAT3, 2, 5)),
                },
                {"payment-transaction": {"amount": 1}},
            ],
        }
        indexer = mocker.MagicMock()
        indexer.search_transactions.side_effect = [
            {"transactions": [first], "next-token": "n1"},
            {"transactions": [second], "next-token": "n2"},
            {"transactions": []},
        ]
        events = tally.voted_events(indexer, 5, 105, 120)
        assert events == [
            {
                "session_id": 1,
                "seat": SEAT1,
                "option": 0,
                "weight": 3,
                "round": 106,
                "txid": "A",
            },
            {
                "session_id": 1,
                "seat": SEAT3,
                "option": 2,
                "weight": 5,
                "round": 108,
                "txid": "B",
            },
        ]
        assert indexer.search_transactions.call_args_list[0].kwargs == {
            "application_id": 5,
            "min_round": 105,
            "max_round": 120,
            "limit": 1000,
            "next_page": None,
        }
        assert indexer.search_transactions.call_args_list[2].kwargs["next_page"] == "n2"

    def test_tally_voted_events_stops_without_next_token(self, mocker):
        indexer = mocker.MagicMock()
        indexer.search_transactions.return_value = {"transactions": []}
        assert tally.voted_events(indexer, 5, 1, 2) == []
        indexer.search_transactions.assert_called_once()


class TestTallyContractResult:
    """Testing class for :py:func:`dapp.tally.contract_result`."""

    @pytest.mark.parametrize(
        "totals,quorum,threshold,expected",
        [
            ([7, 3, 0], 0, (1, 2), (2, 0, 10)),
            ([5, 5, 0], 0, (1, 2), (3, NOT_VOTED, 10)),  # a tie is not above half
            ([0, 0, 0], 0, (1, 2), (3, NOT_VOTED, 0)),
            ([7, 3, 0], 11, (1, 2), (3, NOT_VOTED, 10)),  # below the quorum
            ([6, 4], 0, (2, 3), (3, NOT_VOTED, 10)),  # not above two thirds
            ([0, 7, 7], 0, (1, 3), (2, 1, 14)),  # a tie goes to the lower option
        ],
    )
    def test_tally_contract_result(self, totals, quorum, threshold, expected):
        assert tally.contract_result(totals, quorum, *threshold) == expected


class TestTallySession:
    """Testing class for :py:func:`dapp.tally.tally_session`."""

    def test_tally_session_everything_agrees(self):
        report = _run()
        assert report["problems"] == []
        assert report["totals"] == [4 * MICRO, 3 * MICRO, 0]
        assert report["turnout"] == 7 * MICRO
        assert report["voters"] == 2
        assert (report["status"], report["winner"]) == (2, 0)
        assert report["notes"] == [
            "Voting runs from round 105 to 120; the latest round is 115"
        ]

    def test_tally_session_for_missing_session(self):
        report = tally.tally_session(1, None, {}, _snapshot(), [], {}, 115)
        assert report == {"problems": ["There is no such session"], "notes": []}

    def test_tally_session_checks_the_files(self):
        report = _run(
            snapshot=_snapshot(round=99, hash="cc" * 32, proposal_hash="dd" * 32)
        )
        assert report["problems"] == [
            "Snapshot file round 99 differs from the session's 100",
            "The snapshot file's sha256 differs from the sealed hash",
            "The proposal file's sha256 differs from the session's",
        ]

    def test_tally_session_accepts_the_right_proposal(self):
        assert _run(snapshot=_snapshot(proposal_hash="bb" * 32))["problems"] == []

    def test_tally_session_before_the_seal(self):
        session = _session(
            status=0, snapshot_hash="00" * 32, voters_count=0, totals=[0, 0, 0]
        )
        ballots = {seat: _ballot(box["eligible"]) for seat, box in _ballots().items()}
        report = _run(session=session, ballots=ballots, events=[], now=101)
        assert report["problems"] == []
        assert report["notes"][0] == "The snapshot is not sealed yet"

    def test_tally_session_notes_a_lapsed_session(self):
        session = _session(
            status=0, snapshot_hash="00" * 32, voters_count=0, totals=[0, 0, 0]
        )
        ballots = {seat: _ballot(box["eligible"]) for seat, box in _ballots().items()}
        report = _run(session=session, ballots=ballots, events=[], now=105)
        assert report["problems"] == []
        assert report["notes"] == [
            "The snapshot is not sealed yet",
            "Lapsed: not sealed before the start round, so no vote is possible",
        ]

    def test_tally_session_compares_boxes_with_the_file(self):
        ballots = _ballots()
        del ballots[SEAT3]
        ballots[SEAT2]["eligible"] = 8 * MICRO
        ballots["EXTRA"] = _ballot(MICRO)
        report = _run(ballots=ballots)
        assert report["problems"][:3] == [
            f"{SEAT3}: in the snapshot file, no ballot box",
            f"{SEAT2}: box eligible {8 * MICRO}, file {9 * MICRO}",
            "EXTRA: ballot box, not in the snapshot file",
        ]

    def test_tally_session_checks_the_events(self):
        events = _events() + [_event(SEAT2, 5, 3 * MICRO, 121)]
        report = _run(events=events)
        assert report["problems"][:2] == [
            f"{SEAT2}: vote in round 121, outside",
            f"{SEAT2}: vote for unknown option 5",
        ]

    def test_tally_session_checks_each_ballot_against_its_events(self):
        ballots = _ballots()
        ballots[SEAT2]["weight"] = 2 * MICRO
        events = [
            _event(SEAT2, 0, 3 * MICRO),
            _event(SEAT3, 1, 2 * MICRO, txid="X"),
        ]
        report = _run(ballots=ballots, events=events)
        assert report["problems"][:4] == [
            f"{SEAT1}: box holds a vote, no Voted event",
            f"{SEAT3}: voted in X, box says not voted",
            f"{SEAT2}: last event option 0 weight {3 * MICRO}, box option 1 weight {2 * MICRO}",
            f"{SEAT2}: weight {2 * MICRO}, the rule gives {3 * MICRO}",
        ]

    def test_tally_session_compares_totals_and_voters(self):
        report = _run(
            session=_session(totals=[4 * MICRO, 2 * MICRO, 0], voters_count=3)
        )
        assert report["problems"] == [
            f"Totals recomputed {[4 * MICRO, 3 * MICRO, 0]}, session {[4 * MICRO, 2 * MICRO, 0]}",
            "Voters recomputed 2, session 3",
        ]

    def test_tally_session_ignores_a_box_option_beyond_the_options(self):
        ballots = _ballots()
        ballots[SEAT2]["option"] = 7
        report = _run(
            ballots=ballots, events=_events()[:1] + [_event(SEAT1, 0, 4 * MICRO)]
        )
        assert report["totals"] == [4 * MICRO, 0, 0]
        assert f"{SEAT2}: box holds a vote, no Voted event" in report["problems"]

    def test_tally_session_notes_votes_changed_since_the_vote(self):
        report = _run(votes={SEAT1: 10})
        assert report["problems"] == []
        assert f"{SEAT2}: votes_of was 3 at its vote, 0 now" in report["notes"]

    def test_tally_session_compares_a_recorded_result(self):
        assert _run(session=_session(status=2, winner=0))["problems"] == []
        report = _run(session=_session(status=3, winner=NOT_VOTED))
        assert report["problems"] == [
            f"Result recomputed passed winner 0, session failed winner {NOT_VOTED}"
        ]

    def test_tally_session_notes_a_session_not_finalized(self):
        report = _run(now=121)
        assert report["notes"] == ["Not finalized yet; finalize would record passed"]


class TestTallyCommand:
    """Testing class for the command line tally."""

    def _files(self, tmp_path):
        snapshot_file = tmp_path / "snapshot.json"
        snapshot_file.write_text(
            json.dumps({"snapshot_round": 100, "seats": {SEAT1: {"total": 4 * MICRO}}})
        )
        proposal = tmp_path / "proposal.md"
        proposal.write_text("Proposal")
        return snapshot_file, proposal

    def test_tally_indexer_client(self, mocker):
        mocked = mocker.patch("dapp.tally.IndexerClient")
        env = {"indexer_token_testnet": "t", "indexer_address_testnet": "https://idx"}
        assert tally.indexer_client(env, "testnet") == mocked.return_value
        mocked.assert_called_once_with("t", "https://idx")

    def test_tally_reads_the_chain_and_prints(self, mocker, tmp_path, capsys):
        snapshot_file, proposal = self._files(tmp_path)
        algod, indexer = mocker.MagicMock(), mocker.MagicMock()
        algod.status.return_value = {"last-round": 115}
        session = _session()
        mocker.patch("dapp.tally.session_values", return_value=session)
        mocker.patch("dapp.tally.session_ballots", return_value=_ballots())
        mocked_events = mocker.patch("dapp.tally.voted_events", return_value=["e"])
        mocked_votes = mocker.patch("dapp.tally.permission_votes", side_effect=[10, 3])
        mocked_tally = mocker.patch(
            "dapp.tally.tally_session",
            return_value={
                "problems": [],
                "notes": ["n"],
                "totals": [1],
                "turnout": 1,
                "voters": 1,
            },
        )
        report = tally.tally(
            "testnet", 1, str(snapshot_file), str(proposal), (algod, indexer, 5, 9)
        )
        assert report == mocked_tally.return_value
        mocked_events.assert_called_once_with(indexer, 5, 105, 120)
        assert mocked_votes.call_args_list == [
            mocker.call(algod, 9, SEAT1),
            mocker.call(algod, 9, SEAT2),
        ]
        args = mocked_tally.call_args.args
        assert args[0] == 1 and args[1] == session and args[4] == ["e"]
        assert args[2] == _ballots()
        assert args[3]["round"] == 100
        assert args[3]["amounts"] == {SEAT1: 4 * MICRO}
        assert args[3]["hash"] == tally.sha256_of_file(snapshot_file).hex()
        assert args[3]["proposal_hash"] == tally.sha256_of_file(proposal).hex()
        assert args[5] == {SEAT1: 10, SEAT2: 3} and args[6] == 115
        assert capsys.readouterr().out == (
            "Session 1\nTotals [1], turnout 1, voters 1\nNOTE n\nEverything agrees\n"
        )

    def test_tally_for_missing_session_uses_configured_clients(
        self, mocker, tmp_path, capsys
    ):
        snapshot_file, _ = self._files(tmp_path)
        mocker.patch("dapp.tally.environment_variables", return_value={})
        mocked_algod = mocker.patch("dapp.tally.algod_client")
        mocked_algod.return_value.status.return_value = {"last-round": 1}
        mocked_indexer = mocker.patch("dapp.tally.indexer_client")
        mocker.patch("dapp.tally.voting_dapp_id", return_value=5)
        mocker.patch("dapp.tally.permission_dapp_id", return_value=9)
        mocked_values = mocker.patch("dapp.tally.session_values", return_value=None)
        mocked_ballots = mocker.patch("dapp.tally.session_ballots")
        report = tally.tally("testnet", 3, str(snapshot_file))
        assert report["problems"] == ["There is no such session"]
        mocked_values.assert_called_once_with(mocked_algod.return_value, 5, 3)
        mocked_indexer.assert_called_once_with({}, "testnet")
        mocked_ballots.assert_not_called()
        assert capsys.readouterr().out == (
            "Session 3\nPROBLEM There is no such session\n1 problems\n"
        )

    def test_tally_main_returns_the_exit_status(self, mocker):
        mocked = mocker.patch("dapp.tally.tally", return_value={"problems": []})
        assert tally.main(["testnet", "2", "s.json", "--proposal", "p.md"]) == 0
        mocked.assert_called_once_with(
            network="testnet", session_id=2, snapshot_file="s.json", proposal="p.md"
        )
        mocked.return_value = {"problems": ["x"]}
        assert tally.main(["mainnet", "2", "s.json"]) == 1
