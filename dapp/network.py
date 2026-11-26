"""Module with functions for calling the Voting dApp and reading its boxes."""

import base64

from algosdk import transaction
from algosdk.atomic_transaction_composer import AtomicTransactionComposer
from algosdk.encoding import decode_address, encode_address
from algosdk.error import AlgodHTTPError, IndexerHTTPError

from .configuration import FINAL_SEATS_PER_CALL, NOT_VOTED
from .helpers import (
    app_schemas,
    ballot_box_name,
    decode_ballot,
    decode_session,
    error_message,
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
    try:
        return atc.execute(client, 4).abi_results[0]
    except AlgodHTTPError as exception:
        message = error_message(app_id, exception)
        if message is None:
            raise
        raise AlgodHTTPError(f"{message} ({exception})", exception.code) from exception


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
    start_round,
    end_round,
    seal_round,
    proposal_hash,
    options_count=3,
    abstain=NOT_VOTED,
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
    :param start_round: first round a vote is accepted in
    :type start_round: int
    :param end_round: last round a vote is accepted in
    :type end_round: int
    :param seal_round: first round the final amounts can be sealed in
    :type seal_round: int
    :param proposal_hash: sha256 of the proposal text
    :type proposal_hash: bytes
    :param options_count: number of options to vote for
    :type options_count: int
    :param abstain: index of the Abstain option, NOT_VOTED for none
    :type abstain: int
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
            start_round,
            end_round,
            seal_round,
            options_count,
            abstain,
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


def seal_final(client, app_id, parameters, session_id, final_hash):
    """Seal the final amounts of the session defined by `session_id`.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param parameters: sender, signer and ABI contract of the admin
    :type parameters: dict
    :param session_id: voting session identifier
    :type session_id: int
    :param final_hash: sha256 of the published final file
    :type final_hash: bytes
    """
    _call(
        client,
        app_id,
        parameters,
        "seal_final",
        [session_id, final_hash],
        boxes=[session_box_name(session_id)],
    )


def set_final(client, app_id, parameters, session_id, amounts):
    """Write the lowest amounts voters held while voting to their ballot boxes.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param parameters: sender, signer and ABI contract of the admin
    :type parameters: dict
    :param session_id: voting session identifier
    :type session_id: int
    :param amounts: voters' seat addresses and their lowest micro-ASASTATS
    :type amounts: dict
    :var seats: seat addresses written by the current call
    :type seats: list
    """
    addresses = list(amounts)
    for start in range(0, len(addresses), FINAL_SEATS_PER_CALL):
        seats = addresses[start : start + FINAL_SEATS_PER_CALL]
        _call(
            client,
            app_id,
            parameters,
            "set_final",
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


def permission_votes(client, permission_id, address):
    """Return the votes the Permission dApp's `votes_of` gives `address` today.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param permission_id: Permission dApp identifier
    :type permission_id: int
    :param address: governor seat address
    :type address: str
    :var value: raw Permission box value, None when the address has no box
    :type value: bytes
    :return: int
    """
    value = _box_value(client, permission_id, decode_address(address))
    if value is None:
        return 0

    # as `votes_of`: the first 12 base64 characters hold 9 bytes, `votes` is 8 of them
    return int.from_bytes(base64.b64decode(value[:12])[:8], "big")


# # HOLDINGS ROUNDS
def block_timestamp(indexer, block):
    """Return the timestamp of `block`.

    :param indexer: Algorand Indexer client instance
    :type indexer: :class:`IndexerClient`
    :param block: round number
    :type block: int
    :return: int
    """
    return indexer.block_info(block)["timestamp"]


def round_at_timestamp(indexer, timestamp, latest_round):
    """Return the last round up to `latest_round` whose block is not after `timestamp`.

    :param indexer: Algorand Indexer client instance
    :type indexer: :class:`IndexerClient`
    :param timestamp: block timestamp
    :type timestamp: int
    :param latest_round: highest round to consider
    :type latest_round: int
    :var low: a round whose block is not after `timestamp`
    :type low: int
    :return: int
    """
    if block_timestamp(indexer, latest_round) <= timestamp:
        return latest_round

    low, high = 1, latest_round - 1
    while low < high:
        middle = (low + high + 1) // 2
        if block_timestamp(indexer, middle) <= timestamp:
            low = middle
        else:
            high = middle - 1
    return low


def holdings_rounds(session, voting_indexer, holdings_indexer, latest_round, same):
    """Return the holdings network's rounds matching the session's start and end.

    The end is None while the session's end round isn't confirmed yet.

    :param session: decoded session box
    :type session: dict
    :param voting_indexer: indexer of the network the session runs on
    :type voting_indexer: :class:`IndexerClient`
    :param holdings_indexer: indexer of the network holding the ASASTATS
    :type holdings_indexer: :class:`IndexerClient`
    :param latest_round: latest round of the holdings network
    :type latest_round: int
    :param same: True when the session runs on the holdings network itself
    :type same: bool
    :return: two-tuple
    """
    rounds = []
    for block in (session["start_round"], session["end_round"]):
        try:
            timestamp = block_timestamp(voting_indexer, block)
        except IndexerHTTPError:  # not confirmed yet
            rounds.append(None)
            continue
        rounds.append(
            block
            if same
            else round_at_timestamp(holdings_indexer, timestamp, latest_round)
        )
    return tuple(rounds)
