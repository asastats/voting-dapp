"""Module with the Voting dApp integration test run on Testnet.

Deselected by default, as every run leaves a session and a ballot box on
Testnet: run it with ``python -m pytest -m testnet --no-cov``.
"""

import base64
from pathlib import Path

import pytest
from algosdk.abi.contract import Contract
from algosdk.account import address_from_private_key
from algosdk.atomic_transaction_composer import (
    AccountTransactionSigner,
    AtomicTransactionComposer,
)
from algosdk.encoding import decode_address
from algosdk.error import AlgodHTTPError

from dapp.configuration import NOT_VOTED
from dapp.deploy import algod_client
from dapp.helpers import (
    admin_parameters,
    environment_variables,
    load_contract,
    permission_dapp_id,
    private_key_from_mnemonic,
    read_json,
    voting_dapp_id,
)
from dapp.network import (
    create_session,
    finalize,
    seal_snapshot,
    session_ballots,
    session_values,
    set_snapshot,
    vote,
)

pytestmark = pytest.mark.testnet

MICRO = 1_000_000
PERMISSION_ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
# rounds left for creating the session, writing and sealing the snapshot
LEAD_ROUNDS = 20
VOTING_ROUNDS = 8
SEAT_VOTES = 5
SEAT_ELIGIBLE = 3 * MICRO + 1


def _wait_for_round(client, target_round):
    """Wait until the next transaction is evaluated at `target_round` or later."""
    last_round = client.status()["last-round"]
    while last_round + 1 < target_round:
        last_round = client.status_after_block(last_round)["last-round"]


def _permission_call(client, permission_id, admin, method, seat, *args):
    """Call the Permission dApp's box `method` for `seat` as its creator."""
    contract = Contract.undictify(
        read_json(PERMISSION_ARTIFACTS / "PermissionDApp.arc56.json")
    )
    box_name = decode_address(seat)
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id=permission_id,
        method=contract.get_method_by_name(method),
        sender=admin["sender"],
        sp=client.suggested_params(),
        signer=admin["signer"],
        method_args=[box_name, *args],
        boxes=[(permission_id, box_name)],
    )
    atc.execute(client, 4)


class TestIntegrationTestnet:
    """Testing class for a whole voting session on Testnet."""

    def test_integration_testnet_session_from_snapshot_to_finalize(self):
        env = environment_variables()
        client = algod_client(env, "testnet")
        app_id = voting_dapp_id(network="testnet")
        permission_id = permission_dapp_id(network="testnet")
        admin = admin_parameters(env, network="testnet")
        seat_key = private_key_from_mnemonic(env["user_testnet_mnemonic"])
        seat = address_from_private_key(seat_key)
        seat_parameters = {
            "sender": seat,
            "signer": AccountTransactionSigner(seat_key),
            "contract": load_contract(),
        }
        account = client.account_info(seat)
        assert (
            account["amount"] - account["min-balance"] >= 10_000
        ), f"Send some Testnet ALGO to the seat {seat}; it pays the vote fees"

        _permission_call(
            client,
            permission_id,
            admin,
            "write_box",
            seat,
            base64.b64encode(SEAT_VOTES.to_bytes(8, "big") + bytes(40)).decode(),
        )
        try:
            current = client.status()["last-round"]
            start_round = current + LEAD_ROUNDS
            end_round = start_round + VOTING_ROUNDS
            session_id = create_session(
                client, app_id, admin, current, start_round, end_round, b"\x01" * 32
            )
            set_snapshot(client, app_id, admin, session_id, {seat: SEAT_ELIGIBLE})
            seal_snapshot(client, app_id, admin, session_id, b"\x02" * 32)

            session = session_values(client, app_id, session_id)
            assert session["status"] == 1
            assert session["snapshot_round"] == current
            ballots = session_ballots(client, app_id, session_id)
            assert ballots[seat]["eligible"] == SEAT_ELIGIBLE
            assert ballots[seat]["option"] == NOT_VOTED

            # the session is sealed, but it hasn't started
            with pytest.raises(AlgodHTTPError, match="logic eval error"):
                vote(client, app_id, permission_id, seat_parameters, session_id, 1)

            _wait_for_round(client, start_round)
            vote(client, app_id, permission_id, seat_parameters, session_id, 1)
            session = session_values(client, app_id, session_id)
            assert session["voters_count"] == 1
            assert session["totals"] == [0, SEAT_ELIGIBLE, 0]
            assert session_ballots(client, app_id, session_id)[seat] == {
                "eligible": SEAT_ELIGIBLE,
                "option": 1,
                "max_votes": SEAT_VOTES,
                "weight": SEAT_ELIGIBLE,
            }

            vote(client, app_id, permission_id, seat_parameters, session_id, 0)
            session = session_values(client, app_id, session_id)
            assert session["voters_count"] == 1
            assert session["totals"] == [SEAT_ELIGIBLE, 0, 0]

            _wait_for_round(client, end_round + 1)
            finalize(client, app_id, seat_parameters, session_id)
            session = session_values(client, app_id, session_id)
            assert session["status"] == 2
            assert session["winner"] == 0
        finally:
            _permission_call(client, permission_id, admin, "delete_box", seat)
