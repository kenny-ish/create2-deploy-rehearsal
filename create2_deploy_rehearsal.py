"""Rehearse a CREATE2 deployment through the deterministic deployment proxy."""

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


# Ethereum Keccak-256 uses padding 0x01. hashlib.sha3_256 uses different padding.
ROUND_CONSTANTS = tuple(int(value, 16) for value in (
    "1 8082 800000000000808a 8000000080008000 808b 80000001 "
    "8000000080008081 8000000000008009 8a 88 80008009 8000000a "
    "8000808b 800000000000008b 8000000000008089 8000000000008003 "
    "8000000000008002 8000000000000080 800a 800000008000000a "
    "8000000080008081 8000000000008080 80000001 8000000080008008"
).split())
ROTATIONS = (0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39,
             41, 45, 15, 21, 8, 18, 2, 61, 56, 14)
MASK64 = (1 << 64) - 1

FACTORY_ADDRESS = "0x4e59b44847b379578588920ca78fbf26c0b4956c"
FACTORY_RUNTIME = (
    "0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe0"
    "3601600081602082378035828234f58015156039578182fd5b8082525050506014600cf3"
)


def keccak256(data):
    """Return Ethereum's Keccak-256 digest for bytes."""
    def rotate(value, count):
        return ((value << count) | (value >> (64 - count))) & MASK64

    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(bytes((-len(padded)) % 136))
    padded[-1] |= 0x80
    state = [0] * 25

    for start in range(0, len(padded), 136):
        for index in range(17):
            begin = start + index * 8
            state[index] ^= int.from_bytes(padded[begin:begin + 8], "little")
        for round_constant in ROUND_CONSTANTS:
            columns = [
                state[x] ^ state[x + 5] ^ state[x + 10]
                ^ state[x + 15] ^ state[x + 20]
                for x in range(5)
            ]
            correction = [
                columns[(x - 1) % 5] ^ rotate(columns[(x + 1) % 5], 1)
                for x in range(5)
            ]
            moved = [0] * 25
            for y in range(5):
                for x in range(5):
                    index = x + 5 * y
                    moved[y + 5 * ((2 * x + 3 * y) % 5)] = rotate(
                        state[index] ^ correction[x], ROTATIONS[index]
                    )
            for y in range(5):
                for x in range(5):
                    state[x + 5 * y] = (
                        moved[x + 5 * y]
                        ^ ((~moved[(x + 1) % 5 + 5 * y])
                           & moved[(x + 2) % 5 + 5 * y])
                    )
            state[0] ^= round_constant

    return b"".join(value.to_bytes(8, "little") for value in state)[:32]


def hex_data(value, size=None):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        raise ValueError("expected even-length 0x-prefixed hex data")
    if size is not None and len(value) != size * 2 + 2:
        raise ValueError(f"expected {size} bytes")
    return value.lower()


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("expected an unsigned integer")
    if isinstance(value, str):
        if not re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", value):
            raise ValueError("expected a decimal or 0x integer")
        value = int(value, 16 if value.startswith("0x") else 10)
    if value < 0 or value >= 2**256:
        raise ValueError("integer outside uint256")
    return value


def quantity(value):
    return hex(integer(value))


def address(value):
    return hex_data(value, 20)


def digest_hex(value):
    return "0x" + keccak256(bytes.fromhex(hex_data(value)[2:])).hex()


def create2_address(factory, salt, init_code):
    """Return CREATE2(factory, salt, init_code), including the 0xff domain byte."""
    factory = address(factory)
    salt = hex_data(salt, 32)
    init_code = hex_data(init_code)
    preimage = (
        b"\xff"
        + bytes.fromhex(factory[2:] + salt[2:])
        + keccak256(bytes.fromhex(init_code[2:]))
    )
    return "0x" + keccak256(preimage)[12:].hex()


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


class Rpc:
    READ_ONLY_METHODS = {
        "eth_chainId",
        "eth_getBlockByNumber",
        "eth_getCode",
        "eth_getTransactionCount",
        "eth_call",
    }

    def __init__(self, url, timeout=20):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("RPC URL must use HTTP or HTTPS")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.url = url
        self.timeout = timeout
        self.request_id = 0

    def __call__(self, method, params):
        if method not in self.READ_ONLY_METHODS:
            raise ValueError("RPC method is not supported by this read-only tool")
        self.request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params,
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "create2-deploy-rehearsal/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(4_000_001)
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError(f"RPC transport failed: {exc}") from exc
        if len(raw) > 4_000_000:
            raise ValueError("RPC response exceeds 4 MB")
        try:
            reply = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("RPC response is not JSON") from exc
        if (not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0"
                or type(reply.get("id")) is not int
                or reply["id"] != self.request_id):
            raise ValueError("invalid JSON-RPC envelope or response id")
        if ("result" in reply) == ("error" in reply):
            raise ValueError("expected exactly one of result or error")
        if "error" in reply:
            error = reply["error"]
            if (not isinstance(error, dict) or type(error.get("code")) is not int
                    or not isinstance(error.get("message"), str)):
                raise ValueError("invalid JSON-RPC error")
            raise RpcError(error["code"], error["message"], error.get("data"))
        return reply["result"]


def pin_block(rpc, block):
    tag = block if block in ("latest", "safe", "finalized", "earliest") else quantity(block)
    header = rpc("eth_getBlockByNumber", [tag, False])
    if not isinstance(header, dict):
        raise ValueError("block unavailable")
    number = quantity(header.get("number"))
    block_hash = hex_data(header.get("hash"), 32)
    if tag.startswith("0x") and tag != number:
        raise ValueError("RPC returned a different block than requested")
    chain_id = integer(rpc("eth_chainId", []))
    # Use the returned block number for all state reads. This pins latest/finalized
    # without requiring providers to support EIP-1898 blockHash parameters.
    return number, {
        "chain_id": chain_id,
        "block": int(number, 16),
        "block_hash": block_hash,
    }


def _revert_data(value):
    for _ in range(4):
        if not isinstance(value, dict):
            break
        value = value.get("data", value.get("result", value.get("return")))
    if value is None:
        return None
    try:
        return hex_data(value)
    except ValueError:
        return None


def _call_factory(rpc, transaction, block_tag):
    try:
        result = rpc("eth_call", [transaction, block_tag])
        return {"status": "return", "data": hex_data(result)}
    except RpcError as exc:
        if exc.code != 3 and "revert" not in str(exc).lower():
            raise
        return {"status": "revert", "data": _revert_data(exc.data)}


def rehearse_deployment(rpc, factory, sender, salt, init_code, *, block="finalized",
                       value=0, gas=5_000_000):
    """Check a CREATE2 address and execute the proxy call with eth_call only."""
    factory = address(factory)
    sender = address(sender)
    salt = hex_data(salt, 32)
    init_code = hex_data(init_code)
    value = integer(value)
    gas = integer(gas)
    if gas == 0:
        raise ValueError("gas must be positive")

    predicted = create2_address(factory, salt, init_code)
    block_tag, info = pin_block(rpc, block)
    factory_code = hex_data(rpc("eth_getCode", [factory, block_tag]))
    if factory_code != FACTORY_RUNTIME:
        raise ValueError("factory runtime does not match the supported deployment proxy")

    existing_code = hex_data(rpc("eth_getCode", [predicted, block_tag]))
    existing_nonce = integer(rpc("eth_getTransactionCount", [predicted, block_tag]))
    report = {
        **info,
        "factory": factory,
        "predicted_address": predicted,
        "init_code_hash": digest_hex(init_code),
        "existing_nonce": existing_nonce,
        "existing_code": existing_code != "0x",
    }
    if existing_nonce != 0 or existing_code != "0x":
        return {**report, "status": "collision"}

    transaction = {
        "to": factory,
        "from": sender,
        "data": salt + init_code[2:],
        "value": quantity(value),
        "gas": quantity(gas),
    }
    result = _call_factory(rpc, transaction, block_tag)
    if result["status"] == "revert":
        return {**report, "status": "reverted", "factory_revert_data": result["data"]}
    if result["data"] != predicted:
        raise ValueError("factory returned an unexpected address")
    return {**report, "status": "success", "returned_address": result["data"]}


def main(argv=None, rpc=None):
    parser = argparse.ArgumentParser(
        description="Rehearse a CREATE2 deployment through the deterministic proxy."
    )
    parser.add_argument("--rpc", required=True, help="HTTP(S) JSON-RPC URL")
    parser.add_argument("--factory", default=FACTORY_ADDRESS)
    parser.add_argument("--from", dest="sender", required=True)
    parser.add_argument("--salt", required=True, help="exactly 32 bytes of hex")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--init-code", help="creation bytecode, including constructor arguments")
    source.add_argument("--init-file", help="file containing creation bytecode hex")
    parser.add_argument("--value", default="0", help="wei sent to the constructor")
    parser.add_argument("--gas", default="5000000", help="eth_call execution ceiling")
    parser.add_argument("--block", default="finalized")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args(argv)
    try:
        init_code = args.init_code
        if args.init_file:
            with open(args.init_file, encoding="utf-8") as source_file:
                init_code = source_file.read().strip()
        client = rpc if rpc is not None else Rpc(args.rpc, args.timeout)
        result = rehearse_deployment(
            client,
            args.factory,
            args.sender,
            args.salt,
            init_code,
            block=args.block,
            value=args.value,
            gas=args.gas,
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RpcError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
