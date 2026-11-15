"""Command line script for running Voting dApp sessions as the admin.

Run from the repository's root, e.g. ``python -m dapp.sessions show testnet 1``.
"""

import argparse
import json

from .configuration import NOT_VOTED, STATUS_NAMES
from .deploy import algod_client
from .helpers import (
    admin_parameters,
    environment_variables,
    read_snapshot,
    sha256_of_file,
    voting_dapp_id,
)
from .network import (
    create_session,
    finalize,
    seal_snapshot,
    session_ballots,
    session_values,
    set_snapshot,
)


def _connection(network):
    """Return client, application ID and admin parameters for `network`.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :return: three-tuple
    """
    env = environment_variables()
    return (
        algod_client(env, network),
        voting_dapp_id(network=network),
        admin_parameters(env, network=network),
    )


def _pending_snapshot_writes(client, app_id, session_id, amounts):
    """Return the amounts that differ from the session's ballot boxes.

    A seat that has a box but is missing from `amounts` comes back with 0,
    which removes its box.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param session_id: voting session identifier
    :type session_id: int
    :param amounts: seat addresses and their eligible micro-ASASTATS
    :type amounts: dict
    :var written: eligible amounts found in the ballot boxes
    :type written: dict
    :return: dict
    """
    written = {
        address: ballot["eligible"]
        for address, ballot in session_ballots(client, app_id, session_id).items()
    }
    pending = {
        address: amount
        for address, amount in amounts.items()
        if amount != written.get(address, 0)
    }
    pending.update({address: 0 for address in written if address not in amounts})
    return pending


def create(network, proposal_file, snapshot_round, start_round, end_round, **kwargs):
    """Create a session for the proposal file and return its identifier.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param proposal_file: path to the file holding the proposal text
    :type proposal_file: str
    :param snapshot_round: round every seat's ASASTATS are measured at
    :type snapshot_round: int
    :param start_round: first round a vote is accepted in
    :type start_round: int
    :param end_round: last round a vote is accepted in
    :type end_round: int
    :return: int
    """
    client, app_id, parameters = _connection(network)
    session_id = create_session(
        client,
        app_id,
        parameters,
        snapshot_round,
        start_round,
        end_round,
        sha256_of_file(proposal_file),
        **kwargs,
    )
    print(f"Created session {session_id}")
    return session_id


def close(network, session_id):
    """Finalize the ended session defined by `session_id`.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    """
    client, app_id, parameters = _connection(network)
    finalize(client, app_id, parameters, session_id)
    show(network, session_id)


def current_round(network):
    """Print and return the latest round of `network`.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :return: int
    """
    last_round = algod_client(environment_variables(), network).status()["last-round"]
    print(f"The latest round on {network} is {last_round}")
    return last_round


def seal(network, session_id, snapshot_file):
    """Seal the session's snapshot with the hash of the published snapshot file.

    Refuses when the ballot boxes differ from the file or the file is for
    another snapshot round.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param snapshot_file: path to the published snapshot file
    :type snapshot_file: str
    :var pending: amounts that differ from the session's ballot boxes
    :type pending: dict
    """
    client, app_id, parameters = _connection(network)
    snapshot_round, amounts = read_snapshot(snapshot_file)
    session = session_values(client, app_id, session_id)
    if session is None or session["snapshot_round"] != snapshot_round:
        raise ValueError("The snapshot file isn't for this session's snapshot round!")

    pending = _pending_snapshot_writes(client, app_id, session_id, amounts)
    if pending:
        raise ValueError(f"{len(pending)} seats differ from the file; write it first!")

    seal_snapshot(client, app_id, parameters, session_id, sha256_of_file(snapshot_file))
    print(f"Sealed the snapshot of session {session_id}")


def show(network, session_id):
    """Print the values and ballots of the session defined by `session_id`.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :var session: session's values
    :type session: dict
    """
    client, app_id, _ = _connection(network)
    session = session_values(client, app_id, session_id)
    if session is None:
        print("There is no such session!")
        return

    session["status"] = STATUS_NAMES[session["status"]]
    print(json.dumps(session, indent=2))
    for address, ballot in sorted(session_ballots(client, app_id, session_id).items()):
        option = "-" if ballot["option"] == NOT_VOTED else ballot["option"]
        print(address, ballot["eligible"], option, ballot["weight"])


def snapshot(network, session_id, snapshot_file):
    """Write the snapshot file's amounts that the session's boxes don't hold yet.

    Running it again after a correction of the file writes only the changes.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param snapshot_file: path to the snapshot file
    :type snapshot_file: str
    :var pending: amounts that differ from the session's ballot boxes
    :type pending: dict
    :return: int
    """
    client, app_id, parameters = _connection(network)
    _, amounts = read_snapshot(snapshot_file)
    pending = _pending_snapshot_writes(client, app_id, session_id, amounts)
    set_snapshot(client, app_id, parameters, session_id, pending)
    print(f"Wrote {len(pending)} seats to session {session_id}")
    return len(pending)


def main(argv=None):
    """Parse the command line arguments and run the related command.

    :param argv: command line arguments, the process's own when omitted
    :type argv: list
    :return: object
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    command = commands.add_parser("create")
    command.add_argument("network")
    command.add_argument("proposal_file")
    command.add_argument("snapshot_round", type=int)
    command.add_argument("start_round", type=int)
    command.add_argument("end_round", type=int)
    command.add_argument("--options-count", type=int, default=3)
    command.add_argument("--quorum", type=int, default=0)
    command.add_argument("--threshold", type=int, nargs=2, default=(1, 2))
    command.set_defaults(function=create)

    for name, function in (("snapshot", snapshot), ("seal", seal)):
        command = commands.add_parser(name)
        command.add_argument("network")
        command.add_argument("session_id", type=int)
        command.add_argument("snapshot_file")
        command.set_defaults(function=function)

    for name, function in (("close", close), ("show", show)):
        command = commands.add_parser(name)
        command.add_argument("network")
        command.add_argument("session_id", type=int)
        command.set_defaults(function=function)

    command = commands.add_parser("round")
    command.add_argument("network")
    command.set_defaults(function=current_round)

    arguments = vars(parser.parse_args(argv))
    arguments.pop("command")
    if "threshold" in arguments:
        arguments["threshold"] = tuple(arguments["threshold"])

    return arguments.pop("function")(**arguments)


if __name__ == "__main__":  # pragma: no cover
    main()
