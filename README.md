# ASA Stats Voting dApp

The smart contract and the admin scripts for on-chain voting of the ASA Stats DAO.

Status: in development. Nothing is deployed; the counting rule below is a working
assumption until the DAO confirms it.

## How a vote is counted

A governor's weight in a session is

    min(votes_of(seat) × 10⁶, eligible ASASTATS of the seat at the snapshot)

in micro-units. `votes_of` is read from the
[Permission dApp](https://github.com/asastats/permission-dapp) at the moment of
voting, so a wrong snapshot number can never give a seat more than its allocation.
The eligible amount is measured off-chain at one announced snapshot round and
written to the session by the admin before the voting opens.

## A session

| Step | Method | Who |
|---|---|---|
| Create the session: rounds, options, quorum, threshold, proposal hash | `create_session` | admin |
| Write the snapshot, and correct it during the review period | `set_snapshot` | admin |
| Seal it with the hash of the published snapshot file | `seal_snapshot` | admin |
| Vote, or change the vote, from the start round to the end round | `vote` | every seat in the snapshot |
| Record the result after the end round | `finalize` | anyone |

A session passes when the turnout reaches the quorum and the leading option has
more than `threshold_num / threshold_den` of the turnout.

Boxes: `s` + session id (8 bytes) holds the session, 187 bytes; `v` + session id
+ seat (32 bytes) holds a ballot, 25 bytes. The fields are `Session` and `Ballot`
in `dapp/contract.py`. Every vote logs a `Voted` event and `finalize` logs
`Finalized` (ARC-28).

## Snapshot file

    {
      "snapshot_round": 55000000,
      "asset_id": 393537671,
      "seats": {
        "<seat address>": {"total": 1500000, "venues": {"wallet": 1000000, "<pool>": 500000}}
      }
    }

Amounts are micro-ASASTATS. The scripts read `snapshot_round` and each seat's
`total`; the venue lines are there for the governors to check. The sha256 of the
file as published goes on-chain at the seal.

## Usage

Python 3.12 or newer. All commands run from the repository's root.

    python3 -m venv venv
    source venv/bin/activate
    python -m pip install -r dapp/requirements.txt
    cp dapp/.env.example dapp/.env

Compile the contract after any change to `dapp/contract.py`:

    algokit compile py dapp/contract.py --out-dir artifacts

Run the tests; the integration tests need localnet:

    algokit localnet start
    python -m pytest

Deploy to testnet and fund the application account, which pays for the boxes
(set the returned ID in `dapp/configuration.py`):

    python -m dapp.deploy

Run a session:

    python -m dapp.sessions create testnet proposal.md 55000000 55250000 55500000
    python -m dapp.sessions snapshot testnet 1 snapshot.json
    python -m dapp.sessions seal testnet 1 snapshot.json
    python -m dapp.sessions show testnet 1
    python -m dapp.sessions close testnet 1

`snapshot` writes only the seats whose box differs from the file, so it is run
again after a correction. `seal` refuses while any box differs from the file.

`create` takes `--options-count` (2–8, default 3), `--quorum` (micro-votes,
default 0) and `--threshold NUM DEN` (default `1 2`; at least a half).

Why things are the way they are: `docs/logbook.md`.
