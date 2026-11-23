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
  `votes_of` method at the moment of voting
* the seat's eligible ASASTATS at the session's snapshot round, measured off-chain
  and written to the session by the admin before the voting opens

A wrong snapshot number can therefore never give a seat more than its allocation.


Voting session
^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1

   * - Step
     - Method
     - Who
   * - Create the session: rounds, options, quorum, threshold, proposal hash
     - `create_session`
     - admin
   * - Write the snapshot, and correct it during the review period
     - `set_snapshot`
     - admin
   * - Seal it with the hash of the published snapshot file
     - `seal_snapshot`
     - admin
   * - Vote, or change the vote, from the start round to the end round
     - `vote`
     - every seat in the snapshot
   * - Record the result after the end round
     - `finalize`
     - anyone

A session passes when the turnout reaches the quorum and the leading option has
more than `threshold_num / threshold_den` of the turnout.


Boxes and events
^^^^^^^^^^^^^^^^

The session box is named by `s` followed by the session identifier (8 bytes) and
holds 187 bytes. A ballot box is named by `v` followed by the session identifier
and the seat's public key (32 bytes) and holds 25 bytes. The fields are those of
:class:`dapp.contract.Session` and :class:`dapp.contract.Ballot`.

Every vote logs a `Voted` event and `finalize` logs a `Finalized` event (ARC-28).


Snapshot file
^^^^^^^^^^^^^

.. code-block:: json

    {
      "snapshot_round": 55000000,
      "asset_id": 393537671,
      "seats": {
        "<seat address>": {
          "total": 1500000,
          "venues": {"wallet": 1000000, "<pool>": 500000}
        }
      }
    }

Amounts are micro-ASASTATS. The scripts read `snapshot_round` and each seat's
`total`; the venue lines are there for the governors to check. The sha256 of the
file as published goes on-chain at the seal.
