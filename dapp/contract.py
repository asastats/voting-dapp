"""Voting dApp smart contract implemented with Algorand Python (puyapy)."""

import typing

from algopy import (
    Account,
    ARC4Contract,
    BoxMap,
    Global,
    Txn,
    UInt64,
    arc4,
    op,
    subroutine,
    urange,
)

Hash: typing.TypeAlias = arc4.StaticArray[arc4.Byte, typing.Literal[32]]
Totals: typing.TypeAlias = arc4.StaticArray[arc4.UInt64, typing.Literal[8]]

MICRO = 1_000_000
MAX_OPTIONS = 8
MAX_THRESHOLD_DEN = 1_000
NOT_VOTED = 255

STATUS_OPEN = 0
STATUS_SEALED = 1
STATUS_PASSED = 2
STATUS_FAILED = 3


class Session(arc4.Struct):
    """Session box value; the website decodes it by these offsets."""

    start_round: arc4.UInt64
    end_round: arc4.UInt64
    seal_round: arc4.UInt64
    options_count: arc4.UInt8
    abstain: arc4.UInt8
    quorum: arc4.UInt64
    threshold_num: arc4.UInt64
    threshold_den: arc4.UInt64
    proposal_hash: Hash
    final_hash: Hash
    status: arc4.UInt8
    winner: arc4.UInt8
    voters_count: arc4.UInt64
    weighed_count: arc4.UInt64
    totals: Totals


class BallotKey(arc4.Struct):
    """Ballot box name after the ``v`` prefix."""

    session_id: arc4.UInt64
    seat: arc4.Address


class Ballot(arc4.Struct):
    """Ballot box value, created by the seat's first vote."""

    option: arc4.UInt8
    max_votes: arc4.UInt64
    eligible: arc4.UInt64
    weight: arc4.UInt64
    weighed: arc4.Bool


class Voted(arc4.Struct):
    """ARC-28 event logged by every vote."""

    session_id: arc4.UInt64
    seat: arc4.Address
    option: arc4.UInt8
    max_votes: arc4.UInt64


class Finalized(arc4.Struct):
    """ARC-28 event logged once per session by `finalize`."""

    session_id: arc4.UInt64
    status: arc4.UInt8
    winner: arc4.UInt8
    turnout: arc4.UInt64


class VotingDApp(ARC4Contract):
    """ASA Stats DAO voting sessions weighted by the lowest ASASTATS held while voting."""

    def __init__(self) -> None:
        self.permission_app_id = UInt64(0)
        self.admin = Account()
        self.session_count = UInt64(0)
        self.sessions = BoxMap(UInt64, Session, key_prefix=b"s")
        self.ballots = BoxMap(BallotKey, Ballot, key_prefix=b"v")

    # # LIFECYCLE
    @arc4.abimethod(create="require")
    def create_application(self, permission_app_id: UInt64) -> None:
        """
        Create the application and make the sender its admin.

        Args:
            permission_app_id: The Permission dApp whose `votes_of` caps weights
        """
        assert permission_app_id > 0, "Permission dApp ID is required"
        self.permission_app_id = permission_app_id
        self.admin = Txn.sender

    @arc4.baremethod(allow_actions=["UpdateApplication"])
    def update_application(self) -> None:
        """Allow only the admin to update the application."""
        assert Txn.sender == self.admin, "Only admin can update application"

    @arc4.baremethod(allow_actions=["DeleteApplication"])
    def delete_application(self) -> None:
        """Allow only the admin to delete the application."""
        assert Txn.sender == self.admin, "Only admin can delete application"

    # # SESSION
    @arc4.abimethod
    def create_session(
        self,
        start_round: UInt64,
        end_round: UInt64,
        seal_round: UInt64,
        options_count: UInt64,
        abstain: UInt64,
        quorum: UInt64,
        threshold_num: UInt64,
        threshold_den: UInt64,
        proposal_hash: Hash,
    ) -> UInt64:
        """
        Create a voting session.

        Args:
            start_round: The first round a vote is accepted in
            end_round: The last round a vote is accepted in
            seal_round: The first round the final amounts can be sealed in
            options_count: The number of options, 2 to 8
            abstain: The index of the Abstain option, 255 for none
            quorum: The minimum turnout in micro-votes
            threshold_num: The numerator of the share the winner must exceed
            threshold_den: The denominator of that share
            proposal_hash: The sha256 of the proposal text

        Returns:
            The identifier of the new session
        """
        assert Txn.sender == self.admin, "Only admin can create sessions"
        assert Global.round < start_round, "Start round must be in the future"
        assert start_round < end_round, "End must follow the start"
        assert end_round < seal_round, "Seal round must follow the end"
        assert options_count <= MAX_OPTIONS, "At most eight options are allowed"
        decisive = options_count
        if abstain != NOT_VOTED:
            assert abstain < options_count, "Abstain must be one of the options"
            decisive -= 1
        assert decisive >= 2, "At least two options besides Abstain are required"
        assert threshold_den > 0, "Threshold denominator is required"
        assert threshold_den <= MAX_THRESHOLD_DEN, "Threshold denominator too big"
        assert threshold_num < threshold_den, "Threshold must be below one"
        # at least a half, so that no two options can both clear the threshold
        assert threshold_num * 2 >= threshold_den, "Threshold must be at least half"

        session_id = self.session_count + 1
        self.session_count = session_id
        self.sessions[session_id] = Session(
            start_round=arc4.UInt64(start_round),
            end_round=arc4.UInt64(end_round),
            seal_round=arc4.UInt64(seal_round),
            options_count=arc4.UInt8(options_count),
            abstain=arc4.UInt8(abstain),
            quorum=arc4.UInt64(quorum),
            threshold_num=arc4.UInt64(threshold_num),
            threshold_den=arc4.UInt64(threshold_den),
            proposal_hash=proposal_hash.copy(),
            final_hash=Hash.from_bytes(op.bzero(32)),
            status=arc4.UInt8(STATUS_OPEN),
            winner=arc4.UInt8(NOT_VOTED),
            voters_count=arc4.UInt64(0),
            weighed_count=arc4.UInt64(0),
            totals=Totals.from_bytes(op.bzero(64)),
        )
        return session_id

    # # VOTING
    @arc4.abimethod
    def vote(self, session_id: UInt64, option: UInt64) -> None:
        """
        Cast or change the sender's vote in an open session.

        Args:
            session_id: The session to vote in
            option: The zero-based index of the chosen option
        """
        session = self._session(session_id)
        assert session.status == STATUS_OPEN, "Session is not open for voting"
        assert Global.round >= session.start_round, "Voting has not started"
        assert Global.round <= session.end_round, "Voting has ended"
        assert option < session.options_count.as_uint64(), "Unknown option"

        seat = Txn.sender
        max_votes = self._max_votes(seat)
        assert max_votes > 0, "Seat has no votes"

        key = BallotKey(arc4.UInt64(session_id), arc4.Address(seat))
        if key not in self.ballots:
            session.voters_count = arc4.UInt64(session.voters_count.as_uint64() + 1)
            self.sessions[session_id] = session.copy()
        self.ballots[key] = Ballot(
            option=arc4.UInt8(option),
            max_votes=arc4.UInt64(max_votes),
            eligible=arc4.UInt64(0),
            weight=arc4.UInt64(0),
            weighed=arc4.Bool(False),
        )

        arc4.emit(
            Voted(
                arc4.UInt64(session_id),
                arc4.Address(seat),
                arc4.UInt8(option),
                arc4.UInt64(max_votes),
            )
        )

    # # RESULT
    @arc4.abimethod
    def set_final(
        self,
        session_id: UInt64,
        seats: arc4.DynamicArray[arc4.Address],
        amounts: arc4.DynamicArray[arc4.UInt64],
    ) -> None:
        """
        Write or correct the lowest ASASTATS voters held while voting, before the seal.

        Args:
            session_id: The ended session
            seats: The seat addresses of voters
            amounts: The lowest micro-ASASTATS per seat during voting
        """
        assert Txn.sender == self.admin, "Only admin can write final amounts"
        session = self._session(session_id)
        assert session.status == STATUS_OPEN, "Final amounts are sealed"
        assert Global.round > session.end_round, "Voting has not ended"
        assert seats.length == amounts.length, "Seats and amounts differ in length"

        for index in urange(seats.length):
            key = BallotKey(arc4.UInt64(session_id), seats[index])
            assert key in self.ballots, "Seat has not voted"
            ballot = self.ballots[key].copy()
            option = ballot.option.as_uint64()
            total = session.totals[option].as_uint64()
            if ballot.weighed.native:
                total -= ballot.weight.as_uint64()
            else:
                session.weighed_count = arc4.UInt64(
                    session.weighed_count.as_uint64() + 1
                )

            weight = ballot.max_votes.as_uint64() * MICRO
            if amounts[index].as_uint64() < weight:
                weight = amounts[index].as_uint64()
            session.totals[option] = arc4.UInt64(total + weight)

            ballot.eligible = amounts[index]
            ballot.weight = arc4.UInt64(weight)
            ballot.weighed = arc4.Bool(True)
            self.ballots[key] = ballot.copy()

        self.sessions[session_id] = session.copy()

    @arc4.abimethod
    def seal_final(self, session_id: UInt64, final_hash: Hash) -> None:
        """
        Seal the final amounts; no final write is possible afterwards.

        Args:
            session_id: The session to seal
            final_hash: The sha256 of the published final file
        """
        assert Txn.sender == self.admin, "Only admin can seal final amounts"
        session = self._session(session_id)
        assert session.status == STATUS_OPEN, "Final amounts are sealed"
        assert Global.round >= session.seal_round, "Seal round not reached"
        assert (
            session.weighed_count == session.voters_count
        ), "Every voter needs a final amount"

        session.final_hash = final_hash.copy()
        session.status = arc4.UInt8(STATUS_SEALED)
        self.sessions[session_id] = session.copy()

    @arc4.abimethod
    def finalize(self, session_id: UInt64) -> None:
        """
        Record whether an option has won a session whose final amounts are sealed.

        Args:
            session_id: The session to finalize
        """
        session = self._session(session_id)
        assert session.status == STATUS_SEALED, "Final amounts are not sealed"

        abstain = session.abstain.as_uint64()
        turnout = UInt64(0)
        decisive = UInt64(0)
        winner = UInt64(NOT_VOTED)
        winner_total = UInt64(0)
        for index in urange(session.options_count.as_uint64()):
            total = session.totals[index].as_uint64()
            turnout += total
            if index != abstain:
                decisive += total
                if total > winner_total:
                    winner_total = total
                    winner = index

        status = UInt64(STATUS_FAILED)
        if (
            turnout >= session.quorum.as_uint64()
            and winner_total * session.threshold_den.as_uint64()
            > decisive * session.threshold_num.as_uint64()
        ):
            status = UInt64(STATUS_PASSED)
        else:
            winner = UInt64(NOT_VOTED)

        session.status = arc4.UInt8(status)
        session.winner = arc4.UInt8(winner)
        self.sessions[session_id] = session.copy()

        arc4.emit(
            Finalized(
                arc4.UInt64(session_id),
                arc4.UInt8(status),
                arc4.UInt8(winner),
                arc4.UInt64(turnout),
            )
        )

    # # SUBROUTINES
    @subroutine
    def _session(self, session_id: UInt64) -> Session:
        assert session_id in self.sessions, "Session does not exist"
        return self.sessions[session_id]

    @subroutine
    def _max_votes(self, seat: Account) -> UInt64:  # pragma: no cover
        # inner calls don't run under algopy-testing; the localnet tests run this
        votes, _txn = arc4.abi_call[arc4.UInt64](
            "votes_of(address)uint64",
            arc4.Address(seat),
            app_id=self.permission_app_id,
            fee=0,
        )
        return votes.as_uint64()
