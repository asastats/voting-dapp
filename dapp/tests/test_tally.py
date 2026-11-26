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


def _log(session_id, seat, option, max_votes):
    return (
        tally.VOTED_SELECTOR
        + session_id.to_bytes(8, "big")
        + decode_address(seat)
        + bytes([option])
        + max_votes.to_bytes(8, "big")
    )


def _event(seat, option, max_votes, round_=110, session_id=1, txid="T"):
    return {
        "session_id": session_id,
        "seat": seat,
        "option": option,
        "max_votes": max_votes,
        "round": round_,
        "txid": txid,
    }


def _session(**kwargs):
    """A sealed session: SEAT1 voted 0 (weight 4), SEAT2 voted 1 (weight 3)."""
    session = {
        "start_round": 105,
        "end_round": 120,
        "seal_round": 130,
        "options_count": 3,
        "abstain": 2,
        "quorum": 0,
        "threshold_num": 1,
        "threshold_den": 2,
        "proposal_hash": "bb" * 32,
        "final_hash": "aa" * 32,
        "status": 1,
        "winner": NOT_VOTED,
        "voters_count": 2,
        "weighed_count": 2,
        "totals": [4 * MICRO, 3 * MICRO, 0],
    }
    session.update(kwargs)
    return session


def _ballot(option, max_votes, eligible=0, weight=0, weighed=True):
    return {
        "option": option,
        "max_votes": max_votes,
        "eligible": eligible,
        "weight": weight,
        "weighed": weighed,
    }


def _ballots():
    return {
        SEAT1: _ballot(0, 10, 4 * MICRO, 4 * MICRO),  # capped by the lowest amount
        SEAT2: _ballot(1, 3, 9 * MICRO, 3 * MICRO),  # capped by votes
    }


def _final(**kwargs):
    final = {
        "file_rounds": (500, 900),
        "rounds": (500, 900),
        "hash": "aa" * 32,
        "amounts": {SEAT1: 4 * MICRO, SEAT2: 9 * MICRO, SEAT3: 2 * MICRO},
        "proposal_hash": None,
    }
    final.update(kwargs)
    return final


def _events():
    return [
        _event(SEAT1, 1, 10, 106),  # changed later
        _event(SEAT2, 1, 3, 107),
        _event(SEAT1, 0, 10, 108),
        _event(SEAT3, 2, 9, session_id=2),  # another session
    ]


def _run(session=None, ballots=None, final=None, events=None, votes=None, now=135):
    return tally.tally_session(
        1,
        _session() if session is None else session,
        _ballots() if ballots is None else ballots,
        _final() if final is None else final,
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
            "max_votes": 5 * MICRO,
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
                "max_votes": 3,
                "round": 106,
                "txid": "A",
            },
            {
                "session_id": 1,
                "seat": SEAT3,
                "option": 2,
                "max_votes": 5,
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
        "totals,abstain,quorum,threshold,expected",
        [
            ([7, 3, 0], 2, 0, (1, 2), (2, 0, 10)),
            ([5, 5, 0], 2, 0, (1, 2), (3, NOT_VOTED, 10)),  # a tie is not above half
            ([0, 0, 0], 2, 0, (1, 2), (3, NOT_VOTED, 0)),
            ([7, 3, 0], 2, 11, (1, 2), (3, NOT_VOTED, 10)),  # below the quorum
            ([6, 4], NOT_VOTED, 0, (2, 3), (3, NOT_VOTED, 10)),  # not above 2/3
            ([0, 7, 7], NOT_VOTED, 0, (1, 2), (3, NOT_VOTED, 14)),
            ([0, 7, 7, 1], 3, 0, (1, 3), (2, 1, 15)),  # a tie goes to the lower one
            ([3, 2, 90], 2, 95, (1, 2), (2, 0, 95)),  # abstain can't win, adds turnout
            ([3, 2, 90], NOT_VOTED, 0, (1, 2), (2, 2, 95)),
        ],
    )
    def test_tally_contract_result(self, totals, abstain, quorum, threshold, expected):
        assert tally.contract_result(totals, abstain, quorum, *threshold) == expected


class TestTallySession:
    """Testing class for :py:func:`dapp.tally.tally_session`."""

    def test_tally_session_everything_agrees(self):
        report = _run()
        assert report == {
            "problems": [],
            "notes": ["Not finalized yet; finalize would record passed"],
            "totals": [4 * MICRO, 3 * MICRO, 0],
            "turnout": 7 * MICRO,
            "voters": 2,
            "status": 2,
            "winner": 0,
        }

    def test_tally_session_for_missing_session(self):
        assert tally.tally_session(1, None, {}, None, [], {}, 1) == {
            "problems": ["There is no such session"],
            "notes": [],
        }

    def test_tally_session_checks_the_proposal(self):
        assert _run(final=_final(proposal_hash="cc" * 32))["problems"] == [
            "The proposal file's sha256 differs from the session's"
        ]
        assert _run(final=_final(proposal_hash="bb" * 32))["problems"] == []

    def test_tally_session_while_voting(self):
        ballots = {
            seat: {**box, "weighed": False, "eligible": 0, "weight": 0}
            for seat, box in _ballots().items()
        }
        session = _session(status=0, weighed_count=0, totals=[0, 0, 0], final_hash="00")
        report = _run(session, ballots, {"proposal_hash": None}, now=110)
        assert report["problems"] == []
        assert report["notes"] == [
            "Voting runs from round 105 to 120; the latest round is 110"
        ]

    def test_tally_session_after_the_end(self):
        ballots = _ballots()
        ballots[SEAT2].update({"weighed": False, "eligible": 0, "weight": 0})
        session = _session(status=0, weighed_count=1, totals=[4 * MICRO, 0, 0])
        report = _run(session, ballots, now=125)
        assert report["problems"] == []
        assert report["notes"] == [
            f"{SEAT2}: final amount not written yet",
            "Voting has ended; 1 of 2 voters weighed, sealing from round 130",
        ]

    def test_tally_session_without_final_file_once_sealed(self):
        report = _run(final={"proposal_hash": None})
        assert report["problems"] == []
        assert report["notes"][0] == (
            "No final file given, so the weights were not checked against it"
        )

    def test_tally_session_checks_the_events(self):
        events = _events() + [
            _event(SEAT2, 1, 3, 121, txid="LATE"),
            _event(SEAT2, 5, 3, 110, txid="BAD"),
        ]
        problems = _run(events=events)["problems"]
        assert problems[:2] == [
            f"{SEAT2}: vote in round 121, outside",
            f"{SEAT2}: vote for unknown option 5",
        ]

    def test_tally_session_matches_events_and_boxes(self):
        ballots = _ballots()
        ballots[SEAT3] = _ballot(2, 1, weighed=False)
        events = [_event(SEAT1, 0, 9), _event("GHOST", 1, 1, txid="G")]
        session = _session(voters_count=3)
        problems = _run(session, ballots, events=events)["problems"]
        assert problems[:4] == [
            "GHOST: voted in G, no ballot box",
            f"{SEAT1}: last event option 0 votes 9, box option 0 votes 10",
            f"{SEAT3}: ballot box, no Voted event",
            f"{SEAT2}: ballot box, no Voted event",
        ]

    def test_tally_session_compares_counts_and_totals(self):
        session = _session(voters_count=3, weighed_count=1, totals=[1, 2, 3])
        assert _run(session)["problems"] == [
            "Voters recomputed 2, session 3",
            "Weighed recomputed 2, session 1",
            f"Totals recomputed {[4 * MICRO, 3 * MICRO, 0]}, session [1, 2, 3]",
        ]

    def test_tally_session_checks_the_weight_rule(self):
        ballots = _ballots()
        ballots[SEAT2]["weight"] = 9 * MICRO
        ballots[SEAT3] = _ballot(7, 1, 1, 1)  # option beyond the options: not counted
        events = _events() + [_event(SEAT3, 7, 1)]
        problems = _run(
            _session(voters_count=3, weighed_count=3, totals=[4 * MICRO, 9 * MICRO, 0]),
            ballots,
            events=events,
        )["problems"]
        assert f"{SEAT2}: weight {9 * MICRO}, the rule gives {3 * MICRO}" in problems

    def test_tally_session_compares_boxes_with_the_final_file(self):
        final = _final(
            file_rounds=(500, 899),
            hash="cc" * 32,
            amounts={SEAT1: 5 * MICRO},
        )
        assert _run(final=final)["problems"] == [
            "Final file covers rounds (500, 899), the session's holdings rounds are "
            "(500, 900)",
            "The final file's sha256 differs from the sealed hash",
            f"{SEAT1}: box amount {4 * MICRO}, file {5 * MICRO}",
            f"{SEAT2}: voted, not in the final file",
        ]

    def test_tally_session_notes_votes_changed_since_the_vote(self):
        notes = _run(votes={SEAT1: 10})["notes"]
        assert f"{SEAT2}: votes_of was 3 at its vote, 0 now" in notes

    @pytest.mark.parametrize("status,winner,agrees", [(2, 0, True), (3, 255, False)])
    def test_tally_session_compares_a_recorded_result(self, status, winner, agrees):
        problems = _run(_session(status=status, winner=winner))["problems"]
        assert problems == (
            []
            if agrees
            else ["Result recomputed passed winner 0, session failed winner 255"]
        )


class TestTallyCommand:
    """Testing class for the command line tally."""

    def _files(self, tmp_path):
        final_file = tmp_path / "final.json"
        final_file.write_text(
            json.dumps(
                {
                    "start_round": 500,
                    "end_round": 900,
                    "seats": {SEAT1: {"total": 4 * MICRO}},
                }
            )
        )
        proposal = tmp_path / "proposal.md"
        proposal.write_text("Proposal")
        return final_file, proposal

    def test_tally_reads_the_chain_and_prints(self, mocker, tmp_path, capsys):
        final_file, proposal = self._files(tmp_path)
        algod, indexer, holdings = (mocker.MagicMock() for _ in range(3))
        algod.status.return_value = {"last-round": 135}
        holdings.health.return_value = {"round": 950}
        session = _session()
        mocker.patch("dapp.tally.session_values", return_value=session)
        mocker.patch("dapp.tally.session_ballots", return_value=_ballots())
        mocked_events = mocker.patch("dapp.tally.voted_events", return_value=["e"])
        mocked_votes = mocker.patch("dapp.tally.permission_votes", side_effect=[10, 3])
        mocked_rounds = mocker.patch(
            "dapp.tally.holdings_rounds", return_value=(500, 900)
        )
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
            "testnet",
            1,
            str(final_file),
            str(proposal),
            (algod, indexer, holdings, 5, 9),
        )
        assert report == mocked_tally.return_value
        mocked_events.assert_called_once_with(indexer, 5, 105, 120)
        mocked_rounds.assert_called_once_with(session, indexer, holdings, 950, False)
        assert mocked_votes.call_args_list == [
            mocker.call(algod, 9, SEAT1),
            mocker.call(algod, 9, SEAT2),
        ]
        args = mocked_tally.call_args.args
        assert args[0] == 1 and args[1] == session and args[4] == ["e"]
        assert args[2] == _ballots()
        assert args[3] == {
            "proposal_hash": tally.sha256_of_file(proposal).hex(),
            "file_rounds": (500, 900),
            "rounds": (500, 900),
            "hash": tally.sha256_of_file(final_file).hex(),
            "amounts": {SEAT1: 4 * MICRO},
        }
        assert args[5] == {SEAT1: 10, SEAT2: 3} and args[6] == 135
        assert capsys.readouterr().out == (
            "Session 1\nTotals [1], turnout 1, voters 1\nNOTE n\nEverything agrees\n"
        )

    def test_tally_without_files(self, mocker, capsys):
        algod = mocker.MagicMock()
        algod.status.return_value = {"last-round": 110}
        mocker.patch("dapp.tally.session_values", return_value=_session(status=0))
        mocker.patch("dapp.tally.session_ballots", return_value={})
        mocker.patch("dapp.tally.voted_events", return_value=[])
        mocked_tally = mocker.patch(
            "dapp.tally.tally_session", return_value={"problems": [], "notes": []}
        )
        tally.tally("mainnet", 1, clients=(algod, None, None, 5, 9))
        assert mocked_tally.call_args.args[3] == {"proposal_hash": None}

    def test_tally_for_missing_session_uses_configured_clients(self, mocker, capsys):
        mocker.patch("dapp.tally.environment_variables", return_value={})
        mocked_algod = mocker.patch("dapp.tally.algod_client")
        mocked_algod.return_value.status.return_value = {"last-round": 1}
        mocked_indexer = mocker.patch("dapp.tally.indexer_client")
        mocker.patch("dapp.tally.voting_dapp_id", return_value=5)
        mocker.patch("dapp.tally.permission_dapp_id", return_value=9)
        mocked_values = mocker.patch("dapp.tally.session_values", return_value=None)
        mocked_ballots = mocker.patch("dapp.tally.session_ballots")
        report = tally.tally("testnet", 3)
        assert report["problems"] == ["There is no such session"]
        mocked_values.assert_called_once_with(mocked_algod.return_value, 5, 3)
        assert mocked_indexer.call_args_list == [
            mocker.call({}, "testnet"),
            mocker.call({}, "mainnet"),
        ]
        mocked_ballots.assert_not_called()
        assert capsys.readouterr().out == (
            "Session 3\nPROBLEM There is no such session\n1 problems\n"
        )

    def test_tally_main_returns_the_exit_status(self, mocker):
        mocked = mocker.patch("dapp.tally.tally", return_value={"problems": []})
        assert (
            tally.main(["testnet", "2", "--final", "f.json", "--proposal", "p.md"]) == 0
        )
        mocked.assert_called_once_with(
            network="testnet", session_id=2, final="f.json", proposal="p.md"
        )
        mocked.return_value = {"problems": ["x"]}
        assert tally.main(["mainnet", "2"]) == 1
