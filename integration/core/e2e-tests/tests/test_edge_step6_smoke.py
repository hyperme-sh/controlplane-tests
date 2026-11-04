"""Gateway Step 6: REST + GraphQL + gRPC smoke via ASGI (no Docker)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import grpc.aio
import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from controlplane_edge.aggregation import register_aggregation_routes
from controlplane_edge.graphql.register import register_graphql_routes
from controlplane_edge.grpc_server import grpc_server_lifespan
from controlplane_edge.register_cross_cutting import register_gateway_cross_cutting

_PROTO = Path(__file__).resolve().parents[3] / "proto" / "spanopticon" / "v1"
if str(_PROTO) not in sys.path:
    sys.path.insert(0, str(_PROTO))

import contractecho_pb2  # noqa: E402
import contractecho_pb2_grpc  # noqa: E402


def _fake_bearer(sub: str = "11111111-1111-1111-1111-111111111111") -> str:
    import base64
    import json

    h = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    p = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"{h}.{p}.sig"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rest_graphql_and_grpc_through_gateway_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.core.config as api_config

    monkeypatch.setattr(api_config.settings, "keycloak_jwks_url", None)
    monkeypatch.setattr(api_config.settings, "keycloak_issuer", None)
    monkeypatch.setenv("CPEDGE_AUTH_SERVICE_URL", "http://auth.e2e.test")
    monkeypatch.setenv("CPEDGE_USER_SERVICE_URL", "http://user.e2e.test")

    app = FastAPI()
    register_gateway_cross_cutting(app)
    register_aggregation_routes(app)
    register_graphql_routes(app)

    respx.get("http://auth.e2e.test/openapi.json").mock(
        return_value=httpx.Response(
            200,
            json={"openapi": "3.0.0", "paths": {}, "info": {"title": "a", "version": "1"}, "components": {"schemas": {}}},
        )
    )
    tid = str(uuid.uuid4())

    transport = httpx.ASGITransport(app=app)
    async with grpc_server_lifespan(host="127.0.0.1", port=0, enabled=True) as server:
        async with LifespanManager(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/v1/openapi.json")
                assert r.status_code == 200
                md = await client.get("/changelog/openapi.md", params={"from": "v1", "to": "v2"})
                assert md.status_code == 200
                assert b"#" in md.content
                g = await client.post(
                    "/graphql",
                    json={"query": "{ __typename }"},
                    headers={"Authorization": f"Bearer {_fake_bearer()}", "X-Tenant-ID": tid},
                )
                assert g.status_code == 200

            assert server is not None
            async with grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}") as channel:
                stub = contractecho_pb2_grpc.ContractEchoStub(channel)
                req = contractecho_pb2.UserWire(id=str(uuid.uuid4()), email="a@b.com", display_name="x")
                out = await stub.EchoUser(
                    req,
                    metadata=(
                        ("authorization", f"Bearer {_fake_bearer()}"),
                        ("x-tenant-id", tid),
                    ),
                )
                assert out.email == "a@b.com"
