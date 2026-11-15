"""Testing module for :py:mod:`dapp.helpers` module."""

import hashlib
import json

import pytest
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.mnemonic import from_private_key

from dapp import helpers
from dapp.configuration import PERMISSION_APP_ID, PERMISSION_APP_ID_TESTNET

ADDRESS = "2EVGZ4BGOSL3J64UYDE2BUGTNTBZZZLI54VUQQNZZLYCDODLY33UGXNSIU"


def _session_bytes(options_count=3):
    return (
        (5).to_bytes(8, "big")
        + b"\x02" * 32
        + (20).to_bytes(8, "big")
        + (30).to_bytes(8, "big")
        + bytes([options_count])
        + (9).to_bytes(8, "big")
        + (2).to_bytes(8, "big")
        + (3).to_bytes(8, "big")
        + b"\x01" * 32
        + b"\x01\xff"
        + (4).to_bytes(8, "big")
        + b"".join(value.to_bytes(8, "big") for value in (11, 12, 13, 0, 0, 0, 0, 0))
    )


class TestHelpersBoxes:
    """Testing class for the box names and values functions."""

    def test_helpers_session_box_name(self):
        assert helpers.session_box_name(258) == b"s\x00\x00\x00\x00\x00\x00\x01\x02"

    def test_helpers_ballot_box_name(self):
        returned = helpers.ballot_box_name(1, ADDRESS)
        assert returned == b"v" + (1).to_bytes(8, "big") + decode_address(ADDRESS)
        assert len(returned) == 41

    def test_helpers_decode_session(self):
        assert helpers.decode_session(_session_bytes()) == {
            "snapshot_round": 5,
            "snapshot_hash": "02" * 32,
            "start_round": 20,
            "end_round": 30,
            "options_count": 3,
            "quorum": 9,
            "threshold_num": 2,
            "threshold_den": 3,
            "proposal_hash": "01" * 32,
            "status": 1,
            "winner": 255,
            "voters_count": 4,
            "totals": [11, 12, 13],
        }

    def test_helpers_decode_ballot(self):
        data = (
            (7).to_bytes(8, "big")
            + b"\x02"
            + (5).to_bytes(8, "big")
            + (6).to_bytes(8, "big")
        )
        assert helpers.decode_ballot(data) == {
            "eligible": 7,
            "option": 2,
            "max_votes": 5,
            "weight": 6,
        }

    def test_helpers_decode_raises_for_unexpected_size(self):
        with pytest.raises(ValueError, match="Unexpected box size: 3"):
            helpers.decode_ballot(b"abc")


class TestHelpersContract:
    """Testing class for the smart contract functions."""

    def test_helpers_app_schemas(self):
        contract_json = {
            "state": {
                "schema": {
                    "global": {"ints": 2, "bytes": 1},
                    "local": {"ints": 3, "bytes": 4},
                }
            }
        }
        global_schema, local_schema = helpers.app_schemas(contract_json)
        assert (global_schema.num_uints, global_schema.num_byte_slices) == (2, 1)
        assert (local_schema.num_uints, local_schema.num_byte_slices) == (3, 4)

    def test_helpers_app_schemas_for_empty_specification(self):
        global_schema, local_schema = helpers.app_schemas({})
        assert (global_schema.num_uints, global_schema.num_byte_slices) == (0, 0)
        assert (local_schema.num_uints, local_schema.num_byte_slices) == (0, 0)

    def test_helpers_compile_program(self, mocker):
        client = mocker.MagicMock()
        client.compile.return_value = {"result": "AQID"}
        assert helpers.compile_program(client, b"source") == b"\x01\x02\x03"
        client.compile.assert_called_once_with("source")

    def test_helpers_error_message_for_failed_assert(self):
        contract_json = helpers.read_json(helpers.ARTIFACTS / "VotingDApp.arc56.json")
        entry = next(
            entry
            for entry in contract_json["sourceInfo"]["approval"]["sourceInfo"]
            if entry.get("errorMessage") == "Start round must be in the future"
        )
        exception = Exception(
            f"logic eval error: assert failed pc={entry['pc'][0]}. "
            f"Details: app=505, pc={entry['pc'][0]}, opcodes=dig 10; <; assert"
        )
        assert helpers.error_message(505, exception) == (
            "Start round must be in the future"
        )

    def test_helpers_error_message_for_unknown_pc(self):
        assert helpers.error_message(505, Exception("app=505, pc=999999")) is None

    def test_helpers_error_message_for_another_app(self):
        assert helpers.error_message(505, Exception("app=9, pc=299, opcodes")) is None

    def test_helpers_load_contract(self):
        contract = helpers.load_contract()
        assert contract.name == "VotingDApp"
        assert contract.get_method_by_name("vote").get_signature() == (
            "vote(uint64,uint64)void"
        )


class TestHelpersSnapshot:
    """Testing class for the snapshot file functions."""

    def test_helpers_read_snapshot(self, tmp_path):
        filename = tmp_path / "snapshot.json"
        filename.write_text(
            json.dumps(
                {
                    "snapshot_round": 55,
                    "asset_id": 393537671,
                    "seats": {
                        "A": {"total": 30, "venues": {"wallet": 10, "pool": 20}},
                        "B": {"total": 5, "venues": {"wallet": 5}},
                    },
                }
            )
        )
        assert helpers.read_snapshot(filename) == (55, {"A": 30, "B": 5})

    @pytest.mark.parametrize("content", ['{"seats": {}}', '{"snapshot_round": 1}', "x"])
    def test_helpers_read_snapshot_raises_for_other_files(self, tmp_path, content):
        filename = tmp_path / "snapshot.json"
        filename.write_text(content)
        with pytest.raises(ValueError, match="Not a snapshot file"):
            helpers.read_snapshot(filename)

    def test_helpers_sha256_of_file(self, tmp_path):
        filename = tmp_path / "proposal.md"
        filename.write_bytes(b"proposal")
        assert helpers.sha256_of_file(filename) == hashlib.sha256(b"proposal").digest()


class TestHelpersHelpers:
    """Testing class for the environment and identifiers functions."""

    def test_helpers_admin_parameters_for_rekeyed_admin(self):
        private_key, _ = generate_account()
        env = {
            "creator_testnet_mnemonic": from_private_key(private_key),
            "creator_testnet_address": ADDRESS,
        }
        returned = helpers.admin_parameters(env)
        assert returned["sender"] == ADDRESS
        assert returned["signer"].private_key == private_key
        assert returned["contract"].name == "VotingDApp"

    def test_helpers_admin_parameters_without_address(self):
        private_key, address = generate_account()
        env = {"creator_mainnet_mnemonic": from_private_key(private_key)}
        returned = helpers.admin_parameters(env, network="mainnet")
        assert returned["sender"] == address

    def test_helpers_environment_variables(self, mocker):
        mocker.patch("dapp.helpers.load_dotenv")
        mocker.patch.dict(
            "os.environ",
            {
                "ALGOD_ADDRESS_TESTNET": "https://testnet",
                "CREATOR_TESTNET_MNEMONIC": "words",
            },
            clear=True,
        )
        assert helpers.environment_variables() == {
            "algod_token_testnet": "",
            "algod_token_mainnet": "",
            "algod_address_testnet": "https://testnet",
            "algod_address_mainnet": None,
            "creator_testnet_mnemonic": "words",
            "creator_mainnet_mnemonic": None,
            "creator_testnet_address": None,
            "creator_mainnet_address": None,
            "user_testnet_mnemonic": None,
        }

    def test_helpers_permission_dapp_id(self):
        assert helpers.permission_dapp_id() == PERMISSION_APP_ID_TESTNET
        assert helpers.permission_dapp_id(network="mainnet") == PERMISSION_APP_ID

    def test_helpers_read_json_for_missing_file(self, tmp_path):
        assert helpers.read_json(tmp_path / "missing.json") == {}

    def test_helpers_voting_dapp_id(self, mocker):
        mocker.patch("dapp.helpers.VOTING_APP_ID", 11)
        mocker.patch("dapp.helpers.VOTING_APP_ID_TESTNET", 22)
        assert helpers.voting_dapp_id() == 22
        assert helpers.voting_dapp_id(network="mainnet") == 11

    def test_helpers_voting_dapp_id_raises_when_not_deployed(self, mocker):
        mocker.patch("dapp.helpers.VOTING_APP_ID_TESTNET", None)
        with pytest.raises(ValueError, match="isn't deployed on testnet"):
            helpers.voting_dapp_id()
