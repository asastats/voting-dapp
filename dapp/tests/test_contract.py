"""Testing module for :py:mod:`dapp.contract` module."""

import algopy
import pytest
from algopy import UInt64, arc4
from algopy_testing import algopy_testing_context

from dapp.contract import (
    MICRO,
    NOT_VOTED,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_SEALED,
    STATUS_SNAPSHOT_OPEN,
    Ballot,
    Hash,
    Session,
    VotingDApp,
)

PROPOSAL_HASH = b"\x01" * 32
SNAPSHOT_HASH = b"\x02" * 32
SNAPSHOT_ROUND, START_ROUND, END_ROUND = 5, 20, 30


@pytest.fixture()
def context():
    with algopy_testing_context() as ctx:
        ctx.ledger.patch_global_fields(round=10)
        yield ctx


@pytest.fixture()
def contract(context):
    contract = VotingDApp()
    contract.create_application(UInt64(77))
    return contract


def _create_session(contract, **overrides):
    arguments = {
        "snapshot_round": SNAPSHOT_ROUND,
        "start_round": START_ROUND,
        "end_round": END_ROUND,
        "options_count": 3,
        "quorum": 0,
        "threshold_num": 1,
        "threshold_den": 2,
    }
    arguments.update(overrides)
    return contract.create_session(
        *(UInt64(value) for value in arguments.values()),
        Hash.from_bytes(PROPOSAL_HASH),
    )


def _set_snapshot(contract, session_id, amounts):
    contract.set_snapshot(
        session_id,
        arc4.DynamicArray[arc4.Address](*(arc4.Address(seat) for seat in amounts)),
        arc4.DynamicArray[arc4.UInt64](
            *(arc4.UInt64(amount) for amount in amounts.values())
        ),
    )


def _session(context, contract, session_id=1):
    app = context.ledger.get_app(contract)
    return Session.from_bytes(
        context.ledger.get_box(app.id, b"s" + int(session_id).to_bytes(8, "big"))
    )


def _ballot_box(context, contract, seat, session_id=1):
    app = context.ledger.get_app(contract)
    return context.ledger.get_box(
        app.id, b"v" + int(session_id).to_bytes(8, "big") + seat.bytes.value
    )


def _ballot(context, contract, seat, session_id=1):
    return Ballot.from_bytes(_ballot_box(context, contract, seat, session_id))


def _as(context, account, **overrides):
    return context.txn.create_group(
        active_txn_overrides={"sender": account, **overrides}
    )


@pytest.fixture()
def open_session(context, contract, mocker):
    """Sealed session at a voting round with three seats and mocked max votes."""
    seats = [context.any.account() for _ in range(3)]
    max_votes = {seats[0]: 10, seats[1]: 3, seats[2]: 100}
    mocker.patch.object(
        contract, "_max_votes", side_effect=lambda seat: UInt64(max_votes.get(seat, 0))
    )
    session_id = _create_session(contract)
    _set_snapshot(
        contract,
        session_id,
        {seats[0]: 4 * MICRO, seats[1]: 5 * MICRO + 7, seats[2]: 6 * MICRO},
    )
    contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))
    context.ledger.patch_global_fields(round=START_ROUND)
    return session_id, seats, max_votes


class TestVotingDAppLifecycle:
    """Testing class for the application's lifecycle methods."""

    def test_voting_dapp_create_application_sets_global_state(self, context, contract):
        assert contract.permission_app_id == 77
        assert contract.admin == context.default_sender
        assert contract.session_count == 0

    def test_voting_dapp_create_application_requires_permission_app(self, context):
        with pytest.raises(AssertionError, match="Permission dApp ID is required"):
            VotingDApp().create_application(UInt64(0))

    @pytest.mark.parametrize(
        "method,action",
        [
            ("update_application", algopy.OnCompleteAction.UpdateApplication),
            ("delete_application", algopy.OnCompleteAction.DeleteApplication),
        ],
    )
    def test_voting_dapp_update_and_delete_by_admin_only(
        self, context, contract, method, action
    ):
        with _as(context, context.default_sender, on_completion=action):
            getattr(contract, method)()

        with pytest.raises(AssertionError, match="Only admin can"):
            with _as(context, context.any.account(), on_completion=action):
                getattr(contract, method)()


class TestVotingDAppCreateSession:
    """Testing class for :py:meth:`dapp.contract.VotingDApp.create_session`."""

    def test_voting_dapp_create_session_writes_session_box(self, context, contract):
        returned = _create_session(contract, quorum=9, threshold_num=2, threshold_den=3)

        assert returned == 1
        assert contract.session_count == 1
        session = _session(context, contract)
        assert session.snapshot_round == SNAPSHOT_ROUND
        assert session.snapshot_hash.bytes == bytes(32)
        assert session.start_round == START_ROUND
        assert session.end_round == END_ROUND
        assert session.options_count == 3
        assert session.quorum == 9
        assert session.threshold_num == 2
        assert session.threshold_den == 3
        assert session.proposal_hash.bytes == PROPOSAL_HASH
        assert session.status == STATUS_SNAPSHOT_OPEN
        assert session.winner == NOT_VOTED
        assert session.voters_count == 0
        assert session.totals.bytes == bytes(64)
        assert len(session.bytes) == 187

    def test_voting_dapp_create_session_increments_identifier(self, contract):
        assert _create_session(contract) == 1
        assert _create_session(contract) == 2

    def test_voting_dapp_create_session_fails_for_non_admin(self, context, contract):
        with pytest.raises(AssertionError, match="Only admin can create sessions"):
            with _as(context, context.any.account()):
                _create_session(contract)

    @pytest.mark.parametrize(
        "overrides,message",
        [
            ({"snapshot_round": START_ROUND}, "Snapshot must precede the start"),
            ({"snapshot_round": 1, "start_round": 10}, "Start round must be in the"),
            ({"end_round": START_ROUND}, "End must follow the start"),
            ({"options_count": 1}, "At least two options are required"),
            ({"options_count": 9}, "At most eight options are allowed"),
            ({"threshold_den": 0}, "Threshold denominator is required"),
            ({"threshold_den": 1001}, "Threshold denominator too big"),
            ({"threshold_num": 2}, "Threshold must be below one"),
            ({"threshold_num": 1, "threshold_den": 3}, "Threshold must be at least"),
        ],
    )
    def test_voting_dapp_create_session_fails_for_wrong_arguments(
        self, contract, overrides, message
    ):
        with pytest.raises(AssertionError, match=message):
            _create_session(contract, **overrides)


class TestVotingDAppSnapshot:
    """Testing class for the snapshot writing and sealing methods."""

    def test_voting_dapp_set_snapshot_writes_ballot_boxes(self, context, contract):
        seat1, seat2 = context.any.account(), context.any.account()
        session_id = _create_session(contract)

        _set_snapshot(contract, session_id, {seat1: 11, seat2: 22})

        ballot = _ballot(context, contract, seat1)
        assert ballot.eligible == 11
        assert ballot.option == NOT_VOTED
        assert ballot.max_votes == 0
        assert ballot.weight == 0
        assert len(ballot.bytes) == 25
        assert _ballot(context, contract, seat2).eligible == 22

    def test_voting_dapp_set_snapshot_corrects_and_removes_seats(
        self, context, contract
    ):
        seat1, seat2, seat3 = (context.any.account() for _ in range(3))
        session_id = _create_session(contract)
        _set_snapshot(contract, session_id, {seat1: 11, seat2: 22})

        _set_snapshot(contract, session_id, {seat1: 33, seat2: 0, seat3: 0})

        assert _ballot(context, contract, seat1).eligible == 33
        assert not _ballot_box(context, contract, seat2)
        assert not _ballot_box(context, contract, seat3)

    def test_voting_dapp_set_snapshot_fails_for_non_admin(self, context, contract):
        session_id = _create_session(contract)
        with pytest.raises(AssertionError, match="Only admin can write the snapshot"):
            with _as(context, context.any.account()):
                _set_snapshot(contract, session_id, {})

    def test_voting_dapp_set_snapshot_fails_for_missing_session(self, contract):
        with pytest.raises(AssertionError, match="Session does not exist"):
            _set_snapshot(contract, UInt64(1), {})

    def test_voting_dapp_set_snapshot_fails_for_different_lengths(
        self, context, contract
    ):
        session_id = _create_session(contract)
        with pytest.raises(AssertionError, match="Seats and amounts differ"):
            contract.set_snapshot(
                session_id,
                arc4.DynamicArray(arc4.Address(context.any.account())),
                arc4.DynamicArray[arc4.UInt64](),
            )

    def test_voting_dapp_set_snapshot_fails_after_seal(self, context, contract):
        session_id = _create_session(contract)
        contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))

        with pytest.raises(AssertionError, match="Snapshot is sealed"):
            _set_snapshot(contract, session_id, {context.any.account(): 1})

    def test_voting_dapp_seal_snapshot_stores_hash_and_status(self, context, contract):
        session_id = _create_session(contract)

        contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))

        session = _session(context, contract)
        assert session.snapshot_hash.bytes == SNAPSHOT_HASH
        assert session.status == STATUS_SEALED

    def test_voting_dapp_seal_snapshot_fails_for_non_admin(self, context, contract):
        session_id = _create_session(contract)
        with pytest.raises(AssertionError, match="Only admin can seal the snapshot"):
            with _as(context, context.any.account()):
                contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))

    def test_voting_dapp_seal_snapshot_fails_when_sealed(self, contract):
        session_id = _create_session(contract)
        contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))

        with pytest.raises(AssertionError, match="Snapshot is sealed"):
            contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))

    def test_voting_dapp_seal_snapshot_fails_before_snapshot_round(self, contract):
        session_id = _create_session(contract, snapshot_round=11)
        with pytest.raises(AssertionError, match="Snapshot round not reached"):
            contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))

    def test_voting_dapp_seal_snapshot_fails_from_start_round(self, context, contract):
        session_id = _create_session(contract)
        context.ledger.patch_global_fields(round=START_ROUND)

        with pytest.raises(AssertionError, match="Session start has passed"):
            contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))


class TestVotingDAppVote:
    """Testing class for :py:meth:`dapp.contract.VotingDApp.vote`."""

    def test_voting_dapp_vote_weight_is_capped_by_eligible(
        self, context, contract, open_session
    ):
        session_id, seats, _ = open_session

        with _as(context, seats[0]):
            contract.vote(session_id, UInt64(1))

        ballot = _ballot(context, contract, seats[0])
        assert ballot.eligible == 4 * MICRO
        assert ballot.option == 1
        assert ballot.max_votes == 10
        assert ballot.weight == 4 * MICRO
        session = _session(context, contract)
        assert session.voters_count == 1
        assert [total.as_uint64() for total in session.totals] == [
            0,
            4 * MICRO,
            0,
            0,
            0,
            0,
            0,
            0,
        ]

    def test_voting_dapp_vote_weight_is_capped_by_max_votes(
        self, context, contract, open_session
    ):
        session_id, seats, _ = open_session

        with _as(context, seats[1]):
            contract.vote(session_id, UInt64(2))

        ballot = _ballot(context, contract, seats[1])
        assert ballot.max_votes == 3
        assert ballot.weight == 3 * MICRO
        assert _session(context, contract).totals[2] == 3 * MICRO

    def test_voting_dapp_vote_logs_event(self, context, contract, open_session):
        session_id, seats, _ = open_session

        with _as(context, seats[0]):
            contract.vote(session_id, UInt64(1))

        log = context.txn.last_active.logs(0)
        assert log[:4] == arc4.arc4_signature("Voted(uint64,address,uint8,uint64)")
        assert log[4:] == (
            (1).to_bytes(8, "big")
            + seats[0].bytes.value
            + b"\x01"
            + (4 * MICRO).to_bytes(8, "big")
        )

    def test_voting_dapp_vote_again_moves_the_weight(
        self, context, contract, open_session
    ):
        session_id, seats, max_votes = open_session
        with _as(context, seats[0]):
            contract.vote(session_id, UInt64(0))
        with _as(context, seats[2]):
            contract.vote(session_id, UInt64(0))
        max_votes[seats[0]] = 2

        with _as(context, seats[0]):
            contract.vote(session_id, UInt64(2))

        ballot = _ballot(context, contract, seats[0])
        assert ballot.option == 2
        assert ballot.max_votes == 2
        assert ballot.weight == 2 * MICRO
        session = _session(context, contract)
        assert session.voters_count == 2
        assert session.totals[0] == 6 * MICRO
        assert session.totals[2] == 2 * MICRO

    def test_voting_dapp_vote_in_last_round(self, context, contract, open_session):
        session_id, seats, _ = open_session
        context.ledger.patch_global_fields(round=END_ROUND)

        with _as(context, seats[0]):
            contract.vote(session_id, UInt64(0))

        assert _session(context, contract).voters_count == 1

    def test_voting_dapp_vote_fails_for_missing_session(self, contract, open_session):
        with pytest.raises(AssertionError, match="Session does not exist"):
            contract.vote(UInt64(2), UInt64(0))

    def test_voting_dapp_vote_fails_for_unsealed_session(self, context, contract):
        session_id = _create_session(contract)
        context.ledger.patch_global_fields(round=START_ROUND)

        with pytest.raises(AssertionError, match="Session is not open for voting"):
            contract.vote(session_id, UInt64(0))

    def test_voting_dapp_vote_fails_before_start(self, context, contract, open_session):
        session_id, seats, _ = open_session
        context.ledger.patch_global_fields(round=START_ROUND - 1)

        with pytest.raises(AssertionError, match="Voting has not started"):
            with _as(context, seats[0]):
                contract.vote(session_id, UInt64(0))

    def test_voting_dapp_vote_fails_after_end(self, context, contract, open_session):
        session_id, seats, _ = open_session
        context.ledger.patch_global_fields(round=END_ROUND + 1)

        with pytest.raises(AssertionError, match="Voting has ended"):
            with _as(context, seats[0]):
                contract.vote(session_id, UInt64(0))

    def test_voting_dapp_vote_fails_for_unknown_option(
        self, context, contract, open_session
    ):
        session_id, seats, _ = open_session
        with pytest.raises(AssertionError, match="Unknown option"):
            with _as(context, seats[0]):
                contract.vote(session_id, UInt64(3))

    def test_voting_dapp_vote_fails_for_seat_outside_snapshot(
        self, context, contract, open_session
    ):
        session_id, _, _ = open_session
        with pytest.raises(AssertionError, match="Seat is not in the snapshot"):
            with _as(context, context.any.account()):
                contract.vote(session_id, UInt64(0))

    def test_voting_dapp_vote_fails_for_seat_without_votes(
        self, context, contract, open_session
    ):
        session_id, seats, max_votes = open_session
        max_votes[seats[0]] = 0

        with pytest.raises(AssertionError, match="Seat has no votes"):
            with _as(context, seats[0]):
                contract.vote(session_id, UInt64(0))


class TestVotingDAppFinalize:
    """Testing class for :py:meth:`dapp.contract.VotingDApp.finalize`."""

    def _voted_session(self, context, contract, mocker, votes, **overrides):
        """Return a session past its end where `votes` are (option, weight) pairs."""
        mocker.patch.object(contract, "_max_votes", return_value=UInt64(10**9))
        session_id = _create_session(contract, **overrides)
        seats = [context.any.account() for _ in votes]
        _set_snapshot(
            contract,
            session_id,
            {seat: weight for seat, (_, weight) in zip(seats, votes)},
        )
        contract.seal_snapshot(session_id, Hash.from_bytes(SNAPSHOT_HASH))
        context.ledger.patch_global_fields(round=START_ROUND)
        for seat, (option, _) in zip(seats, votes):
            with _as(context, seat):
                contract.vote(session_id, UInt64(option))

        context.ledger.patch_global_fields(round=END_ROUND + 1)
        return session_id

    def test_voting_dapp_finalize_passes_with_majority(self, context, contract, mocker):
        session_id = self._voted_session(
            context, contract, mocker, [(0, 40), (1, 51), (2, 9)]
        )

        with _as(context, context.any.account()):
            contract.finalize(session_id)

        session = _session(context, contract)
        assert session.status == STATUS_PASSED
        assert session.winner == 1
        log = context.txn.last_active.logs(0)
        assert log[:4] == arc4.arc4_signature("Finalized(uint64,uint8,uint8,uint64)")
        assert log[4:] == (
            (1).to_bytes(8, "big") + b"\x02\x01" + (100).to_bytes(8, "big")
        )

    def test_voting_dapp_finalize_fails_at_exact_threshold(
        self, context, contract, mocker
    ):
        session_id = self._voted_session(context, contract, mocker, [(0, 50), (1, 50)])

        contract.finalize(session_id)

        session = _session(context, contract)
        assert session.status == STATUS_FAILED
        assert session.winner == NOT_VOTED

    def test_voting_dapp_finalize_fails_below_two_thirds(
        self, context, contract, mocker
    ):
        session_id = self._voted_session(
            context,
            contract,
            mocker,
            [(0, 66), (1, 34)],
            threshold_num=2,
            threshold_den=3,
        )

        contract.finalize(session_id)

        assert _session(context, contract).status == STATUS_FAILED

    def test_voting_dapp_finalize_passes_above_two_thirds(
        self, context, contract, mocker
    ):
        session_id = self._voted_session(
            context,
            contract,
            mocker,
            [(0, 67), (1, 33)],
            threshold_num=2,
            threshold_den=3,
        )

        contract.finalize(session_id)

        session = _session(context, contract)
        assert session.status == STATUS_PASSED
        assert session.winner == 0

    def test_voting_dapp_finalize_fails_below_quorum(self, context, contract, mocker):
        session_id = self._voted_session(
            context, contract, mocker, [(0, 90), (1, 9)], quorum=100
        )

        contract.finalize(session_id)

        session = _session(context, contract)
        assert session.status == STATUS_FAILED
        assert session.winner == NOT_VOTED

    def test_voting_dapp_finalize_fails_without_votes(self, context, contract, mocker):
        session_id = self._voted_session(context, contract, mocker, [])

        contract.finalize(session_id)

        assert _session(context, contract).status == STATUS_FAILED

    def test_voting_dapp_finalize_fails_before_end(self, context, contract, mocker):
        session_id = self._voted_session(context, contract, mocker, [(0, 1)])
        context.ledger.patch_global_fields(round=END_ROUND)

        with pytest.raises(AssertionError, match="Voting has not ended"):
            contract.finalize(session_id)

    def test_voting_dapp_finalize_fails_when_finalized(self, context, contract, mocker):
        session_id = self._voted_session(context, contract, mocker, [(0, 1)])
        contract.finalize(session_id)

        with pytest.raises(AssertionError, match="Session is not open for voting"):
            contract.finalize(session_id)

    def test_voting_dapp_finalize_fails_for_missing_session(self, contract):
        with pytest.raises(AssertionError, match="Session does not exist"):
            contract.finalize(UInt64(1))
