"""Module with localnet integration tests for the Voting dApp smart contract."""

import base64
import json
import os
import time
from pathlib import Path

import pytest
from algokit_utils import AlgoAmount, AlgorandClient
from algosdk import transaction
from algosdk.abi.contract import Contract
from algosdk.atomic_transaction_composer import (
    AccountTransactionSigner,
    AtomicTransactionComposer,
    sign_transaction_with_signer,
)
from algosdk.encoding import decode_address
from algosdk.error import AlgodHTTPError
from algosdk.logic import get_application_address

from dapp.configuration import NOT_VOTED
from dapp.helpers import (
    ARTIFACTS,
    DAPP_NAME,
    compile_program,
    load_contract,
    read_json,
    sha256_of_file,
)
from dapp.network import (
    app_global_state,
    create_app,
    create_session,
    finalize,
    holdings_rounds,
    seal_final,
    session_ballots,
    session_values,
    set_final,
    vote,
)
from dapp.tally import tally

MICRO = 1_000_000
PERMISSION_ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
PROPOSAL_HASH = b"\x01" * 32
FINAL_HASH = b"\x02" * 32
LOGIC_ERROR = "logic eval error"


@pytest.fixture(scope="module")
def algorand():
    """Return the localnet client; fail early if localnet is not running."""
    client = AlgorandClient.from_environment()
    try:
        client.client.algod.status()
    except Exception as exception:
        raise RuntimeError(
            "Localnet is not running. Start it with: algokit localnet start"
        ) from exception

    return client


def _parameters(account, contract=None):
    return {
        "sender": account.address,
        "signer": AccountTransactionSigner(account.private_key),
        "contract": contract or load_contract(),
    }


def _account(algorand, algo=10):
    account = algorand.account.random()
    algorand.account.ensure_funded_from_environment(
        account.address, AlgoAmount(algo=algo)
    )
    return account


def _send(algod, txn, account):
    tx_id = algod.send_transaction(
        sign_transaction_with_signer(txn, AccountTransactionSigner(account.private_key))
    )
    return transaction.wait_for_confirmation(algod, tx_id, 4)


def _advance_to(algod, account, target_round):
    """Send payments until the next transaction is evaluated at `target_round`."""
    while algod.status()["last-round"] + 1 < target_round:
        _send(
            algod,
            transaction.PaymentTxn(
                account.address,
                algod.suggested_params(),
                account.address,
                0,
                note=os.urandom(8),
            ),
            account,
        )


def _programs(algod, folder, name):
    return (
        compile_program(algod, (folder / f"{name}.approval.teal").read_bytes()),
        compile_program(algod, (folder / f"{name}.clear.teal").read_bytes()),
    )


class Deployment:
    """Permission dApp and Voting dApp deployed to localnet by one admin."""

    def __init__(self, algorand):
        self.algorand = algorand
        self.algod = algorand.client.algod
        self.admin = _account(algorand, algo=100)
        self.parameters = _parameters(self.admin)

        approval, clear = _programs(self.algod, PERMISSION_ARTIFACTS, "PermissionDApp")
        self.permission_contract = Contract.undictify(
            read_json(PERMISSION_ARTIFACTS / "PermissionDApp.arc56.json")
        )
        self.permission_id = _send(
            self.algod,
            transaction.ApplicationCreateTxn(
                self.admin.address,
                self.algod.suggested_params(),
                transaction.OnComplete.NoOpOC,
                approval,
                clear,
                transaction.StateSchema(0, 0),
                transaction.StateSchema(0, 0),
            ),
            self.admin,
        )["application-index"]

        self.programs = _programs(self.algod, ARTIFACTS, DAPP_NAME)
        self.app_id, _ = create_app(
            self.algod,
            self.parameters,
            *self.programs,
            read_json(ARTIFACTS / f"{DAPP_NAME}.arc56.json"),
            self.permission_id,
        )
        for app_id in (self.permission_id, self.app_id):
            _send(
                self.algod,
                transaction.PaymentTxn(
                    self.admin.address,
                    self.algod.suggested_params(),
                    get_application_address(app_id),
                    3 * MICRO,
                ),
                self.admin,
            )

    def seat(self, votes):
        """Return a new funded account with `votes` in its Permission dApp box."""
        account = _account(self.algorand)
        if votes is not None:
            box_name = decode_address(account.address)
            atc = AtomicTransactionComposer()
            atc.add_method_call(
                app_id=self.permission_id,
                method=self.permission_contract.get_method_by_name("write_box"),
                sender=self.admin.address,
                sp=self.algod.suggested_params(),
                signer=self.parameters["signer"],
                method_args=[
                    box_name,
                    base64.b64encode(votes.to_bytes(8, "big") + bytes(40)).decode(),
                ],
                boxes=[(self.permission_id, box_name)],
            )
            atc.execute(self.algod, 4)

        return account

    def session(self, lead=20, length=10, review=5, **kwargs):
        """Create a session starting `lead` rounds from now; return its rounds."""
        start_round = self.algod.status()["last-round"] + lead
        end_round = start_round + length
        session_id = create_session(
            self.algod,
            self.app_id,
            self.parameters,
            start_round,
            end_round,
            end_round + review,
            PROPOSAL_HASH,
            **kwargs,
        )
        return session_id, start_round, end_round, end_round + review

    def vote(self, account, session_id, option):
        vote(
            self.algod,
            self.app_id,
            self.permission_id,
            _parameters(account),
            session_id,
            option,
        )


@pytest.fixture()
def deployment(algorand):
    return Deployment(algorand)


def _set_votes(deployment, account, votes):
    """Rewrite the Permission dApp box of `account` with `votes`."""
    box_name = decode_address(account.address)
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id=deployment.permission_id,
        method=deployment.permission_contract.get_method_by_name("write_box"),
        sender=deployment.admin.address,
        sp=deployment.algod.suggested_params(),
        signer=deployment.parameters["signer"],
        method_args=[
            box_name,
            base64.b64encode(votes.to_bytes(8, "big") + bytes(40)).decode(),
        ],
        boxes=[(deployment.permission_id, box_name)],
    )
    atc.execute(deployment.algod, 4)


def _wait_for_indexer(indexer, target_round, timeout=60):
    """Wait until the indexer has caught up with `target_round`."""
    deadline = time.time() + timeout
    while indexer.health().get("round", 0) < target_round:
        if time.time() > deadline:
            raise TimeoutError(f"Indexer didn't reach round {target_round}")
        time.sleep(1)


class TestIntegrationSession:
    """Testing class for a whole voting session on localnet."""

    def test_integration_session_from_votes_to_finalize(self, deployment):
        algod, app_id = deployment.algod, deployment.app_id
        capped_by_amount = deployment.seat(votes=10)
        capped_by_votes = deployment.seat(votes=3)
        abstaining = deployment.seat(votes=100)
        no_votes = deployment.seat(votes=None)
        session_id, start_round, end_round, seal_round = deployment.session(abstain=2)

        assert session_id == 1
        assert app_global_state(algod, app_id) == {
            "permission_app_id": deployment.permission_id,
            "admin": decode_address(deployment.admin.address),
            "session_count": 1,
        }

        # no vote before the start
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            deployment.vote(abstaining, session_id, 0)

        _advance_to(algod, deployment.admin, start_round)
        deployment.vote(capped_by_amount, session_id, 1)
        deployment.vote(capped_by_votes, session_id, 0)
        deployment.vote(abstaining, session_id, 2)
        for account, option in ((no_votes, 0), (abstaining, 3)):
            with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
                deployment.vote(account, session_id, option)
        # a changed vote doesn't count the voter twice
        deployment.vote(capped_by_amount, session_id, 0)

        session = session_values(algod, app_id, session_id)
        assert session["status"] == 0
        assert session["abstain"] == 2
        assert session["seal_round"] == seal_round
        assert session["voters_count"] == 3
        assert session["totals"] == [0, 0, 0]
        assert session_ballots(algod, app_id, session_id)[capped_by_amount.address] == {
            "option": 0,
            "max_votes": 10,
            "eligible": 0,
            "weight": 0,
            "weighed": False,
        }

        # the final amounts only after the end, then sealed after the review
        amounts = {
            capped_by_amount.address: 4 * MICRO,
            capped_by_votes.address: 9 * MICRO,
            abstaining.address: 500 * MICRO,
        }
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            set_final(algod, app_id, deployment.parameters, session_id, amounts)
        _advance_to(algod, deployment.admin, end_round + 1)
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            deployment.vote(abstaining, session_id, 0)
        set_final(
            algod,
            app_id,
            deployment.parameters,
            session_id,
            {**amounts, capped_by_amount.address: 6 * MICRO},
        )
        set_final(  # a correction moves the weight
            algod,
            app_id,
            deployment.parameters,
            session_id,
            {capped_by_amount.address: 4 * MICRO},
        )
        session = session_values(algod, app_id, session_id)
        assert session["weighed_count"] == 3
        assert session["totals"] == [7 * MICRO, 0, 100 * MICRO]
        ballots = session_ballots(algod, app_id, session_id)
        assert ballots[capped_by_amount.address]["weight"] == 4 * MICRO
        assert ballots[capped_by_votes.address]["weight"] == 3 * MICRO

        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            finalize(algod, app_id, _parameters(abstaining), session_id)
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            seal_final(algod, app_id, deployment.parameters, session_id, FINAL_HASH)
        _advance_to(algod, deployment.admin, seal_round)
        seal_final(algod, app_id, deployment.parameters, session_id, FINAL_HASH)
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            set_final(algod, app_id, deployment.parameters, session_id, amounts)

        # Abstain holds most of the weight but can't win
        finalize(algod, app_id, _parameters(abstaining), session_id)
        session = session_values(algod, app_id, session_id)
        assert session["final_hash"] == FINAL_HASH.hex()
        assert (session["status"], session["winner"]) == (2, 0)
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            finalize(algod, app_id, _parameters(abstaining), session_id)

    def test_integration_session_fails_below_two_thirds(self, deployment):
        algod, app_id = deployment.algod, deployment.app_id
        seat1, seat2 = deployment.seat(votes=66), deployment.seat(votes=34)
        session_id, start_round, end_round, seal_round = deployment.session(
            options_count=2, threshold=(2, 3)
        )
        _advance_to(algod, deployment.admin, start_round)
        deployment.vote(seat1, session_id, 0)
        deployment.vote(seat2, session_id, 1)
        _advance_to(algod, deployment.admin, seal_round)
        set_final(
            algod,
            app_id,
            deployment.parameters,
            session_id,
            {seat1.address: 500 * MICRO, seat2.address: 500 * MICRO},
        )
        seal_final(algod, app_id, deployment.parameters, session_id, FINAL_HASH)

        finalize(algod, app_id, deployment.parameters, session_id)

        session = session_values(algod, app_id, session_id)
        assert session["totals"] == [66 * MICRO, 34 * MICRO]
        assert (session["status"], session["winner"]) == (3, NOT_VOTED)

    def test_integration_final_amounts_in_several_calls(self, deployment):
        algod, app_id = deployment.algod, deployment.app_id
        seats = [deployment.seat(votes=1) for _ in range(9)]
        session_id, start_round, end_round, _ = deployment.session()
        _advance_to(algod, deployment.admin, start_round)
        for seat in seats:
            deployment.vote(seat, session_id, 0)
        _advance_to(algod, deployment.admin, end_round + 1)

        amounts = {seat.address: index + 1 for index, seat in enumerate(seats)}
        set_final(algod, app_id, deployment.parameters, session_id, amounts)

        ballots = session_ballots(algod, app_id, session_id)
        assert {address: box["eligible"] for address, box in ballots.items()} == amounts
        assert session_values(algod, app_id, session_id)["totals"][0] == 45
        assert session_ballots(algod, app_id, session_id + 1) == {}

    def test_integration_missing_session(self, deployment):
        assert session_values(deployment.algod, deployment.app_id, 1) is None
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            finalize(deployment.algod, deployment.app_id, deployment.parameters, 1)


class TestIntegrationAdmin:
    """Testing class for the admin-only calls on localnet."""

    def test_integration_only_admin_runs_sessions(self, deployment):
        algod, app_id = deployment.algod, deployment.app_id
        other = _parameters(_account(deployment.algorand))
        current = algod.status()["last-round"]
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            create_session(
                algod,
                app_id,
                other,
                current + 20,
                current + 30,
                current + 40,
                PROPOSAL_HASH,
            )

        seat = deployment.seat(votes=1)
        session_id, start_round, end_round, seal_round = deployment.session()
        _advance_to(algod, deployment.admin, start_round)
        deployment.vote(seat, session_id, 0)
        _advance_to(algod, deployment.admin, seal_round)
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            set_final(algod, app_id, other, session_id, {seat.address: 1})
        with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
            seal_final(algod, app_id, other, session_id, FINAL_HASH)

    def test_integration_only_admin_updates_and_deletes(self, deployment):
        algod, app_id = deployment.algod, deployment.app_id
        other = _account(deployment.algorand)

        def update(account):
            return transaction.ApplicationUpdateTxn(
                account.address, algod.suggested_params(), app_id, *deployment.programs
            )

        def delete(account):
            return transaction.ApplicationDeleteTxn(
                account.address, algod.suggested_params(), app_id
            )

        for build in (update, delete):
            with pytest.raises(AlgodHTTPError, match=LOGIC_ERROR):
                _send(algod, build(other), other)

        _send(algod, update(deployment.admin), deployment.admin)
        _send(algod, delete(deployment.admin), deployment.admin)
        with pytest.raises(AlgodHTTPError, match="application does not exist"):
            algod.application_info(app_id)


class TestIntegrationTally:
    """Testing class for the independent tally of a finished session on localnet."""

    def test_integration_tally_agrees_with_a_finished_session(
        self, deployment, tmp_path, capsys
    ):
        algod, app_id = deployment.algod, deployment.app_id
        indexer = deployment.algorand.client.indexer
        first, second, third = (deployment.seat(votes=votes) for votes in (10, 3, 5))
        session_id, start_round, end_round, seal_round = deployment.session(
            length=12, abstain=2
        )
        proposal = tmp_path / "proposal.md"
        proposal.write_bytes(b"proposal")

        _advance_to(algod, deployment.admin, start_round)
        deployment.vote(first, session_id, 1)
        deployment.vote(second, session_id, 1)
        deployment.vote(first, session_id, 0)  # changed: only the last vote counts
        _set_votes(deployment, second, 7)  # after its vote: noted, not a problem
        _advance_to(algod, deployment.admin, seal_round)
        _wait_for_indexer(indexer, algod.status()["last-round"])

        # localnet holds its own "holdings", so the rounds map onto themselves
        start, end = holdings_rounds(
            session_values(algod, app_id, session_id),
            indexer,
            indexer,
            indexer.health()["round"],
            False,
        )
        final_file = tmp_path / "final.json"
        final_file.write_text(
            json.dumps(
                {
                    "start_round": start,
                    "end_round": end,
                    "asset_id": 393537671,
                    "seats": {
                        first.address: {"total": 4 * MICRO, "venues": {}},
                        second.address: {"total": 9 * MICRO, "venues": {}},
                        third.address: {"total": 2 * MICRO, "venues": {}},
                    },
                }
            )
        )
        set_final(
            algod,
            app_id,
            deployment.parameters,
            session_id,
            {first.address: 4 * MICRO, second.address: 9 * MICRO},
        )
        seal_final(
            algod, app_id, deployment.parameters, session_id, sha256_of_file(final_file)
        )
        finalize(algod, app_id, _parameters(first), session_id)
        _wait_for_indexer(indexer, algod.status()["last-round"])

        clients = (algod, indexer, indexer, app_id, deployment.permission_id)
        report = tally(
            "localnet", session_id, final_file, str(proposal), clients=clients
        )
        assert report["problems"] == [
            "The proposal file's sha256 differs from the session's"
        ]
        report = tally("localnet", session_id, final_file, clients=clients)
        assert report["problems"] == []
        assert report["totals"] == [4 * MICRO, 3 * MICRO, 0]
        assert report["voters"] == 2
        assert (report["status"], report["winner"]) == (2, 0)
        assert report["notes"] == [
            f"{second.address}: votes_of was 3 at its vote, 7 now"
        ]
        assert capsys.readouterr().out.endswith("Everything agrees\n")

        # a file that isn't the sealed one
        final_file.write_text(final_file.read_text().replace("9000000", "9000001"))
        report = tally("localnet", session_id, final_file, clients=clients)
        assert report["problems"] == [
            "The final file's sha256 differs from the sealed hash",
            f"{second.address}: box amount {9 * MICRO}, file {9 * MICRO + 1}",
        ]
