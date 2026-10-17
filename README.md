# CREATE2 Deploy Rehearsal

CREATE2 Deploy Rehearsal answers one practical question before a deployment transaction is signed: will this exact creation bytecode and salt produce the expected address through the canonical deterministic deployment proxy, and is that address already occupied? It uses only read-only JSON-RPC calls and executes the real proxy with `eth_call`, so it never writes chain state.

## Example

These runs were made against Ethereum mainnet. The first uses `https://eth.drpc.org` at an old block and the second uses `https://rpc.mevblocker.io` at a recent block. In both cases the factory returned the same address calculated locally, and the target account was unused at the pinned block.

```text
$ python create2_deploy_rehearsal.py --rpc https://eth.drpc.org --from 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --salt 0x0000000000000000000000000000000000000000000000000000000000000001 --init-code 0x6001600c60003960016000f300 --block 0x1036640
{
  "chain_id": 1,
  "block": 17000000,
  "block_hash": "0x96cfa0fb5e50b0a3f6cc76f3299cfbf48f17e8b41798d1394474e67ec8a97e9f",
  "factory": "0x4e59b44847b379578588920ca78fbf26c0b4956c",
  "predicted_address": "0x6de9c5fa3955213e98d5adc9ecbecb5d08076f8d",
  "init_code_hash": "0x5fd875ef7d60f65991512743b1cdf42b8d3116d2e91e85f05d2482819c20b4e7",
  "existing_nonce": 0,
  "existing_code": false,
  "status": "success",
  "returned_address": "0x6de9c5fa3955213e98d5adc9ecbecb5d08076f8d"
}

$ python create2_deploy_rehearsal.py --rpc https://rpc.mevblocker.io --from 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --salt 0x0000000000000000000000000000000000000000000000000000000000c0ffee --init-code 0x6001600c60003960016000f300 --block 0x18f3664
{
  "chain_id": 1,
  "block": 26162788,
  "block_hash": "0x78dcf12c25c2261e9ca3b904b18b668d38d8c8b224020c0f44f1bb6d73966fe3",
  "factory": "0x4e59b44847b379578588920ca78fbf26c0b4956c",
  "predicted_address": "0xb7b2e63b5d13682a242a4c0720af364c23127d0d",
  "init_code_hash": "0x5fd875ef7d60f65991512743b1cdf42b8d3116d2e91e85f05d2482819c20b4e7",
  "existing_nonce": 0,
  "existing_code": false,
  "status": "success",
  "returned_address": "0xb7b2e63b5d13682a242a4c0720af364c23127d0d"
}
```

## Usage

```bash
python create2_deploy_rehearsal.py \
  --rpc https://your-rpc.example \
  --from 0x2222222222222222222222222222222222222222 \
  --salt 0x0000000000000000000000000000000000000000000000000000000000000001 \
  --init-file creation-code.hex
```

Use `--init-code` instead of `--init-file` for inline creation bytecode. The salt must be exactly 32 bytes. `--value` is constructor endowment in wei. `--gas` is the `eth_call` execution ceiling, not a gas estimate. `--block` accepts a block number or `latest`, `safe`, or `finalized`. The default factory is `0x4e59b44847b379578588920ca78fbf26c0b4956c`, the deterministic deployment proxy commonly deployed at the same address on EVM networks. The tool verifies that the code at the selected address matches this proxy runtime before calling it.

## How it works

The address is `keccak256(0xff || factory || salt || keccak256(init_code))[12:]`, as specified by EIP-1014. The tool pins a block, checks the proxy bytecode, checks both code and nonce at the predicted address, and stops with `collision` if either is nonzero. If the address is free, it sends the salt followed by the creation bytecode to the proxy in `eth_call` and checks that the returned address matches the local prediction.

## Tests

```bash
python -m unittest -v
```
