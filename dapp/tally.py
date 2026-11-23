"""Independent tally of a Voting dApp session from public data.

Run from the repository's root, e.g.
``python -m dapp.tally testnet 1 snapshot.json --proposal proposal.md``.
"""

import argparse
import base64
import sys

from algosdk.encoding import checksum, encode_address
from algosdk.v2client.indexer import IndexerClient

from .configuration import MICRO, NOT_VOTED, STATUS_NAMES
from .deploy import algod_client
from .helpers import (
    environment_variables,
    permission_dapp_id,
    read_snapshot,
    sha256_of_file,
    voting_dapp_id,
)
from .network import permission_votes, session_ballots, session_values

VOTED_SELECTOR = checksum(b"Voted(uint64,address,uint8,uint64)")[:4]
STATUS_SEALED, STATUS_PASSED, STATUS_FAILED = 1, 2, 3


# # EVENTS
def decode_voted(log):
    """Return the `Voted` event decoded from a raw log, None for any other log.

    :param log: raw application log
    :type log: bytes
    :return: dict
    """
    if len(log) != 53 or log[:4] != VOTED_SELECTOR:
        return None

    return {
        "session_id": int.from_bytes(log[4:12], "big"),
        "seat": encode_address(log[12:44]),
        "option": log[44],
        "weight": int.from_bytes(log[45:53], "big"),
    }


def _events_in_transaction(transaction, app_id, root):
    """Yield the `Voted` events the dApp logged in `transaction`, inner calls included.

    :param transaction: indexer transaction
    :type transaction: dict
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param root: top-level transaction carrying round and identifier
    :type root: dict
    :yield: dict
    """
    if transaction.get("application-transaction", {}).get("application-id") == app_id:
        for log in transaction.get("logs", []):
            event = decode_voted(base64.b64decode(log))
            if event is not None:
                event["round"] = root.get("confirmed-round")
                event["txid"] = root.get("id")
                yield event

    for inner in transaction.get("inner-txns", []):
        yield from _events_in_transaction(inner, app_id, root)


def voted_events(indexer, app_id, min_round, max_round):
    """Return every `Voted` event of the dApp between two rounds, in chain order.

    :param indexer: Algorand Indexer client instance
    :type indexer: :class:`IndexerClient`
    :param app_id: Voting dApp identifier
    :type app_id: int
    :param min_round: first round searched
    :type min_round: int
    :param max_round: last round searched
    :type max_round: int
    :var next_page: indexer's token for the next page
    :type next_page: str
    :return: list
    """
    events, next_page = [], None
    while True:
        response = indexer.search_transactions(
            application_id=app_id,
            min_round=min_round,
            max_round=max_round,
            limit=1000,
            next_page=next_page,
        )
        for transaction in response.get("transactions", []):
            events.extend(_events_in_transaction(transaction, app_id, transaction))

        next_page = response.get("next-token")
        if not next_page or not response.get("transactions"):
            return events


# # TALLY
def contract_result(totals, quorum, threshold_num, threshold_den):
    """Return status, winner and turnout by the contract's `finalize` rule.

    :param totals: weight per option
    :type totals: list
    :param quorum: minimum turnout in micro-votes
    :type quorum: int
    :param threshold_num: numerator of the share the winner must exceed
    :type threshold_num: int
    :param threshold_den: denominator of that share
    :type threshold_den: int
    :return: three-tuple
    """
    turnout = sum(totals)
    winner, winner_total = NOT_VOTED, 0
    for index, total in enumerate(totals):
        if total > winner_total:  # strict: a tie goes to the lower option
            winner, winner_total = index, total

    if turnout >= quorum and winner_total * threshold_den > turnout * threshold_num:
        return STATUS_PASSED, winner, turnout

    return STATUS_FAILED, NOT_VOTED, turnout


def tally_session(
    session_id, session, ballots, snapshot, events, votes_now, current_round
):
    """Recompute a session from public data and return what agrees and what doesn't.

    :param session_id: voting session identifier
    :type session_id: int
    :param session: decoded session box, None if there is no such session
    :type session: dict
    :param ballots: decoded ballot boxes by seat address
    :type ballots: dict
    :param snapshot: snapshot round, sha256 hex of the file, eligible amount per seat,
        and the proposal's sha256 hex or None
    :type snapshot: dict
    :param events: the dApp's `Voted` events in chain order
    :type events: list
    :param votes_now: Permission dApp votes per voted seat today
    :type votes_now: dict
    :param current_round: latest round of the network
    :type current_round: int
    :var last: each seat's latest event in this session
    :type last: dict
    :return: dict
    """
    problems, notes = [], []
    if session is None:
        return {"problems": ["There is no such session"], "notes": notes}

    if snapshot["round"] != session["snapshot_round"]:
        problems.append(
            f"Snapshot file round {snapshot['round']} differs from the session's "
            f"{session['snapshot_round']}"
        )
    if session["status"] == 0:
        notes.append("The snapshot is not sealed yet")
    elif snapshot["hash"] != session["snapshot_hash"]:
        problems.append("The snapshot file's sha256 differs from the sealed hash")
    if snapshot["proposal_hash"] not in (None, session["proposal_hash"]):
        problems.append("The proposal file's sha256 differs from the session's")

    eligible = {seat: amount for seat, amount in snapshot["amounts"].items() if amount}
    for seat, amount in sorted(eligible.items()):
        box = ballots.get(seat)
        if box is None:
            problems.append(f"{seat}: in the snapshot file, no ballot box")
        elif box["eligible"] != amount:
            problems.append(f"{seat}: box eligible {box['eligible']}, file {amount}")
    for seat in sorted(set(ballots) - set(eligible)):
        problems.append(f"{seat}: ballot box, not in the snapshot file")

    last = {}
    for event in events:
        if event["session_id"] != session_id:
            continue
        if not session["start_round"] <= event["round"] <= session["end_round"]:
            problems.append(f"{event['seat']}: vote in round {event['round']}, outside")
        if event["option"] >= session["options_count"]:
            problems.append(
                f"{event['seat']}: vote for unknown option {event['option']}"
            )
        last[event["seat"]] = event

    totals = [0] * session["options_count"]
    for seat, box in sorted(ballots.items()):
        event = last.get(seat)
        if box["option"] == NOT_VOTED:
            if event is not None:
                problems.append(f"{seat}: voted in {event['txid']}, box says not voted")
            continue

        if event is None:
            problems.append(f"{seat}: box holds a vote, no Voted event")
        elif (event["option"], event["weight"]) != (box["option"], box["weight"]):
            problems.append(
                f"{seat}: last event option {event['option']} weight {event['weight']}, "
                f"box option {box['option']} weight {box['weight']}"
            )
        expected = min(box["max_votes"] * MICRO, box["eligible"])
        if box["weight"] != expected:
            problems.append(
                f"{seat}: weight {box['weight']}, the rule gives {expected}"
            )
        if votes_now.get(seat, 0) != box["max_votes"]:
            notes.append(
                f"{seat}: votes_of was {box['max_votes']} at its vote, "
                f"{votes_now.get(seat, 0)} now"
            )
        if box["option"] < len(totals):
            totals[box["option"]] += box["weight"]

    from_events = [0] * session["options_count"]
    for event in last.values():
        if event["option"] < len(from_events):
            from_events[event["option"]] += event["weight"]
    if from_events != totals:
        problems.append(f"Totals from events {from_events}, from boxes {totals}")
    if totals != session["totals"]:
        problems.append(f"Totals recomputed {totals}, session {session['totals']}")
    voters = sum(1 for box in ballots.values() if box["option"] != NOT_VOTED)
    if voters != session["voters_count"]:
        problems.append(
            f"Voters recomputed {voters}, session {session['voters_count']}"
        )

    status, winner, turnout = contract_result(
        totals, session["quorum"], session["threshold_num"], session["threshold_den"]
    )
    if session["status"] in (STATUS_PASSED, STATUS_FAILED):
        if (session["status"], session["winner"]) != (status, winner):
            problems.append(
                f"Result recomputed {STATUS_NAMES[status]} winner {winner}, session "
                f"{STATUS_NAMES[session['status']]} winner {session['winner']}"
            )
    elif session["status"] == 0 and current_round >= session["start_round"]:
        notes.append(
            "Lapsed: not sealed before the start round, so no vote is possible"
        )
    elif session["status"] == STATUS_SEALED and current_round > session["end_round"]:
        notes.append(f"Not finalized yet; finalize would record {STATUS_NAMES[status]}")
    else:
        notes.append(
            f"Voting runs from round {session['start_round']} to "
            f"{session['end_round']}; the latest round is {current_round}"
        )

    return {
        "problems": problems,
        "notes": notes,
        "totals": totals,
        "turnout": turnout,
        "voters": voters,
        "status": status,
        "winner": winner,
    }


# # COMMAND
def indexer_client(env, network):
    """Return Algorand Indexer client for the provided `network`.

    :param env: environment variables collection
    :type env: dict
    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :return: :class:`IndexerClient`
    """
    return IndexerClient(
        env.get(f"indexer_token_{network}"), env.get(f"indexer_address_{network}")
    )


def tally(network, session_id, snapshot_file, proposal=None, clients=None):
    """Tally the session on `network`, print the report and return it.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param snapshot_file: path to the published snapshot file
    :type snapshot_file: str
    :param proposal: path to the published proposal file
    :type proposal: str
    :param clients: algod, indexer, Voting dApp and Permission dApp identifiers,
        when not the configured ones
    :type clients: tuple
    :return: dict
    """
    if clients is None:
        env = environment_variables()
        clients = (
            algod_client(env, network),
            indexer_client(env, network),
            voting_dapp_id(network=network),
            permission_dapp_id(network=network),
        )
    algod, indexer, app_id, permission_id = clients

    snapshot_round, amounts = read_snapshot(snapshot_file)
    snapshot = {
        "round": snapshot_round,
        "hash": sha256_of_file(snapshot_file).hex(),
        "amounts": amounts,
        "proposal_hash": sha256_of_file(proposal).hex() if proposal else None,
    }
    session = session_values(algod, app_id, session_id)
    ballots, events, votes_now = {}, [], {}
    if session is not None:
        ballots = session_ballots(algod, app_id, session_id)
        events = voted_events(
            indexer, app_id, session["start_round"], session["end_round"]
        )
        votes_now = {
            seat: permission_votes(algod, permission_id, seat)
            for seat, box in ballots.items()
            if box["option"] != NOT_VOTED
        }

    report = tally_session(
        session_id,
        session,
        ballots,
        snapshot,
        events,
        votes_now,
        algod.status()["last-round"],
    )
    _print_report(session_id, report)
    return report


def _print_report(session_id, report):
    """Print the tally report of the session.

    :param session_id: voting session identifier
    :type session_id: int
    :param report: the result of :func:`tally_session`
    :type report: dict
    """
    print(f"Session {session_id}")
    if "totals" in report:
        print(
            f"Totals {report['totals']}, turnout {report['turnout']}, "
            f"voters {report['voters']}"
        )
    for note in report["notes"]:
        print(f"NOTE {note}")
    for problem in report["problems"]:
        print(f"PROBLEM {problem}")
    print(
        "Everything agrees"
        if not report["problems"]
        else f"{len(report['problems'])} problems"
    )


def main(argv=None):
    """Parse the command line arguments, tally, and return the exit status.

    :param argv: command line arguments, the process's own when omitted
    :type argv: list
    :return: int
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("network")
    parser.add_argument("session_id", type=int)
    parser.add_argument("snapshot_file")
    parser.add_argument("--proposal")
    report = tally(**vars(parser.parse_args(argv)))
    return 1 if report["problems"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
