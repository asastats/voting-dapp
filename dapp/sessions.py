"""Command line script for running Voting dApp sessions as the admin.

Run from the repository's root, e.g. ``python -m dapp.sessions show testnet 1``.
"""

import argparse
import json

from .configuration import HOLDINGS_NETWORK, NOT_VOTED, STATUS_NAMES
from .deploy import algod_client, indexer_client
from .helpers import (
    admin_parameters,
    environment_variables,
    read_final,
    sha256_of_file,
    voting_dapp_id,
)
from .network import (
    create_session,
    finalize,
    holdings_rounds,
    seal_final,
    session_ballots,
    session_values,
    set_final,
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


def _holdings(network, session):
    """Return the holdings rounds of the session and the holdings network's latest round.

    :param network: network the session runs on (e.g., "testnet")
    :type network: str
    :param session: decoded session box
    :type session: dict
    :var latest: latest round the holdings network's indexer has
    :type latest: int
    :return: three-tuple
    """
    env = environment_variables()
    holdings_indexer = indexer_client(env, HOLDINGS_NETWORK)
    latest = holdings_indexer.health()["round"]
    start, end = holdings_rounds(
        session,
        indexer_client(env, network),
        holdings_indexer,
        latest,
        network == HOLDINGS_NETWORK,
    )
    return start, end, latest


def _checked_final(network, session_id, final_file):
    """Return client, app ID, parameters, voters' ballots and the file's amounts.

    Refuses a file for other holdings rounds and one that misses a voter.

    :param network: network the session runs on (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param final_file: path to the final file
    :type final_file: str
    :return: tuple
    """
    client, app_id, parameters = _connection(network)
    session = session_values(client, app_id, session_id)
    if session is None:
        raise ValueError("There is no such session!")

    start, end, _ = _holdings(network, session)
    if end is None:
        raise ValueError("Voting hasn't ended yet!")

    file_start, file_end, amounts = read_final(final_file)
    if (file_start, file_end) != (start, end):
        raise ValueError(
            f"The file covers rounds {file_start}-{file_end}, the session's holdings "
            f"rounds are {start}-{end}!"
        )

    ballots = session_ballots(client, app_id, session_id)
    missing = sorted(set(ballots) - set(amounts))
    if missing:
        raise ValueError(f"{len(missing)} voters are missing from the file: {missing}")

    return client, app_id, parameters, ballots, amounts


def _pending_final_writes(ballots, amounts):
    """Return the voters' amounts that their ballot boxes don't hold yet.

    :param ballots: decoded ballot boxes by seat address
    :type ballots: dict
    :param amounts: lowest micro-ASASTATS per seat from the final file
    :type amounts: dict
    :return: dict
    """
    return {
        seat: amounts[seat]
        for seat, ballot in sorted(ballots.items())
        if not ballot["weighed"] or ballot["eligible"] != amounts[seat]
    }


def create(network, proposal_file, start_round, end_round, seal_round, **kwargs):
    """Create a session for the proposal file and return its identifier.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param proposal_file: path to the file holding the proposal text
    :type proposal_file: str
    :param start_round: first round a vote is accepted in
    :type start_round: int
    :param end_round: last round a vote is accepted in
    :type end_round: int
    :param seal_round: first round the final amounts can be sealed in
    :type seal_round: int
    :return: int
    """
    client, app_id, parameters = _connection(network)
    session_id = create_session(
        client,
        app_id,
        parameters,
        start_round,
        end_round,
        seal_round,
        sha256_of_file(proposal_file),
        **kwargs,
    )
    print(f"Created session {session_id}")
    return session_id


def close(network, session_id):
    """Finalize the sealed session defined by `session_id`.

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


def final(network, session_id, final_file):
    """Write the final file's amounts that the voters' boxes don't hold yet.

    Running it again after a correction of the file writes only the changes.

    :param network: network the session runs on (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param final_file: path to the final file
    :type final_file: str
    :return: int
    """
    client, app_id, parameters, ballots, amounts = _checked_final(
        network, session_id, final_file
    )
    pending = _pending_final_writes(ballots, amounts)
    set_final(client, app_id, parameters, session_id, pending)
    print(f"Wrote {len(pending)} of {len(ballots)} voters to session {session_id}")
    return len(pending)


def period(network, session_id, so_far=False):
    """Print the engine command that writes the session's final or so-far file.

    :param network: network the session runs on (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param so_far: True for the lowest amounts up to the latest round
    :type so_far: bool
    :return: two-tuple
    """
    client, app_id, _ = _connection(network)
    session = session_values(client, app_id, session_id)
    if session is None:
        raise ValueError("There is no such session!")

    start, end, latest = _holdings(network, session)
    if start is None:
        raise ValueError("Voting hasn't started yet!")
    if so_far:
        # the engine needs the round after the last one confirmed
        end, name = min(end or latest - 1, latest - 1), "sofar.json"
    elif end is None:
        raise ValueError("Voting hasn't ended yet; use --so-far!")
    else:
        name = "final.json"

    print(
        f"python manage.py daosnapshot {start} seats.json /tmp/{name} --to {end} "
        "--settings=engine.settings"
    )
    return start, end


def seal(network, session_id, final_file):
    """Seal the session's final amounts with the hash of the published final file.

    Refuses when a voter's box differs from the file.

    :param network: network the session runs on (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param final_file: path to the published final file
    :type final_file: str
    """
    client, app_id, parameters, ballots, amounts = _checked_final(
        network, session_id, final_file
    )
    pending = _pending_final_writes(ballots, amounts)
    if pending:
        raise ValueError(f"{len(pending)} voters differ from the file; write it first!")

    seal_final(client, app_id, parameters, session_id, sha256_of_file(final_file))
    print(f"Sealed the final amounts of session {session_id}")


def show(network, session_id):
    """Print the values and ballots of the session defined by `session_id`.

    Each ballot line: seat, option, votes, lowest amount and weight (the last two
    once the final amounts are written).

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
        weighed = (
            [ballot["eligible"], ballot["weight"]] if ballot["weighed"] else ["-", "-"]
        )
        print(address, ballot["option"], ballot["max_votes"], *weighed)


def _abstain(arguments):
    """Return the Abstain index from the parsed `create` arguments.

    :param arguments: parsed command line arguments
    :type arguments: dict
    :return: int
    """
    if arguments.pop("no_abstain"):
        arguments.pop("abstain")
        return NOT_VOTED

    abstain = arguments.pop("abstain")
    return arguments["options_count"] - 1 if abstain is None else abstain


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
    command.add_argument("start_round", type=int)
    command.add_argument("end_round", type=int)
    command.add_argument("seal_round", type=int)
    command.add_argument("--options-count", type=int, default=3)
    command.add_argument(
        "--abstain", type=int, help="index of the Abstain option (default: the last)"
    )
    command.add_argument("--no-abstain", action="store_true")
    command.add_argument("--quorum", type=int, default=0)
    command.add_argument("--threshold", type=int, nargs=2, default=(1, 2))
    command.set_defaults(function=create)

    for name, function in (("final", final), ("seal-final", seal)):
        command = commands.add_parser(name)
        command.add_argument("network")
        command.add_argument("session_id", type=int)
        command.add_argument("final_file")
        command.set_defaults(function=function)

    command = commands.add_parser("period")
    command.add_argument("network")
    command.add_argument("session_id", type=int)
    command.add_argument("--so-far", action="store_true")
    command.set_defaults(function=period)

    for name, function in (("close", close), ("show", show)):
        command = commands.add_parser(name)
        command.add_argument("network")
        command.add_argument("session_id", type=int)
        command.set_defaults(function=function)

    command = commands.add_parser("round")
    command.add_argument("network")
    command.set_defaults(function=current_round)

    arguments = vars(parser.parse_args(argv))
    if arguments.pop("command") == "create":
        arguments["abstain"] = _abstain(arguments)
        arguments["threshold"] = tuple(arguments["threshold"])

    return arguments.pop("function")(**arguments)


if __name__ == "__main__":  # pragma: no cover
    main()
