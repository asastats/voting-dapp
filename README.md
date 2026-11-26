# ASA Stats Voting dApp

[![build-status](https://github.com/asastats/voting-dapp/actions/workflows/build.yml/badge.svg)](https://github.com/asastats/voting-dapp/actions/workflows/build.yml) [![docs](https://img.shields.io/readthedocs/asastats/latest)](https://voting-dapp.readthedocs.io/en/latest/?badge=latest) [![codecov](https://codecov.io/gh/asastats/voting-dapp/graph/badge.svg?token=DQC4SRY8J9)](https://codecov.io/gh/asastats/voting-dapp)


The smart contract and the admin scripts for on-chain voting of the ASA Stats DAO.

Status: in development. Nothing is deployed; the counting rule below is a working
assumption until the DAO confirms it.

## How a vote is counted

A governor's weight in a session is

    min(votes_of(seat) × 10⁶, lowest ASASTATS the seat held while voting was open)

in micro-units. `votes_of` is read from the
[Permission dApp](https://github.com/asastats/permission-dapp) when the seat
votes. The lowest amount is measured off-chain after the end round, from the
start round to the end round, and written by the admin; the totals exist only
once it is written. Buying during voting adds nothing, selling or moving ASASTATS
off the seat address lowers the weight. Abstain, when the session has one, counts
toward the turnout and the quorum but can't win and isn't part of the total the
winner must beat.

## A session

| Step | Method | Who |
|---|---|---|
| Create the session: rounds, options, Abstain, quorum, threshold, proposal hash | `create_session` | admin |
| Vote, or change the vote, from the start round to the end round | `vote` | any seat with votes |
| After the end round: write each voter's lowest amount, correct it during the review | `set_final` | admin |
| From the seal round: seal with the hash of the published final file | `seal_final` | admin |
| Record the result | `finalize` | anyone |

A session passes when the turnout reaches the quorum and the leading option
other than Abstain has more than `threshold_num / threshold_den` of the turnout
without Abstain.

Boxes: `s` + session id (8 bytes) holds the session, 196 bytes; `v` + session id
+ seat (32 bytes) holds a ballot, 26 bytes, created by the seat's first vote. The
fields are `Session` and `Ballot` in `dapp/contract.py`. Every vote logs a
`Voted` event and `finalize` logs `Finalized` (ARC-28).

## Final file

Written by the ASA Stats engine (`daosnapshot <start> seats.json final.json --to <end>`):

    {
      "start_round": 55000000,
      "end_round": 55210000,
      "asset_id": 393537671,
      "seats": {
        "<seat address>": {"start_total": 1600000, "total": 1500000, "minimum_round": 55100000,
                           "venues": {"wallet": 1000000, "<pool>": 500000}}
      }
    }

Amounts are micro-ASASTATS. The scripts read the two rounds and each seat's
`total` (the lowest amount); the rest is there for the governors to check. The
rounds are those of the network holding ASASTATS, mainnet: for a testnet session
they are the mainnet rounds at the session's start and end times, which
`sessions period` prints. The sha256 of the file as published goes on-chain at
the seal. The same command up to the latest round gives a "so far" file during
voting, for showing totals that can only go down.

## Usage

Python 3.12 or newer. All commands run from the repository's root.

    python3 -m venv venv
    source venv/bin/activate
    python -m pip install -r dapp/requirements.txt
    cp dapp/.env.example dapp/.env

Compile the contract after any change to `dapp/contract.py`:

    algokit compile py dapp/contract.py --out-dir artifacts

Run the tests, from the `dapp` directory or from the root; the integration tests
need localnet:

    algokit localnet start
    cd dapp
    python -m pytest

The test of a whole session on testnet is left out of that run, because every
run leaves a session and a ballot box there. It needs `USER_TESTNET_MNEMONIC`,
a funded account that votes, and takes about two minutes:

    python -m pytest -m testnet --no-cov

Deploy to testnet and fund the application account, which pays for the boxes
(set the returned ID in `dapp/configuration.py`):

    python -m dapp.deploy

Run a session. The three numbers are the start, end and seal rounds: the start
has to be ahead of the latest round, which `round` prints, and the seal round,
after the review of the final file, follows the end. A day is about 31,900 rounds
on testnet.

    python -m dapp.sessions round testnet
    python -m dapp.sessions create testnet proposal.md 69760000 69984000 70048000
    python -m dapp.sessions period testnet 1 --so-far   # during voting
    python -m dapp.sessions period testnet 1            # after the end round
    python -m dapp.sessions final testnet 1 final.json
    python -m dapp.sessions seal-final testnet 1 final.json
    python -m dapp.sessions close testnet 1
    python -m dapp.sessions show testnet 1

`period` prints the engine command, with the holdings rounds, that writes the
so-far or the final file. `final` writes only the voters whose box differs from
the file, so it is run again after a correction; it refuses a file for other
rounds or one that misses a voter. `seal-final` refuses while any voter's box
differs from the file.

`create` takes `--options-count` (2–8, default 3), `--abstain N` (default the
last option) or `--no-abstain`, `--quorum` (micro-votes, default 0) and
`--threshold NUM DEN` (default `1 2`; at least a half).

Anyone can tally a session independently from public data (algod, the indexers of
the session's network and of mainnet, and the published files); it prints a
`PROBLEM` line for anything that differs from the chain and exits with status 1:

    python -m dapp.tally testnet 1 --final final.json --proposal proposal.md

Why things are the way they are: `docs/logbook.md`.
