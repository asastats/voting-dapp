"""Module with functions for deploying Voting dApp smart contract to blockchain."""

import json

from algosdk.atomic_transaction_composer import sign_transaction_with_signer
from algosdk.logic import get_application_address
from algosdk.transaction import PaymentTxn
from algosdk.v2client.algod import AlgodClient

from .helpers import (
    ARTIFACTS,
    DAPP_NAME,
    admin_parameters,
    compile_program,
    environment_variables,
    permission_dapp_id,
    read_json,
)
from .network import create_app


def algod_client(env, network):
    """Return Algorand Node client for the provided `network`.

    :param env: environment variables collection
    :type env: dict
    :param network: network to connect to (e.g., "testnet")
    :type network: str
    :return: :class:`AlgodClient`
    """
    return AlgodClient(
        env.get(f"algod_token_{network}"), env.get(f"algod_address_{network}")
    )


def deploy_app(network="testnet"):
    """Create the Voting dApp on `network` and record its ID in the ARC-56 artifact.

    :param network: network to deploy to (e.g., "testnet")
    :type network: str
    :var contract_json: ARC-56 smart contract specification
    :type contract_json: dict
    :var app_id: ID of the newly created application
    :type app_id: int
    :var genesis_hash: genesis hash of the network
    :type genesis_hash: str
    :return: int
    """
    env = environment_variables()
    client = algod_client(env, network)
    contract_json_path = ARTIFACTS / f"{DAPP_NAME}.arc56.json"
    contract_json = read_json(contract_json_path)

    app_id, genesis_hash = create_app(
        client,
        admin_parameters(env, network=network),
        compile_program(
            client, (ARTIFACTS / f"{DAPP_NAME}.approval.teal").read_bytes()
        ),
        compile_program(client, (ARTIFACTS / f"{DAPP_NAME}.clear.teal").read_bytes()),
        contract_json,
        permission_dapp_id(network=network),
    )

    contract_json.setdefault("networks", {})[genesis_hash] = {"appID": app_id}
    with open(contract_json_path, "w") as json_file:
        json.dump(contract_json, json_file, indent=4)

    print("Created new app id: ", app_id)
    return app_id


def fund_app(app_id, network="testnet", amount=5_000_000):
    """Send `amount` microAlgos from the admin to the application's account.

    The application account pays the minimum balance of every session and
    ballot box.

    :param app_id: Voting dApp identifier
    :type app_id: int
    :param network: network where the app is deployed (e.g., "testnet")
    :type network: str
    :param amount: amount in microAlgos to send
    :type amount: int
    :var parameters: sender and signer of the admin
    :type parameters: dict
    :return: str
    """
    env = environment_variables()
    client = algod_client(env, network)
    parameters = admin_parameters(env, network=network)
    txn = PaymentTxn(
        sender=parameters["sender"],
        sp=client.suggested_params(),
        receiver=get_application_address(app_id),
        amt=amount,
    )
    signed_txn = sign_transaction_with_signer(txn, parameters["signer"])
    tx_id = client.send_transaction(signed_txn)
    print(f"Funded app {app_id} with {amount / 1_000_000} Algo in transaction {tx_id}")
    return tx_id


if __name__ == "__main__":  # pragma: no cover
    fund_app(deploy_app(network="testnet"), network="testnet")
