Development
===========

Setup
-----

Due to the requirements including Algokit, the Voting dApp requires Python 3.12 or later for development and deployment.


Python environment
^^^^^^^^^^^^^^^^^^

Create Python virtual environment:

.. code-block:: bash

  python3 -m venv dao


Activate Python environment and install the requirements:

.. code-block:: bash

  source dao/bin/activate
  python -m pip install -r dapp/requirements.txt


Adding an alias can be useful:

.. code-block:: bash
  :caption: ~/.bashrc

  alias 'dao'='cd ~/dev/voting-dapp; \
    source ~/dev/venvs/dao/bin/activate'


The `puyapy` compiler is pinned to an exact version and `algorand-python` is capped
to the range that version supports. Raise them together, then recompile and review
the changes in the `artifacts` directory.


Environment variables
^^^^^^^^^^^^^^^^^^^^^

Copy provided .env.example file to a working copy:

.. code-block:: bash

  cd ~/dev/voting-dapp
  cp dapp/.env.example dapp/.env

You have to update `CREATOR_TESTNET_MNEMONIC` variable in the newly created `.env` file,
as well as the related `VOTING_APP_ID_TESTNET` application identifier in the
`configuration` module after you deploy your own dapp on Testnet. Set
`CREATOR_TESTNET_ADDRESS` only if the admin account is rekeyed.


Smart contract
--------------

All the commands are run from the repository's root.

Compile
^^^^^^^

After you activated the Python environment, you can compile smart contract by issuing
the following command:

.. code-block:: bash

  algokit compile py dapp/contract.py --out-dir artifacts


Deploy and fund Voting dApp
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Issue the following command to deploy the generated smart contract
on the blockchain and fund the related account with required ALGO:

.. code-block:: bash

  python -m dapp.deploy

The application account pays the minimum balance of every session and ballot box.


Tests
-----

Python
^^^^^^

The integration tests deploy the Voting dApp and the Permission dApp to LocalNet,
so it has to be started first. The tests are run from the `dapp` directory or from
the repository's root:

.. code-block:: bash

  cd ~/dev/voting-dapp/dapp
  source ~/dev/venvs/dao/bin/activate
  algokit localnet start
  python -m pytest -v  # or just `pytest -v`

The test of a whole voting session on Testnet isn't a part of that run, as every run
leaves a session and a ballot box on Testnet. It needs the `USER_TESTNET_MNEMONIC`
variable set to a funded account, the governor seat that votes:

.. code-block:: bash

  python -m pytest -m testnet --no-cov

The Permission dApp's compiled program in `dapp/tests/artifacts` is a copy from the
`permission-dapp` repository. Refresh it whenever that smart contract is recompiled.
