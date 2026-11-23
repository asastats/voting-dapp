Howto
=====

Run a voting session
--------------------

All the commands are run from the repository's root as the dApp's admin.

Create a session for a proposal file, with the snapshot, start and end rounds. The
snapshot round comes before the start round, and the start round has to be ahead of
the network's latest round, which the `round` command prints. A day is about 30,000
rounds.

.. code-block:: bash

    python -m dapp.sessions round testnet
    python -m dapp.sessions create testnet proposal.md 69290000 69500000 69710000

A call the smart contract rejects ends with the contract's own message, for example
`Start round must be in the future`.

The command takes `--options-count` (2–8, default 3), `--quorum` (micro-votes,
default 0) and `--threshold NUM DEN` (default `1 2`; at least a half).

Write the snapshot file to the session, and seal it after the review period:

.. code-block:: bash

    python -m dapp.sessions snapshot testnet 1 snapshot.json
    python -m dapp.sessions seal testnet 1 snapshot.json

The `snapshot` command writes only the seats whose box differs from the file, so it
is run again after a correction. The `seal` command refuses while any box differs
from the file.

Show the session and its ballots, and record the result after the end round:

.. code-block:: bash

    python -m dapp.sessions show testnet 1
    python -m dapp.sessions close testnet 1


Tally a session independently
-----------------------------

Anyone can recompute a session from public data, without the admin's keys. The
command reads the session and ballot boxes and the Permission dApp boxes from algod,
and the `Voted` events from the indexer (`INDEXER_ADDRESS_TESTNET` /
`INDEXER_ADDRESS_MAINNET`, `testnet-idx.algonode.cloud` / `mainnet-idx.algonode.cloud` by default):

.. code-block:: bash

    python -m dapp.tally testnet 1 snapshot.json --proposal proposal.md

It checks the files against the session's hashes and snapshot round, every ballot box
against the snapshot file, every vote against the counting rule and against the seat's
last `Voted` event, and the totals, voter count and result against the session. It
prints a `PROBLEM` line for anything that differs and exits with status 1, or ends with
`Everything agrees`. A seat whose `votes_of` changed after its vote is a `NOTE`: the
contract reads `votes_of` when the seat votes.


Add content
-----------

Add your content using ``reStructuredText`` syntax. See the
`reStructuredText <https://www.sphinx-doc.org/en/master/usage/restructuredtext/index.html>`_
documentation for details.


Create documentation
--------------------

Activate the project's Python environment and create the documentation by invoking a simple `make` command:

.. code-block:: bash

    cd docs
    make html
    # make latexpdf

The created docs are placed in the `docs/_build` directory.

The documentation of this project is created and hosted automatically through the
`Read the Docs <https://about.readthedocs.com>`_ platform.
