"""Module with functions for calling the Voting dApp and reading its boxes."""

import base64

from algosdk import transaction
from algosdk.atomic_transaction_composer import AtomicTransactionComposer
from algosdk.encoding import decode_address, encode_address
from algosdk.error import AlgodHTTPError

from .configuration import SNAPSHOT_SEATS_PER_CALL
from .helpers import (
    app_schemas,
    ballot_box_name,
    decode_ballot,
    decode_session,
    session_box_name,
)


def _call(client, app_id, parameters, method, args, boxes=(), **kwargs):
    """Call the dApp's `method` and return its ABI result when confirmed.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier, 0 to create the application
    :type app_id: int
    :param parameters: sender, signer and ABI contract of the caller
    :type parameters: dict
    :param method: ABI method name
    :type method: str
    :param args: ABI method arguments
    :type args: list
    :param boxes: names of the dApp's boxes the call references
    :type boxes: tuple
    :return: :class:`ABIResult`
    """
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id=app_id,
        method=parameters.get("contract").get_method_by_name(method),
        sender=parameters.get("sender"),
        sp=kwargs.pop("sp", None) or client.suggested_params(),
        signer=parameters.get("signer"),
        method_args=args,
        boxes=[(app_id, name) for name in boxes] + kwargs.pop("other_boxes", []),
        **kwargs,
    )
    return atc.execute(client, 4).abi_results[0]


# # APPLICATION
def create_app(
    client, parameters, approval_program, clear_program, contract_json, permission_id
):
    """Create the Voting dApp and return its identifier and the genesis hash.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param parameters: sender, signer and ABI contract of the creator
    :type parameters: dict
    :param approval_program: compiled approval program
    :type approval_program: bytes
    :param clear_program: compiled clear program
    :type clear_program: bytes
    :param contract_json: ARC-56 smart contract specification
    :type contract_json: dict
    :param permission_id: Permission dApp identifier the new application reads
    :type permission_id: int
    :return: two-tuple
    """
    global_schema, local_schema = app_schemas(contract_json)
    params = client.suggested_params()
    result = _call(
        client,
        0,
        parameters,
        "create_application",
        [permission_id],
        sp=params,
        approval_program=approval_program,
        clear_program=clear_program,
        global_schema=global_schema,
        local_schema=local_schema,
    )
    return result.tx_info["application-index"], params.gh


# # SESSION
def create_session(
    client,
    app_id,
    parameters,
    snapshot_round,
    start_round,
    end_round,
    proposal_hash,
    options_count=3,
    quorum=0,
    threshold=(1, 2),
):
    """Create a voting session and return its identifier.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param parameters: sender, signer and ABI contract of the admin
    :type parameters: dict
    :param snapshot_round: round every seat's ASASTATS are measured at
    :type snapshot_round: int
    :param start_round: first round a vote is accepted in
    :type start_round: int
    :param end_round: last round a vote is accepted in
    :type end_round: int
    :param proposal_hash: sha256 of the proposal text
    :type proposal_hash: bytes
    :param options_count: number of options to vote for
    :type options_count: int
    :param quorum: minimum turnout in micro-votes
    :type quorum: int
    :param threshold: numerator and denominator of the share the winner must exceed
    :type threshold: tuple
    :return: int
    """
    session_count = app_global_state(client, app_id).get("session_count", 0)
    return _call(
        client,
        app_id,
        parameters,
        "create_session",
        [
            snapshot_round,
            start_round,
            end_round,
            options_count,
            quorum,
            threshold[0],
            threshold[1],
            proposal_hash,
        ],
        boxes=[session_box_name(session_count + 1)],
    ).return_value


def finalize(client, app_id, parameters, session_id):
    """Finalize the ended session defined by `session_id`.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param parameters: sender, signer and ABI contract of the caller
    :type parameters: dict
    :param session_id: voting session identifier
    :type session_id: int
    """
    _call(
        client,
        app_id,
        parameters,
        "finalize",
        [session_id],
        boxes=[session_box_name(session_id)],
    )


def seal_snapshot(client, app_id, parameters, session_id, snapshot_hash):
    """Seal the snapshot of the session defined by `session_id`.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param parameters: sender, signer and ABI contract of the admin
    :type parameters: dict
    :param session_id: voting session identifier
    :type session_id: int
    :param snapshot_hash: sha256 of the published snapshot file
    :type snapshot_hash: bytes
    """
    _call(
        client,
        app_id,
        parameters,
        "seal_snapshot",
        [session_id, snapshot_hash],
        boxes=[session_box_name(session_id)],
    )


def set_snapshot(client, app_id, parameters, session_id, amounts):
    """Write the eligible amounts of seats to the session's ballot boxes.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param parameters: sender, signer and ABI contract of the admin
    :type parameters: dict
    :param session_id: voting session identifier
    :type session_id: int
    :param amounts: seat addresses and their eligible micro-ASASTATS; 0 removes
    :type amounts: dict
    :var seats: seat addresses written by the current call
    :type seats: list
    """
    addresses = list(amounts)
    for start in range(0, len(addresses), SNAPSHOT_SEATS_PER_CALL):
        seats = addresses[start : start + SNAPSHOT_SEATS_PER_CALL]
        _call(
            client,
            app_id,
            parameters,
            "set_snapshot",
            [session_id, seats, [amounts[seat] for seat in seats]],
            boxes=[session_box_name(session_id)]
            + [ballot_box_name(session_id, seat) for seat in seats],
        )


def vote(client, app_id, permission_id, parameters, session_id, option):
    """Vote for `option` in the session as the seat defined by `parameters`.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param permission_id: Permission dApp identifier the Voting dApp reads
    :type permission_id: int
    :param parameters: sender, signer and ABI contract of the governor seat
    :type parameters: dict
    :param session_id: voting session identifier
    :type session_id: int
    :param option: zero-based index of the chosen option
    :type option: int
    :var params: suggested parameters with the fee covering the inner call
    :type params: :class:`transaction.SuggestedParams`
    """
    params = client.suggested_params()
    params.flat_fee = True
    params.fee = 2 * max(params.min_fee, transaction.constants.MIN_TXN_FEE)
    seat = parameters.get("sender")
    _call(
        client,
        app_id,
        parameters,
        "vote",
        [session_id, option],
        boxes=[session_box_name(session_id), ballot_box_name(session_id, seat)],
        other_boxes=[(permission_id, decode_address(seat))],
        foreign_apps=[permission_id],
        sp=params,
    )


# # READING
def _box_value(client, app_id, box_name):
    """Return the raw value of the dApp's box, None if there is no such box.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param box_name: raw box name
    :type box_name: bytes
    :return: bytes
    """
    try:
        response = client.application_box_by_name(app_id, box_name)
    except AlgodHTTPError as exception:
        if "box not found" in str(exception):
            return None
        raise

    return base64.b64decode(response.get("value"))


def app_global_state(client, app_id):
    """Return the dApp's global state as names and integer or raw bytes values.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :return: dict
    """
    state = client.application_info(app_id).get("params", {}).get("global-state", [])
    return {
        base64.b64decode(row["key"]).decode(): (
            row["value"]["uint"]
            if row["value"]["type"] == 2
            else base64.b64decode(row["value"]["bytes"])
        )
        for row in state
    }


def session_ballots(client, app_id, session_id):
    """Return the session's ballots by seat address.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param session_id: voting session identifier
    :type session_id: int
    :var prefix: beginning of the session's ballot box names
    :type prefix: bytes
    :return: dict
    """
    prefix = b"v" + session_id.to_bytes(8, "big")
    ballots = {}
    for box in client.application_boxes(app_id).get("boxes", []):
        box_name = base64.b64decode(box.get("name"))
        if not box_name.startswith(prefix):
            continue

        value = _box_value(client, app_id, box_name)
        if value is not None:
            ballots[encode_address(box_name[len(prefix) :])] = decode_ballot(value)

    return ballots


def session_values(client, app_id, session_id):
    """Return the session's values, None if there is no such session.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param session_id: voting session identifier
    :type session_id: int
    :return: dict
    """
    value = _box_value(client, app_id, session_box_name(session_id))
    return decode_session(value) if value is not None else None
