"""Testing module for :py:mod:`dapp.contract` module."""

import algopy
import pytest
from algopy import UInt64, arc4
from algopy_testing import algopy_testing_context

from dapp.contract import (
    MICRO,
    NOT_VOTED,
    STATUS_FAILED,
    STATUS_OPEN,
    STATUS_PASSED,
    STATUS_SEALED,
    Ballot,
    Hash,
    Session,
    VotingDApp,
)

PROPOSAL_HASH = b"\x01" * 32
FINAL_HASH = b"\x02" * 32
START_ROUND, END_ROUND, SEAL_ROUND = 20, 30, 40


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
        "start_round": START_ROUND,
        "end_round": END_ROUND,
        "seal_round": SEAL_ROUND,
        "options_count": 3,
        "abstain": 2,
        "quorum": 0,
        "threshold_num": 1,
        "threshold_den": 2,
    }
    arguments.update(overrides)
    return contract.create_session(
        *(UInt64(value) for value in arguments.values()),
        Hash.from_bytes(PROPOSAL_HASH),
    )


def _set_final(contract, session_id, amounts):
    contract.set_final(
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


def _totals(context, contract, session_id=1):
    return [
        total.as_uint64() for total in _session(context, contract, session_id).totals
    ]


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
def seats(context, contract, mocker):
    """Three seats with mocked max votes 10, 3 and 100."""
    accounts = [context.any.account() for _ in range(3)]
    max_votes = {accounts[0]: 10, accounts[1]: 3, accounts[2]: 100}
    mocker.patch.object(
        contract, "_max_votes", side_effect=lambda seat: UInt64(max_votes.get(seat, 0))
    )
    return accounts


@pytest.fixture()
def voting(context, contract, seats):
    """A session at its start round."""
    session_id = _create_session(contract)
    context.ledger.patch_global_fields(round=START_ROUND)
    return session_id


@pytest.fixture()
def ended(context, contract, seats, voting):
    """A session past its end: seats[0] voted 0, seats[1] voted 1."""
    for seat, option in ((seats[0], 0), (seats[1], 1)):
        with _as(context, seat):
            contract.vote(voting, UInt64(option))
    context.ledger.patch_global_fields(round=END_ROUND + 1)
    return voting


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
        assert session.start_round == START_ROUND
        assert session.end_round == END_ROUND
        assert session.seal_round == SEAL_ROUND
        assert session.options_count == 3
        assert session.abstain == 2
        assert session.quorum == 9
        assert session.threshold_num == 2
        assert session.threshold_den == 3
        assert session.proposal_hash.bytes == PROPOSAL_HASH
        assert session.final_hash.bytes == bytes(32)
        assert session.status == STATUS_OPEN
        assert session.winner == NOT_VOTED
        assert session.voters_count == 0
        assert session.weighed_count == 0
        assert session.totals.bytes == bytes(64)
        assert len(session.bytes) == 196

    def test_voting_dapp_create_session_increments_identifier(self, contract):
        assert _create_session(contract) == 1
        assert _create_session(contract) == 2

    def test_voting_dapp_create_session_without_abstain(self, context, contract):
        _create_session(contract, options_count=2, abstain=NOT_VOTED)
        assert _session(context, contract).abstain == NOT_VOTED

    def test_voting_dapp_create_session_fails_for_non_admin(self, context, contract):
        with pytest.raises(AssertionError, match="Only admin can create sessions"):
            with _as(context, context.any.account()):
                _create_session(contract)

    @pytest.mark.parametrize(
        "overrides,message",
        [
            ({"start_round": 10}, "Start round must be in the future"),
            ({"end_round": START_ROUND}, "End must follow the start"),
            ({"seal_round": END_ROUND}, "Seal round must follow the end"),
            ({"options_count": 9}, "At most eight options are allowed"),
            ({"abstain": 3}, "Abstain must be one of the options"),
            ({"options_count": 2, "abstain": 1}, "At least two options besides"),
            ({"options_count": 1, "abstain": NOT_VOTED}, "At least two options"),
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


class TestVotingDAppVote:
    """Testing class for :py:meth:`dapp.contract.VotingDApp.vote`."""

    def test_voting_dapp_vote_creates_ballot_without_weight(
        self, context, contract, seats, voting
    ):
        with _as(context, seats[0]):
            contract.vote(voting, UInt64(1))

        ballot = _ballot(context, contract, seats[0])
        assert ballot.option == 1
        assert ballot.max_votes == 10
        assert ballot.eligible == 0
        assert ballot.weight == 0
        assert not ballot.weighed.native
        assert len(ballot.bytes) == 26
        session = _session(context, contract)
        assert session.voters_count == 1
        assert _totals(context, contract) == [0] * 8

    def test_voting_dapp_vote_logs_event(self, context, contract, seats, voting):
        with _as(context, seats[0]):
            contract.vote(voting, UInt64(1))

        log = context.txn.last_active.logs(0)
        assert log[:4] == arc4.arc4_signature("Voted(uint64,address,uint8,uint64)")
        assert log[4:] == (
            (1).to_bytes(8, "big")
            + seats[0].bytes.value
            + b"\x01"
            + (10).to_bytes(8, "big")
        )

    def test_voting_dapp_vote_again_changes_option_not_voters(
        self, context, contract, seats, voting
    ):
        with _as(context, seats[0]):
            contract.vote(voting, UInt64(1))
        with _as(context, seats[0]):
            contract.vote(voting, UInt64(0))
        with _as(context, seats[1]):
            contract.vote(voting, UInt64(2))

        assert _ballot(context, contract, seats[0]).option == 0
        assert _session(context, contract).voters_count == 2

    def test_voting_dapp_vote_in_last_round(self, context, contract, seats, voting):
        context.ledger.patch_global_fields(round=END_ROUND)
        with _as(context, seats[2]):
            contract.vote(voting, UInt64(2))
        assert _ballot(context, contract, seats[2]).option == 2

    def test_voting_dapp_vote_fails_for_missing_session(self, contract, voting):
        with pytest.raises(AssertionError, match="Session does not exist"):
            contract.vote(UInt64(9), UInt64(0))

    def test_voting_dapp_vote_fails_before_start(self, context, contract, seats):
        session_id = _create_session(contract)
        with pytest.raises(AssertionError, match="Voting has not started"):
            with _as(context, seats[0]):
                contract.vote(session_id, UInt64(0))

    def test_voting_dapp_vote_fails_after_end(self, context, contract, seats, voting):
        context.ledger.patch_global_fields(round=END_ROUND + 1)
        with pytest.raises(AssertionError, match="Voting has ended"):
            with _as(context, seats[0]):
                contract.vote(voting, UInt64(0))

    def test_voting_dapp_vote_fails_when_sealed(self, context, contract, seats, ended):
        context.ledger.patch_global_fields(round=SEAL_ROUND)
        _set_final(contract, ended, {seats[0]: 1, seats[1]: 1})
        contract.seal_final(ended, Hash.from_bytes(FINAL_HASH))
        with pytest.raises(AssertionError, match="Session is not open for voting"):
            with _as(context, seats[0]):
                contract.vote(ended, UInt64(0))

    def test_voting_dapp_vote_fails_for_unknown_option(
        self, context, contract, seats, voting
    ):
        with pytest.raises(AssertionError, match="Unknown option"):
            with _as(context, seats[0]):
                contract.vote(voting, UInt64(3))

    def test_voting_dapp_vote_fails_for_seat_without_votes(
        self, context, contract, seats, voting
    ):
        with pytest.raises(AssertionError, match="Seat has no votes"):
            with _as(context, context.any.account()):
                contract.vote(voting, UInt64(0))


class TestVotingDAppSetFinal:
    """Testing class for :py:meth:`dapp.contract.VotingDApp.set_final`."""

    def test_voting_dapp_set_final_weighs_voters(self, context, contract, seats, ended):
        _set_final(contract, ended, {seats[0]: 4 * MICRO, seats[1]: 5 * MICRO + 7})

        first = _ballot(context, contract, seats[0])
        assert (first.eligible, first.weight, first.weighed.native) == (
            4 * MICRO,
            4 * MICRO,  # capped by the lowest amount
            True,
        )
        second = _ballot(context, contract, seats[1])
        assert (second.eligible, second.weight) == (5 * MICRO + 7, 3 * MICRO)
        assert _totals(context, contract)[:3] == [4 * MICRO, 3 * MICRO, 0]
        assert _session(context, contract).weighed_count == 2

    def test_voting_dapp_set_final_corrects_a_weight(
        self, context, contract, seats, ended
    ):
        _set_final(contract, ended, {seats[0]: 4 * MICRO})
        _set_final(contract, ended, {seats[0]: 2 * MICRO, seats[1]: 0})

        assert _ballot(context, contract, seats[0]).weight == 2 * MICRO
        assert _ballot(context, contract, seats[1]).weighed.native
        assert _totals(context, contract)[:3] == [2 * MICRO, 0, 0]
        assert _session(context, contract).weighed_count == 2

    def test_voting_dapp_set_final_fails_for_non_admin(
        self, context, contract, seats, ended
    ):
        with pytest.raises(AssertionError, match="Only admin can write final"):
            with _as(context, context.any.account()):
                _set_final(contract, ended, {seats[0]: 1})

    def test_voting_dapp_set_final_fails_before_end(
        self, context, contract, seats, voting
    ):
        with pytest.raises(AssertionError, match="Voting has not ended"):
            _set_final(contract, voting, {seats[0]: 1})

    def test_voting_dapp_set_final_fails_for_different_lengths(
        self, contract, seats, ended
    ):
        with pytest.raises(AssertionError, match="Seats and amounts differ"):
            contract.set_final(
                ended,
                arc4.DynamicArray[arc4.Address](arc4.Address(seats[0])),
                arc4.DynamicArray[arc4.UInt64](),
            )

    def test_voting_dapp_set_final_fails_for_seat_without_vote(
        self, contract, seats, ended
    ):
        with pytest.raises(AssertionError, match="Seat has not voted"):
            _set_final(contract, ended, {seats[2]: 1})

    def test_voting_dapp_set_final_fails_after_seal(
        self, context, contract, seats, ended
    ):
        _set_final(contract, ended, {seats[0]: 1, seats[1]: 1})
        context.ledger.patch_global_fields(round=SEAL_ROUND)
        contract.seal_final(ended, Hash.from_bytes(FINAL_HASH))
        with pytest.raises(AssertionError, match="Final amounts are sealed"):
            _set_final(contract, ended, {seats[0]: 2})


class TestVotingDAppSealFinal:
    """Testing class for :py:meth:`dapp.contract.VotingDApp.seal_final`."""

    def test_voting_dapp_seal_final_stores_hash_and_status(
        self, context, contract, seats, ended
    ):
        _set_final(contract, ended, {seats[0]: 1, seats[1]: 1})
        context.ledger.patch_global_fields(round=SEAL_ROUND)
        contract.seal_final(ended, Hash.from_bytes(FINAL_HASH))

        session = _session(context, contract)
        assert session.final_hash.bytes == FINAL_HASH
        assert session.status == STATUS_SEALED

    def test_voting_dapp_seal_final_without_voters(self, context, contract, voting):
        context.ledger.patch_global_fields(round=SEAL_ROUND)
        contract.seal_final(voting, Hash.from_bytes(FINAL_HASH))
        assert _session(context, contract).status == STATUS_SEALED

    def test_voting_dapp_seal_final_fails_for_non_admin(self, context, contract, ended):
        context.ledger.patch_global_fields(round=SEAL_ROUND)
        with pytest.raises(AssertionError, match="Only admin can seal final"):
            with _as(context, context.any.account()):
                contract.seal_final(ended, Hash.from_bytes(FINAL_HASH))

    def test_voting_dapp_seal_final_fails_before_seal_round(
        self, context, contract, seats, ended
    ):
        _set_final(contract, ended, {seats[0]: 1, seats[1]: 1})
        context.ledger.patch_global_fields(round=SEAL_ROUND - 1)
        with pytest.raises(AssertionError, match="Seal round not reached"):
            contract.seal_final(ended, Hash.from_bytes(FINAL_HASH))

    def test_voting_dapp_seal_final_fails_with_unweighed_voter(
        self, context, contract, seats, ended
    ):
        _set_final(contract, ended, {seats[0]: 1})
        context.ledger.patch_global_fields(round=SEAL_ROUND)
        with pytest.raises(AssertionError, match="Every voter needs a final amount"):
            contract.seal_final(ended, Hash.from_bytes(FINAL_HASH))

    def test_voting_dapp_seal_final_fails_when_sealed(self, context, contract, voting):
        context.ledger.patch_global_fields(round=SEAL_ROUND)
        contract.seal_final(voting, Hash.from_bytes(FINAL_HASH))
        with pytest.raises(AssertionError, match="Final amounts are sealed"):
            contract.seal_final(voting, Hash.from_bytes(FINAL_HASH))


class TestVotingDAppFinalize:
    """Testing class for :py:meth:`dapp.contract.VotingDApp.finalize`."""

    def _sealed(self, context, contract, mocker, votes, **overrides):
        """Return a sealed session where `votes` are (option, weight) pairs."""
        mocker.patch.object(contract, "_max_votes", return_value=UInt64(10**9))
        session_id = _create_session(contract, **overrides)
        seats = [context.any.account() for _ in votes]
        context.ledger.patch_global_fields(round=START_ROUND)
        for seat, (option, _) in zip(seats, votes):
            with _as(context, seat):
                contract.vote(session_id, UInt64(option))

        context.ledger.patch_global_fields(round=SEAL_ROUND)
        if votes:
            _set_final(
                contract,
                session_id,
                {seat: weight for seat, (_, weight) in zip(seats, votes)},
            )
        contract.seal_final(session_id, Hash.from_bytes(FINAL_HASH))
        return session_id

    def _finalize(self, context, contract, session_id):
        with _as(context, context.any.account()):
            contract.finalize(session_id)
        return _session(context, contract, session_id)

    def test_voting_dapp_finalize_passes_with_majority(self, context, contract, mocker):
        session_id = self._sealed(context, contract, mocker, [(0, 40), (1, 51), (2, 9)])

        session = self._finalize(context, contract, session_id)

        assert session.status == STATUS_PASSED
        assert session.winner == 1
        log = context.txn.last_active.logs(0)
        assert log[:4] == arc4.arc4_signature("Finalized(uint64,uint8,uint8,uint64)")
        assert log[4:] == (
            (1).to_bytes(8, "big") + b"\x02\x01" + (100).to_bytes(8, "big")
        )

    def test_voting_dapp_finalize_abstain_cannot_win(self, context, contract, mocker):
        session_id = self._sealed(
            context, contract, mocker, [(0, 30), (1, 20), (2, 90)]
        )
        session = self._finalize(context, contract, session_id)
        assert (session.status, session.winner) == (STATUS_PASSED, 0)

    def test_voting_dapp_finalize_abstain_counts_toward_quorum(
        self, context, contract, mocker
    ):
        session_id = self._sealed(
            context, contract, mocker, [(0, 30), (1, 20), (2, 50)], quorum=100
        )
        session = self._finalize(context, contract, session_id)
        assert (session.status, session.winner) == (STATUS_PASSED, 0)

    def test_voting_dapp_finalize_without_abstain_counts_every_option(
        self, context, contract, mocker
    ):
        session_id = self._sealed(
            context, contract, mocker, [(0, 30), (1, 20), (2, 40)], abstain=NOT_VOTED
        )
        session = self._finalize(context, contract, session_id)
        assert (session.status, session.winner) == (STATUS_FAILED, NOT_VOTED)

    def test_voting_dapp_finalize_fails_at_exact_threshold(
        self, context, contract, mocker
    ):
        session_id = self._sealed(context, contract, mocker, [(0, 50), (1, 50), (2, 7)])
        session = self._finalize(context, contract, session_id)
        assert (session.status, session.winner) == (STATUS_FAILED, NOT_VOTED)

    @pytest.mark.parametrize("first,status", [(66, STATUS_FAILED), (67, STATUS_PASSED)])
    def test_voting_dapp_finalize_two_thirds(
        self, context, contract, mocker, first, status
    ):
        session_id = self._sealed(
            context,
            contract,
            mocker,
            [(0, first), (1, 100 - first)],
            threshold_num=2,
            threshold_den=3,
        )
        assert self._finalize(context, contract, session_id).status == status

    def test_voting_dapp_finalize_fails_below_quorum(self, context, contract, mocker):
        session_id = self._sealed(
            context, contract, mocker, [(0, 60), (1, 30)], quorum=91
        )
        session = self._finalize(context, contract, session_id)
        assert (session.status, session.winner) == (STATUS_FAILED, NOT_VOTED)

    def test_voting_dapp_finalize_fails_without_votes(self, context, contract, mocker):
        session_id = self._sealed(context, contract, mocker, [])
        assert self._finalize(context, contract, session_id).status == STATUS_FAILED

    def test_voting_dapp_finalize_fails_with_zero_weights(
        self, context, contract, mocker
    ):
        session_id = self._sealed(context, contract, mocker, [(0, 0), (2, 0)])
        assert self._finalize(context, contract, session_id).status == STATUS_FAILED

    def test_voting_dapp_finalize_fails_before_seal(self, context, contract, ended):
        with pytest.raises(AssertionError, match="Final amounts are not sealed"):
            contract.finalize(ended)

    def test_voting_dapp_finalize_fails_when_finalized(self, context, contract, mocker):
        session_id = self._sealed(context, contract, mocker, [(0, 1)])
        self._finalize(context, contract, session_id)
        with pytest.raises(AssertionError, match="Final amounts are not sealed"):
            contract.finalize(session_id)

    def test_voting_dapp_finalize_fails_for_missing_session(self, contract):
        with pytest.raises(AssertionError, match="Session does not exist"):
            contract.finalize(UInt64(9))
