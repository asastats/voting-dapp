Howto
=====

Run a voting session
--------------------

All the commands are run from the repository's root as the dApp's admin.

Create a session for a proposal file, with the start, end and seal rounds. The start
round has to be ahead of the network's latest round, which the `round` command prints,
and the seal round follows the end round by the review of the final file. A day is
about 31,900 rounds on testnet.

.. code-block:: bash

    python -m dapp.sessions round testnet
    python -m dapp.sessions create testnet proposal.md 69760000 69984000 70048000

A call the smart contract rejects ends with the contract's own message, for example
`Start round must be in the future`.

The command takes `--options-count` (2–8, default 3), `--abstain N` (default the last
option) or `--no-abstain`, `--quorum` (micro-votes, default 0) and `--threshold NUM DEN`
(default `1 2`; at least a half).

During voting, print the engine command for a "so far" file; after the end round, the
one for the final file:

.. code-block:: bash

    python -m dapp.sessions period testnet 1 --so-far
    python -m dapp.sessions period testnet 1

Write the final file's amounts after the end round, and seal them from the seal round:

.. code-block:: bash

    python -m dapp.sessions final testnet 1 final.json
    python -m dapp.sessions seal-final testnet 1 final.json

The `final` command writes only the voters whose box differs from the file, so it is
run again after a correction. The `seal-final` command refuses while any voter's box
differs from the file.

Show the session and its ballots, and record the result once sealed:

.. code-block:: bash

    python -m dapp.sessions show testnet 1
    python -m dapp.sessions close testnet 1


Tally a session independently
-----------------------------

Anyone can recompute a session from public data, without the admin's keys. The
command reads the session and ballot boxes and the Permission dApp boxes from algod,
the `Voted` events from the indexer, and the mainnet indexer for the holdings rounds
(`INDEXER_ADDRESS_TESTNET` / `INDEXER_ADDRESS_MAINNET`, `testnet-idx.algonode.cloud` /
`mainnet-idx.algonode.cloud` by default):

.. code-block:: bash

    python -m dapp.tally testnet 1 --final final.json --proposal proposal.md

It checks the files against the session's hashes and holdings rounds, every vote against
the seat's last `Voted` event, every written weight against the counting rule and the
final file, and the totals, counts and result against the session. It prints a
`PROBLEM` line for anything that differs and exits with status 1, or ends with
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
