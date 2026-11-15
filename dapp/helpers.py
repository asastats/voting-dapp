"""Module with Voting dApp helpers functions."""

import base64
import hashlib
import json
import os
from pathlib import Path

from algosdk.abi.contract import Contract
from algosdk.account import address_from_private_key
from algosdk.atomic_transaction_composer import AccountTransactionSigner
from algosdk.encoding import decode_address
from algosdk.mnemonic import to_private_key
from algosdk.transaction import StateSchema
from dotenv import load_dotenv

from .configuration import (
    BALLOT_LAYOUT,
    PERMISSION_APP_ID,
    PERMISSION_APP_ID_TESTNET,
    SESSION_LAYOUT,
    VOTING_APP_ID,
    VOTING_APP_ID_TESTNET,
)

DAPP_NAME = "VotingDApp"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


# # BOXES
def _decode(data, layout):
    """Return `data` split by `layout` into integers and, for 32 bytes, hex strings.

    :param data: raw box value
    :type data: bytes
    :param layout: field name and size pairs
    :type layout: tuple
    :return: dict
    """
    if len(data) != sum(size for _, size in layout):
        raise ValueError(f"Unexpected box size: {len(data)}")

    decoded, offset = {}, 0
    for name, size in layout:
        chunk = data[offset : offset + size]
        if name == "totals":
            decoded[name] = [
                int.from_bytes(chunk[index : index + 8], "big")
                for index in range(0, size, 8)
            ]
        elif size == 32:
            decoded[name] = chunk.hex()
        else:
            decoded[name] = int.from_bytes(chunk, "big")
        offset += size

    return decoded


def ballot_box_name(session_id, address):
    """Return the name of the box holding the ballot of `address` in a session.

    :param session_id: voting session identifier
    :type session_id: int
    :param address: governor seat address
    :type address: str
    :return: bytes
    """
    return b"v" + session_id.to_bytes(8, "big") + decode_address(address)


def decode_ballot(data):
    """Return ballot values decoded from the provided raw box value.

    :param data: raw ballot box value
    :type data: bytes
    :return: dict
    """
    return _decode(data, BALLOT_LAYOUT)


def decode_session(data):
    """Return session values decoded from the provided raw box value.

    :param data: raw session box value
    :type data: bytes
    :return: dict
    """
    session = _decode(data, SESSION_LAYOUT)
    session["totals"] = session["totals"][: session["options_count"]]
    return session


def session_box_name(session_id):
    """Return the name of the box holding the session defined by `session_id`.

    :param session_id: voting session identifier
    :type session_id: int
    :return: bytes
    """
    return b"s" + session_id.to_bytes(8, "big")


# # CONTRACT
def app_schemas(contract_json):
    """Return state schema instances for the smart contract's global and local state.

    :param contract_json: ARC-56 smart contract specification
    :type contract_json: dict
    :return: two-tuple
    """
    schema = contract_json.get("state", {}).get("schema", {})
    global_schema = schema.get("global", {})
    local_schema = schema.get("local", {})
    return (
        StateSchema(global_schema.get("ints", 0), global_schema.get("bytes", 0)),
        StateSchema(local_schema.get("ints", 0), local_schema.get("bytes", 0)),
    )


def compile_program(client, source_code):
    """Return the program compiled by the node from the provided TEAL source.

    :param client: Algorand Node client instance
    :type client: :class:`AlgodClient`
    :param source_code: approval/clear program code
    :type source_code: bytes
    :return: bytes
    """
    return base64.b64decode(client.compile(source_code.decode("utf-8"))["result"])


def load_contract():
    """Load from disk and return the Voting dApp ABI contract object.

    :return: :class:`Contract`
    """
    return Contract.undictify(read_json(ARTIFACTS / f"{DAPP_NAME}.arc56.json"))


# # SNAPSHOT
def read_snapshot(filename):
    """Return snapshot round and eligible amount per seat from a snapshot file.

    :param filename: full path to the snapshot JSON file
    :type filename: :class:`pathlib.Path`
    :var data: snapshot file's content
    :type data: dict
    :return: two-tuple
    """
    data = read_json(filename)
    if "snapshot_round" not in data or "seats" not in data:
        raise ValueError(f"Not a snapshot file: {filename}")

    return data["snapshot_round"], {
        address: seat["total"] for address, seat in data["seats"].items()
    }


def sha256_of_file(filename):
    """Return the sha256 digest of the provided file's content.

    :param filename: full path to the file
    :type filename: :class:`pathlib.Path`
    :return: bytes
    """
    return hashlib.sha256(Path(filename).read_bytes()).digest()


# # HELPERS
def admin_parameters(env, network="testnet"):
    """Return the instances needed for calling the dApp as its admin.

    The sender is the admin address; it differs from the signing key's address
    when the admin account is rekeyed.

    :param env: environment variables collection
    :type env: dict
    :param network: network suffix for environment variable keys
    :type network: str
    :return: dict
    """
    private_key = private_key_from_mnemonic(env.get(f"creator_{network}_mnemonic"))
    return {
        "sender": env.get(f"creator_{network}_address")
        or address_from_private_key(private_key),
        "signer": AccountTransactionSigner(private_key),
        "contract": load_contract(),
    }


def environment_variables():
    """Return collection of required environment variables.

    :return: dict
    """
    load_dotenv()
    return {
        "algod_token_testnet": os.getenv("ALGOD_TOKEN_TESTNET", ""),
        "algod_token_mainnet": os.getenv("ALGOD_TOKEN_MAINNET", ""),
        "algod_address_testnet": os.getenv("ALGOD_ADDRESS_TESTNET"),
        "algod_address_mainnet": os.getenv("ALGOD_ADDRESS_MAINNET"),
        "creator_testnet_mnemonic": os.getenv("CREATOR_TESTNET_MNEMONIC"),
        "creator_mainnet_mnemonic": os.getenv("CREATOR_MAINNET_MNEMONIC"),
        "creator_testnet_address": os.getenv("CREATOR_TESTNET_ADDRESS"),
        "creator_mainnet_address": os.getenv("CREATOR_MAINNET_ADDRESS"),
    }


def permission_dapp_id(network="testnet"):
    """Return Permission dApp identifier for provided `network`.

    :param network: network to return the identifier for (e.g., "testnet")
    :type network: str
    :return: int
    """
    return PERMISSION_APP_ID if network == "mainnet" else PERMISSION_APP_ID_TESTNET


def private_key_from_mnemonic(passphrase):
    """Return base64 encoded private key created from provided mnemonic `passphrase`.

    :param passphrase: collection of English words separated by spaces
    :type passphrase: str
    :return: str
    """
    return to_private_key(passphrase)


def read_json(filename):
    """Return the content of the provided JSON file, empty if it can't be read.

    :param filename: full path to JSON file
    :type filename: :class:`pathlib.Path`
    :return: dict
    """
    if os.path.exists(filename):
        with open(filename, "r") as json_file:
            try:
                return json.load(json_file)
            except json.JSONDecodeError:
                pass
    return {}


def voting_dapp_id(network="testnet"):
    """Return Voting dApp identifier for provided `network`.

    :param network: network to return the identifier for (e.g., "testnet")
    :type network: str
    :var app_id: configured application identifier
    :type app_id: int
    :return: int
    """
    app_id = VOTING_APP_ID if network == "mainnet" else VOTING_APP_ID_TESTNET
    if app_id is None:
        raise ValueError(f"Voting dApp isn't deployed on {network}!")

    return app_id
