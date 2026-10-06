"""Wallet proofs query only the configured Base chain and fail closed."""

import json

import httpx
import pytest
from controlplane_identity.wallet import (
    BaseWalletVerifier,
    CounterfactualValidator,
    WalletProofError,
)
from eth_abi import decode, encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

ACCOUNT = Account.from_key(bytes.fromhex("11" * 32))
OTHER = Account.from_key(bytes.fromhex("22" * 32))
MESSAGE = "test-only single-use wallet challenge on Base Sepolia"
SIGNATURE = bytes(ACCOUNT.sign_message(encode_defunct(text=MESSAGE)).signature)
MAGIC = bytes.fromhex("1626ba7e") + bytes(28)
SUFFIX = bytes.fromhex("6492" * 16)
VALIDATOR = OTHER.address
CODE = bytes.fromhex("60016000")


def make_verifier(
    *, code="0x", result=None, chain="0x14a34", validator=None, override=None
):
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        method = body["method"]
        results = {
            "eth_chainId": chain,
            "eth_blockNumber": "0x123",
            "eth_getCode": code,
            "eth_call": "0x" + MAGIC.hex(),
        }
        value = results[method] if result is None or method != "eth_call" else result
        payload = {"jsonrpc": "2.0", "id": 1, "result": value}
        if override is not None:
            payload = override(method, payload)
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return (
        BaseWalletVerifier(
            chain_id=84532,
            rpc_url="https://base.example/rpc",
            client=client,
            counterfactual_validator=validator,
        ),
        client,
        calls,
    )


async def verify(verifier, **kwargs):
    return await verifier.verify_personal_signature(
        **dict(
            address=ACCOUNT.address,
            message=MESSAGE,
            signature=SIGNATURE,
            chain_id=84532,
            **kwargs,
        )
    )


@pytest.mark.asyncio
async def test_eoa_and_exact_block():
    verifier, client, calls = make_verifier()
    async with client:
        assert await verify(verifier) == ACCOUNT.address
    assert [c["method"] for c in calls] == [
        "eth_chainId",
        "eth_blockNumber",
        "eth_getCode",
    ]
    assert calls[-1]["params"] == [ACCOUNT.address, "0x123"]


@pytest.mark.asyncio
@pytest.mark.parametrize("chain_id", [232, 1, 8453, True])
async def test_other_chains_rejected_before_rpc(chain_id):
    verifier, client, calls = make_verifier()
    with pytest.raises(WalletProofError):
        async with client:
            await verifier.verify_personal_signature(
                address=ACCOUNT.address,
                message=MESSAGE,
                signature=SIGNATURE,
                chain_id=chain_id,
            )
    assert calls == []


@pytest.mark.asyncio
async def test_contract_exists_only_on_other_chain_is_not_accepted():
    verifier, client, calls = make_verifier()
    with pytest.raises(WalletProofError):
        async with client:
            await verifier.verify_personal_signature(
                address=OTHER.address,
                message=MESSAGE,
                signature=SIGNATURE,
                chain_id=84532,
            )
    assert [c["method"] for c in calls] == [
        "eth_chainId",
        "eth_blockNumber",
        "eth_getCode",
    ]


@pytest.mark.asyncio
async def test_erc1271_base_contract_and_digest():
    verifier, client, calls = make_verifier(code="0x6000")
    async with client:
        assert await verify(verifier) == ACCOUNT.address
    call, block = calls[-1]["params"]
    assert block == "0x123" and call["to"] == ACCOUNT.address
    calldata = bytes.fromhex(call["data"][2:])
    digest, signature = decode(["bytes32", "bytes"], calldata[4:])
    signable = encode_defunct(text=MESSAGE)
    assert digest == keccak(
        b"\x19" + signable.version + signable.header + signable.body
    )
    assert signature == SIGNATURE
    assert calldata[:4] == bytes.fromhex("1626ba7e")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result", ["0x", "0x1626ba7e", "0x" + bytes(32).hex(), None, 1, "garbage"]
)
async def test_bad_contract_results_rejected(result):
    verifier, client, _ = make_verifier(
        code="0x6000",
        override=lambda method, payload: (
            dict(payload, result=result) if method == "eth_call" else payload
        ),
    )
    with pytest.raises(WalletProofError):
        async with client:
            await verify(verifier)


@pytest.mark.asyncio
async def test_6492_wrapper_is_forwarded_before_signer_code_lookup():
    wrapper = (
        encode(
            ["address", "bytes", "bytes"],
            [OTHER.address, b"factory calldata", b"original signature"],
        )
        + SUFFIX
    )
    validator = CounterfactualValidator(VALIDATOR, keccak(CODE))
    verifier, client, calls = make_verifier(
        code="0x" + CODE.hex(),
        result="0x" + (1).to_bytes(32, "big").hex(),
        validator=validator,
    )
    async with client:
        assert (
            await verifier.verify_personal_signature(
                address=ACCOUNT.address,
                message=MESSAGE,
                signature=wrapper,
                chain_id=84532,
            )
            == ACCOUNT.address
        )
    assert calls[2]["params"] == [VALIDATOR, "0x123"]
    call, block = calls[-1]["params"]
    assert call["to"] == VALIDATOR and block == "0x123"
    calldata = bytes.fromhex(call["data"][2:])
    assert calldata[:4] == keccak(text="isValidSig(address,bytes32,bytes)")[:4]
    signer, _, signature = decode(["address", "bytes32", "bytes"], calldata[4:])
    assert signer.lower() == ACCOUNT.address.lower() and signature == wrapper


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "validator,code",
    [
        (None, "0x"),
        (CounterfactualValidator(VALIDATOR, keccak(CODE)), "0x"),
        (CounterfactualValidator(VALIDATOR, keccak(CODE)), "0x6000"),
    ],
)
async def test_unconfigured_or_untrusted_6492_rejected(validator, code):
    verifier, client, calls = make_verifier(code=code, validator=validator)
    with pytest.raises(WalletProofError):
        async with client:
            await verifier.verify_personal_signature(
                address=ACCOUNT.address,
                message=MESSAGE,
                signature=b"wrapped" + SUFFIX,
                chain_id=84532,
            )
    assert not any(c["method"] == "eth_call" for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        lambda m, p: dict(p, result="0xe8") if m == "eth_chainId" else p,
        lambda m, p: dict(p, error={"code": -32000}) if m == "eth_call" else p,
        lambda m, p: dict(p, id=2),
        lambda m, p: dict(p, result="0x0123") if m == "eth_blockNumber" else p,
        lambda m, p: [],
    ],
)
async def test_rpc_errors_fail_closed(override):
    verifier, client, _ = make_verifier(code="0x6000", override=override)
    with pytest.raises(WalletProofError):
        async with client:
            await verify(verifier)


def test_configuration_refuses_foreign_chain():
    with pytest.raises(ValueError):
        BaseWalletVerifier(
            chain_id=232, rpc_url="https://base.example", client=httpx.AsyncClient()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "redirect", "http_error"])
async def test_transport_failures_reject_proof(failure):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout")
        if failure == "redirect":
            return httpx.Response(
                302, headers={"location": "https://other-chain.example"}
            )
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        verifier = BaseWalletVerifier(
            chain_id=84532, rpc_url="https://base.example", client=client
        )
        with pytest.raises(WalletProofError):
            await verify(verifier)


@pytest.mark.asyncio
@pytest.mark.parametrize("signature", [b"", bytes(16385), bytes(65)])
async def test_malformed_signatures_rejected(signature):
    verifier, client, _ = make_verifier()
    with pytest.raises(WalletProofError):
        async with client:
            await verifier.verify_personal_signature(
                address=ACCOUNT.address,
                message=MESSAGE,
                signature=signature,
                chain_id=84532,
            )


@pytest.mark.asyncio
async def test_base_mainnet_supported():
    verifier, client, _ = make_verifier(chain="0x2105")
    verifier = BaseWalletVerifier(
        chain_id=8453, rpc_url="https://base.example", client=client
    )
    async with client:
        assert (
            await verifier.verify_personal_signature(
                address=ACCOUNT.address,
                message=MESSAGE,
                signature=SIGNATURE,
                chain_id=8453,
            )
            == ACCOUNT.address
        )
