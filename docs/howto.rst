Howto
=====

Run a voting session
--------------------

All the commands are run from the repository's root as the dApp's admin.

Create a session for a proposal file, with the snapshot, start and end rounds:

.. code-block:: bash

    python -m dapp.sessions create testnet proposal.md 55000000 55250000 55500000

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
