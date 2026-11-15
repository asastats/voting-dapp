"""Testing module for :py:mod:`dapp.deploy` module."""

import json

from algosdk.account import generate_account
from algosdk.atomic_transaction_composer import AccountTransactionSigner
from algosdk.logic import get_application_address

from dapp import deploy


class TestDeploy:
    """Testing class for :py:mod:`dapp.deploy` functions."""

    def test_deploy_algod_client(self, mocker):
        mocked_client = mocker.patch("dapp.deploy.AlgodClient")
        env = {"algod_token_testnet": "token", "algod_address_testnet": "address"}
        assert deploy.algod_client(env, "testnet") == mocked_client.return_value
        mocked_client.assert_called_once_with("token", "address")

    def test_deploy_deploy_app_records_app_id(self, mocker, tmp_path):
        for name in ("approval.teal", "clear.teal"):
            (tmp_path / f"VotingDApp.{name}").write_text(name)
        (tmp_path / "VotingDApp.arc56.json").write_text('{"name": "VotingDApp"}')
        mocker.patch("dapp.deploy.ARTIFACTS", tmp_path)
        mocker.patch("dapp.deploy.environment_variables", return_value={})
        client = mocker.patch("dapp.deploy.algod_client").return_value
        parameters = mocker.patch("dapp.deploy.admin_parameters").return_value
        mocker.patch(
            "dapp.deploy.compile_program", side_effect=lambda _, source: source[:3]
        )
        mocked_create = mocker.patch(
            "dapp.deploy.create_app", return_value=(505, "genesis")
        )

        returned = deploy.deploy_app(network="mainnet")

        assert returned == 505
        mocked_create.assert_called_once_with(
            client, parameters, b"app", b"cle", mocker.ANY, 3647489997
        )
        assert json.loads((tmp_path / "VotingDApp.arc56.json").read_text()) == {
            "name": "VotingDApp",
            "networks": {"genesis": {"appID": 505}},
        }

    def test_deploy_fund_app_pays_the_application_account(self, mocker):
        private_key, address = generate_account()
        mocker.patch("dapp.deploy.environment_variables", return_value={})
        client = mocker.patch("dapp.deploy.algod_client").return_value
        client.suggested_params.return_value = mocker.MagicMock(
            fee=1000, first=1, last=2, gh="gh", gen="gen", flat_fee=True, min_fee=1000
        )
        signer = AccountTransactionSigner(private_key)
        mocker.patch(
            "dapp.deploy.admin_parameters",
            return_value={"sender": address, "signer": signer},
        )
        mocked_txn = mocker.patch("dapp.deploy.PaymentTxn")
        mocked_sign = mocker.patch("dapp.deploy.sign_transaction_with_signer")

        returned = deploy.fund_app(505, amount=7)

        mocked_txn.assert_called_once_with(
            sender=address,
            sp=client.suggested_params.return_value,
            receiver=get_application_address(505),
            amt=7,
        )
        mocked_sign.assert_called_once_with(mocked_txn.return_value, signer)
        client.send_transaction.assert_called_once_with(mocked_sign.return_value)
        assert returned == client.send_transaction.return_value
