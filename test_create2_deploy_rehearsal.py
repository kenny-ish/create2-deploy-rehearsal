import contextlib
import io
import json
import unittest
from unittest.mock import patch

import create2_deploy_rehearsal as tool


# Ethereum mainnet block 17,000,000 returned by eth_getBlockByNumber(0x1036640, false).
# The factory runtime below is the code returned at the canonical deterministic
# deployment proxy address on Ethereum mainnet.
BLOCK = {
    "number": "0x1036640",
    "hash": "0x96cfa0fb5e50b0a3f6cc76f3299cfbf48f17e8b41798d1394474e67ec8a97e9f",
}
BLOCK_TAG = "0x1036640"
RECENT_BLOCK_TAG = "0x18f3664"
RECENT_BLOCK = {
    "number": RECENT_BLOCK_TAG,
    "hash": "0x78dcf12c25c2261e9ca3b904b18b668d38d8c8b224020c0f44f1bb6d73966fe3",
}
FACTORY = "0xdeadbeef" + "00" * 16
LIVE_FACTORY = "0x4e59b44847b379578588920ca78fbf26c0b4956c"
LIVE_INIT_CODE = "0x6001600c60003960016000f300"
LIVE_SALT_OLD = "0x" + "00" * 31 + "01"
LIVE_SALT_RECENT = "0x" + "00" * 29 + "c0ffee"
LIVE_OLD_ADDRESS = "0x6de9c5fa3955213e98d5adc9ecbecb5d08076f8d"
LIVE_RECENT_ADDRESS = "0xb7b2e63b5d13682a242a4c0720af364c23127d0d"
SENDER = "0x" + "aa" * 20
SALT = "0x" + "00" * 32
INIT_CODE = "0x00"
EXPECTED = "0xb928f69bb1d91cd65274e3c79d8986362984fda3"


class FakeRpc:
    def __init__(self, nonce="0x0", deployed_code="0x", call_result=EXPECTED,
                 call_error=None):
        self.calls = []
        self.nonce = nonce
        self.deployed_code = deployed_code
        self.call_result = call_result
        self.call_error = call_error

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "eth_getBlockByNumber":
            if params != ["0x1036640", False]:
                raise AssertionError(params)
            return BLOCK.copy()
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_getCode":
            if params[0] == FACTORY:
                result = tool.FACTORY_RUNTIME
            elif params[0] == EXPECTED:
                result = self.deployed_code
            else:
                raise AssertionError(params[0])
            if params[1] != BLOCK_TAG:
                raise AssertionError(params[1])
            return result
        if method == "eth_getTransactionCount":
            if params != [EXPECTED, BLOCK_TAG]:
                raise AssertionError(params)
            return self.nonce
        if method == "eth_call":
            if len(params) != 2 or params[1] != BLOCK_TAG:
                raise AssertionError(params)
            expected_tx = {
                "to": FACTORY,
                "from": SENDER,
                "data": SALT + "00",
                "value": "0x0",
                "gas": "0x4c4b40",
            }
            if params[0] != expected_tx:
                raise AssertionError(params[0])
            if self.call_error is not None:
                raise self.call_error
            return self.call_result
        raise AssertionError(f"unexpected RPC method: {method}")


def fixture_report(fake=None):
    return tool.rehearse_deployment(
        fake or FakeRpc(), FACTORY, SENDER, SALT, INIT_CODE,
        block="0x1036640",
    )


class EncodingTests(unittest.TestCase):
    def setUp(self):
        self.network = patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network access is forbidden in tests"),
        )
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_keccak_known_values_and_complete_tables(self):
        self.assertEqual(
            tool.keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            tool.keccak256(b"withdraw(uint256)").hex(),
            "2e1a7d4d13322e7b96f9a57413e1525c250fb7a9021cf91d1540d5b69f16a49f",
        )
        self.assertEqual(len(tool.ROTATIONS), 25)
        self.assertEqual(len(tool.ROUND_CONSTANTS), 24)

    def test_eip1014_create2_vectors(self):
        # https://eips.ethereum.org/EIPS/eip-1014#examples
        zero_address = "0x" + "00" * 20
        last_address = "0x" + "00" * 16 + "deadbeef"
        feed_salt = "0x" + "00" * 12 + "feed" + "00" * 18
        cafe_salt = "0x" + "00" * 28 + "cafebabe"
        cases = [
            (zero_address, SALT, "0x00", "4d1a2e2bb4f88f0250f26ffff098b0b30b26bf38"),
            ("0xdeadbeef" + "00" * 16, SALT, "0x00",
             "b928f69bb1d91cd65274e3c79d8986362984fda3"),
            ("0xdeadbeef" + "00" * 16, feed_salt, "0x00",
             "d04116cdd17bebe565eb2422f2497e06cc1c9833"),
            (zero_address, SALT, "0xdeadbeef",
             "70f2b2914a2a4b783faefb75f459a580616fcb5e"),
            (last_address, cafe_salt, "0xdeadbeef",
             "60f3f640a8508fc6a86d45df051962668e1e8ac7"),
            (last_address, cafe_salt, "0x" + "deadbeef" * 11,
             "1d8bfdc5d46dc4f61d6b6115972536ebe6a8854c"),
            (zero_address, SALT, "0x",
             "e33c0c7f7df4809055c3eb a6c09cfe4baf1bd9e0".replace(" ", "")),
        ]
        for factory, salt, init_code, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(tool.create2_address(factory, salt, init_code), "0x" + expected)

    def test_two_real_block_hash_fixtures_are_pinned(self):
        for block in (
            BLOCK,
            RECENT_BLOCK,
        ):
            with self.subTest(block=block["number"]):
                fake = FakeRpc()
                fake_block = dict(block)

                def response(method, params):
                    if method == "eth_getBlockByNumber":
                        if params != [fake_block["number"], False]:
                            raise AssertionError(params)
                        return fake_block
                    if method == "eth_chainId":
                        return "0x1"
                    raise AssertionError(method)

                number, info = tool.pin_block(response, block["number"])
                self.assertEqual(number, block["number"])
                self.assertEqual(info["block_hash"], block["hash"])

    def test_live_case_predictions_are_fixed(self):
        self.assertEqual(
            tool.create2_address(LIVE_FACTORY, LIVE_SALT_OLD, LIVE_INIT_CODE),
            LIVE_OLD_ADDRESS,
        )
        self.assertEqual(
            tool.create2_address(LIVE_FACTORY, LIVE_SALT_RECENT, LIVE_INIT_CODE),
            LIVE_RECENT_ADDRESS,
        )

    def test_canonical_factory_runtime_is_complete(self):
        self.assertEqual(len(bytes.fromhex(tool.FACTORY_RUNTIME[2:])), 69)
        self.assertEqual(
            tool.FACTORY_RUNTIME,
            "0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe0"
            "3601600081602082378035828234f58015156039578182fd5b8082525050506014600cf3",
        )

    def test_hex_and_integer_validation(self):
        for value in ("0x0", "broken", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tool.hex_data(value)
        for value in (-1, True, 1.5, "-1", 2**256):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tool.integer(value)
        self.assertEqual(tool.quantity("16"), "0x10")


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.network = patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network access is forbidden in tests"),
        )
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_success_uses_pinned_block_and_eth_call_only(self):
        fake = FakeRpc()
        report = fixture_report(fake)
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["predicted_address"], EXPECTED)
        self.assertEqual(report["returned_address"], EXPECTED)
        self.assertEqual(report["existing_nonce"], 0)
        self.assertFalse(report["existing_code"])
        self.assertEqual([method for method, _ in fake.calls].count("eth_call"), 1)

    def test_nonce_collision_stops_before_eth_call(self):
        fake = FakeRpc(nonce="0x1")
        report = fixture_report(fake)
        self.assertEqual(report["status"], "collision")
        self.assertFalse(any(method == "eth_call" for method, _ in fake.calls))

    def test_code_collision_stops_before_eth_call(self):
        report = fixture_report(FakeRpc(deployed_code="0x00"))
        self.assertEqual(report["status"], "collision")

    def test_factory_revert_is_reported(self):
        report = fixture_report(
            FakeRpc(call_error=tool.RpcError(3, "execution reverted", "0xdeadbeef"))
        )
        self.assertEqual(report["status"], "reverted")
        self.assertEqual(report["factory_revert_data"], "0xdeadbeef")

    def test_unknown_revert_data_is_preserved_as_unknown(self):
        report = fixture_report(
            FakeRpc(call_error=tool.RpcError(3, "execution reverted"))
        )
        self.assertEqual(report["status"], "reverted")
        self.assertIsNone(report["factory_revert_data"])

    def test_non_revert_error_is_not_hidden(self):
        with self.assertRaises(tool.RpcError):
            fixture_report(FakeRpc(call_error=tool.RpcError(-32000, "execution failed")))

    def test_wrong_factory_return_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unexpected address"):
            fixture_report(FakeRpc(call_result="0x" + "00" * 20))

    def test_missing_factory_code_fails_closed(self):
        fake = FakeRpc()

        def missing_factory(method, params):
            if method == "eth_getCode" and params[0] == FACTORY:
                return "0x"
            return fake(method, params)

        with self.assertRaisesRegex(ValueError, "factory runtime"):
            tool.rehearse_deployment(
                missing_factory, FACTORY, SENDER, SALT, INIT_CODE,
                block=BLOCK_TAG,
            )

    def test_invalid_input_is_rejected_before_rpc(self):
        fake = FakeRpc()
        with self.assertRaises(ValueError):
            tool.rehearse_deployment(fake, FACTORY, SENDER, "0x00", INIT_CODE)
        with self.assertRaises(ValueError):
            tool.rehearse_deployment(fake, FACTORY, SENDER, SALT, INIT_CODE, gas=0)
        self.assertEqual(fake.calls, [])

    def test_cli_fixture_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = tool.main(
                [
                    "--rpc", "https://fixture.invalid",
                    "--factory", FACTORY,
                    "--from", SENDER,
                    "--salt", SALT,
                    "--init-code", INIT_CODE,
                    "--block", BLOCK_TAG,
                ],
                rpc=FakeRpc(),
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "success")


class RpcTests(unittest.TestCase):
    def test_user_agent_is_sent_and_write_methods_are_rejected(self):
        def response(request, timeout):
            self.assertEqual(request.get_header("User-agent"), "create2-deploy-rehearsal/1.0")
            body = json.loads(request.data)
            return io.BytesIO(json.dumps({
                "jsonrpc": "2.0", "id": body["id"], "result": "0x1",
            }).encode())

        with patch("urllib.request.urlopen", side_effect=response):
            rpc = tool.Rpc("https://fixture.invalid")
            self.assertEqual(rpc("eth_chainId", []), "0x1")
            with self.assertRaises(ValueError):
                rpc("eth_sendRawTransaction", [])

    def test_bad_json_rpc_envelope_is_rejected(self):
        reply = {"jsonrpc": "2.0", "id": 999, "result": "0x1"}
        with patch(
            "urllib.request.urlopen",
            return_value=io.BytesIO(json.dumps(reply).encode()),
        ):
            with self.assertRaises(ValueError):
                tool.Rpc("https://fixture.invalid")("eth_chainId", [])


if __name__ == "__main__":
    unittest.main()
