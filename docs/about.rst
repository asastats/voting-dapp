About
=====

This repository contains code for the ASA Stats Voting dApp, the smart contract
the `ASA Stats DAO governors <https://github.com/asastats/dao-discussion/discussions>`_
vote with.

The dApp is in development and isn't deployed yet. The counting rule described
below is a working assumption until the DAO confirms it.

Besides the smart contract code found in the `artifacts` directory, this repository
contains the scripts for deploying the dApp and for running voting sessions.


Structure
---------

.. code-block:: bash

    dapp/
    ├── artifacts/
    ├── tests/
    ├── configuration.py
    ├── contract.py
    ├── deploy.py
    ├── helpers.py
    ├── network.py
    ├── sessions.py
    └── tally.py


Smart contract
^^^^^^^^^^^^^^

Module `contract` contains Voting dApp's
`Algorand Python <https://github.com/algorandfoundation/puya>`_ application code.
After that code is compiled (see :doc:`development`), the smart contract is converted to
`TEAL <https://dev.algorand.co/concepts/smart-contracts/languages/teal/>`_
and placed inside the `artifacts` directory.

The deployment of the smart contract is done by running `python -m dapp.deploy`
from the repository's root.


Vote weight
^^^^^^^^^^^

A governor's weight in a session, in micro-units, is the lower of two values:

* the seat's votes multiplied by 10⁶, read from the
  `Permission dApp <https://github.com/asastats/permission-dapp>`_ by calling its
  `votes_of` method when the seat votes
* the lowest ASASTATS the seat held from the start round to the end round, measured
  off-chain after the end and written to the session by the admin

Abstain, when the session has one, counts toward the turnout and the quorum but can't
win and isn't part of the total the winner must beat.


Voting session
^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1

   * - Step
     - Method
     - Who
   * - Create the session: rounds, options, Abstain, quorum, threshold, proposal hash
     - `create_session`
     - admin
   * - Vote, or change the vote, from the start round to the end round
     - `vote`
     - any seat with votes
   * - After the end round: write each voter's lowest amount, correct it during the review
     - `set_final`
     - admin
   * - From the seal round: seal with the hash of the published final file
     - `seal_final`
     - admin
   * - Record the result
     - `finalize`
     - anyone

A session passes when the turnout reaches the quorum and the leading option other than
Abstain has more than `threshold_num / threshold_den` of the turnout without Abstain.


Boxes and events
^^^^^^^^^^^^^^^^

The session box is named by `s` followed by the session identifier (8 bytes) and
holds 196 bytes. A ballot box is named by `v` followed by the session identifier
and the seat's public key (32 bytes), holds 26 bytes and is created by the seat's
first vote. The fields are those of :class:`dapp.contract.Session` and
:class:`dapp.contract.Ballot`.

Every vote logs a `Voted` event and `finalize` logs a `Finalized` event (ARC-28).


Final file
^^^^^^^^^^

.. code-block:: json

    {
      "start_round": 55000000,
      "end_round": 55210000,
      "asset_id": 393537671,
      "seats": {
        "<seat address>": {
          "start_total": 1600000,
          "total": 1500000,
          "minimum_round": 55100000,
          "venues": {"wallet": 1000000, "<pool>": 500000}
        }
      }
    }

Amounts are micro-ASASTATS. The scripts read the two rounds and each seat's `total`,
the lowest amount; the rest is there for the governors to check. The rounds are
mainnet rounds: for a testnet session, those at the session's start and end times.
The sha256 of the file as published goes on-chain at the seal.
