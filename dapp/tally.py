"""Independent tally of a Voting dApp session from public data.

Run from the repository's root, e.g.
``python -m dapp.tally testnet 1 --final final.json --proposal proposal.md``.
"""

import argparse
import base64
import sys

from algosdk.encoding import checksum, encode_address

from .configuration import (
    HOLDINGS_NETWORK,
    MICRO,
    NOT_VOTED,
    STATUS_FAILED,
    STATUS_NAMES,
    STATUS_OPEN,
    STATUS_PASSED,
    STATUS_SEALED,
)
from .deploy import algod_client, indexer_client
from .helpers import (
    environment_variables,
    permission_dapp_id,
    read_final,
    sha256_of_file,
    voting_dapp_id,
)
from .network import (
    holdings_rounds,
    permission_votes,
    session_ballots,
    session_values,
)

VOTED_SELECTOR = checksum(b"Voted(uint64,address,uint8,uint64)")[:4]


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
        "max_votes": int.from_bytes(log[45:53], "big"),
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
def contract_result(totals, abstain, quorum, threshold_num, threshold_den):
    """Return status, winner and turnout by the contract's `finalize` rule.

    :param totals: weight per option
    :type totals: list
    :param abstain: index of the Abstain option, NOT_VOTED for none
    :type abstain: int
    :param quorum: minimum turnout in micro-votes
    :type quorum: int
    :param threshold_num: numerator of the share the winner must exceed
    :type threshold_num: int
    :param threshold_den: denominator of that share
    :type threshold_den: int
    :return: three-tuple
    """
    turnout = sum(totals)
    decisive = turnout - (totals[abstain] if abstain < len(totals) else 0)
    winner, winner_total = NOT_VOTED, 0
    for index, total in enumerate(totals):
        if index != abstain and total > winner_total:  # a tie goes to the lower one
            winner, winner_total = index, total

    if turnout >= quorum and winner_total * threshold_den > decisive * threshold_num:
        return STATUS_PASSED, winner, turnout

    return STATUS_FAILED, NOT_VOTED, turnout


def _check_events(session_id, session, ballots, events, problems):
    """Check the session's `Voted` events against its ballot boxes.

    :param session_id: voting session identifier
    :type session_id: int
    :param session: decoded session box
    :type session: dict
    :param ballots: decoded ballot boxes by seat address
    :type ballots: dict
    :param events: the dApp's `Voted` events in chain order
    :type events: list
    :param problems: collection the problems are appended to
    :type problems: list
    :var last: each seat's latest event in this session
    :type last: dict
    """
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

    for seat in sorted(set(last) - set(ballots)):
        problems.append(f"{seat}: voted in {last[seat]['txid']}, no ballot box")
    for seat, box in sorted(ballots.items()):
        event = last.get(seat)
        if event is None:
            problems.append(f"{seat}: ballot box, no Voted event")
        elif (event["option"], event["max_votes"]) != (box["option"], box["max_votes"]):
            problems.append(
                f"{seat}: last event option {event['option']} votes "
                f"{event['max_votes']}, box option {box['option']} votes "
                f"{box['max_votes']}"
            )


def _check_weights(session, ballots, final, problems, notes):
    """Check the written weights and return the totals recomputed from the boxes.

    :param session: decoded session box
    :type session: dict
    :param ballots: decoded ballot boxes by seat address
    :type ballots: dict
    :param final: holdings rounds expected and found, sha256 hex and amount per seat
        of the final file, or None
    :type final: dict
    :param problems: collection the problems are appended to
    :type problems: list
    :param notes: collection the notes are appended to
    :type notes: list
    :return: list
    """
    totals = [0] * session["options_count"]
    for seat, box in sorted(ballots.items()):
        if not box["weighed"]:
            continue
        expected = min(box["max_votes"] * MICRO, box["eligible"])
        if box["weight"] != expected:
            problems.append(
                f"{seat}: weight {box['weight']}, the rule gives {expected}"
            )
        if box["option"] < len(totals):
            totals[box["option"]] += box["weight"]

    weighed = sum(1 for box in ballots.values() if box["weighed"])
    if weighed != session["weighed_count"]:
        problems.append(
            f"Weighed recomputed {weighed}, session {session['weighed_count']}"
        )
    if totals != session["totals"]:
        problems.append(f"Totals recomputed {totals}, session {session['totals']}")

    if final is None:
        if session["status"] != STATUS_OPEN:
            notes.append(
                "No final file given, so the weights were not checked against it"
            )
        return totals

    if final["file_rounds"] != final["rounds"]:
        problems.append(
            f"Final file covers rounds {final['file_rounds']}, the session's holdings "
            f"rounds are {final['rounds']}"
        )
    if session["status"] != STATUS_OPEN and final["hash"] != session["final_hash"]:
        problems.append("The final file's sha256 differs from the sealed hash")
    for seat, box in sorted(ballots.items()):
        amount = final["amounts"].get(seat)
        if amount is None:
            problems.append(f"{seat}: voted, not in the final file")
        elif not box["weighed"]:
            notes.append(f"{seat}: final amount not written yet")
        elif box["eligible"] != amount:
            problems.append(f"{seat}: box amount {box['eligible']}, file {amount}")
    return totals


def tally_session(
    session_id, session, ballots, final, events, votes_now, current_round
):
    """Recompute a session from public data and return what agrees and what doesn't.

    :param session_id: voting session identifier
    :type session_id: int
    :param session: decoded session box, None if there is no such session
    :type session: dict
    :param ballots: decoded ballot boxes by seat address
    :type ballots: dict
    :param final: holdings rounds expected and found, sha256 hex and amount per seat
        of the final file, or None; with the proposal's sha256 hex or None
    :type final: dict
    :param events: the dApp's `Voted` events in chain order
    :type events: list
    :param votes_now: Permission dApp votes per voter today
    :type votes_now: dict
    :param current_round: latest round of the network
    :type current_round: int
    :return: dict
    """
    problems, notes = [], []
    if session is None:
        return {"problems": ["There is no such session"], "notes": notes}

    proposal_hash = final.pop("proposal_hash", None) if final else None
    if proposal_hash not in (None, session["proposal_hash"]):
        problems.append("The proposal file's sha256 differs from the session's")
    if final is not None and "amounts" not in final:
        final = None

    _check_events(session_id, session, ballots, events, problems)
    if len(ballots) != session["voters_count"]:
        problems.append(
            f"Voters recomputed {len(ballots)}, session {session['voters_count']}"
        )
    for seat, box in sorted(ballots.items()):
        if votes_now.get(seat, 0) != box["max_votes"]:
            notes.append(
                f"{seat}: votes_of was {box['max_votes']} at its vote, "
                f"{votes_now.get(seat, 0)} now"
            )
    totals = _check_weights(session, ballots, final, problems, notes)

    status, winner, turnout = contract_result(
        totals,
        session["abstain"],
        session["quorum"],
        session["threshold_num"],
        session["threshold_den"],
    )
    if session["status"] in (STATUS_PASSED, STATUS_FAILED):
        if (session["status"], session["winner"]) != (status, winner):
            problems.append(
                f"Result recomputed {STATUS_NAMES[status]} winner {winner}, session "
                f"{STATUS_NAMES[session['status']]} winner {session['winner']}"
            )
    elif session["status"] == STATUS_SEALED:
        notes.append(f"Not finalized yet; finalize would record {STATUS_NAMES[status]}")
    elif current_round > session["end_round"]:
        notes.append(
            f"Voting has ended; {session['weighed_count']} of "
            f"{session['voters_count']} voters weighed, sealing from round "
            f"{session['seal_round']}"
        )
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
        "voters": len(ballots),
        "status": status,
        "winner": winner,
    }


# # COMMAND
def tally(network, session_id, final=None, proposal=None, clients=None):
    """Tally the session on `network`, print the report and return it.

    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :param session_id: voting session identifier
    :type session_id: int
    :param final: path to the published final file
    :type final: str
    :param proposal: path to the published proposal file
    :type proposal: str
    :param clients: algod, indexer, holdings indexer, Voting dApp and Permission
        dApp identifiers, when not the configured ones
    :type clients: tuple
    :return: dict
    """
    if clients is None:
        env = environment_variables()
        clients = (
            algod_client(env, network),
            indexer_client(env, network),
            indexer_client(env, HOLDINGS_NETWORK),
            voting_dapp_id(network=network),
            permission_dapp_id(network=network),
        )
    algod, indexer, holdings_indexer, app_id, permission_id = clients

    session = session_values(algod, app_id, session_id)
    checked = {"proposal_hash": sha256_of_file(proposal).hex() if proposal else None}
    ballots, events, votes_now = {}, [], {}
    if session is not None:
        ballots = session_ballots(algod, app_id, session_id)
        events = voted_events(
            indexer, app_id, session["start_round"], session["end_round"]
        )
        votes_now = {
            seat: permission_votes(algod, permission_id, seat) for seat in ballots
        }
        if final:
            start, end, amounts = read_final(final)
            checked.update(
                {
                    "file_rounds": (start, end),
                    "rounds": holdings_rounds(
                        session,
                        indexer,
                        holdings_indexer,
                        holdings_indexer.health()["round"],
                        network == HOLDINGS_NETWORK,
                    ),
                    "hash": sha256_of_file(final).hex(),
                    "amounts": amounts,
                }
            )

    report = tally_session(
        session_id,
        session,
        ballots,
        checked,
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
    parser.add_argument("--final")
    parser.add_argument("--proposal")
    report = tally(**vars(parser.parse_args(argv)))
    return 1 if report["problems"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
