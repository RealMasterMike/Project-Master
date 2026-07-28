import asyncio

import httpx
import pytest
from pydantic import ValidationError

from project_master.integrations.comfyui.profiles import (
    ComfyAuth,
    ComfyUIProfile,
    EnvironmentSecretResolver,
    SecretRef,
)
from project_master.integrations.comfyui.security import (
    ComfySecurityError,
    join_api_url,
)
from project_master.integrations.comfyui.transport import (
    ComfyTransportError,
    HttpxComfyTransport,
    OutputRef,
)


def test_local_profile_normalizes_and_persists_only_secret_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROJECT_MASTER_TEST_COMFY_TOKEN", "runtime-secret")
    profile = ComfyUIProfile(
        id="studio",
        name="Local studio",
        base_url="http://localhost:8188/",
        auth=ComfyAuth(secret_ref=SecretRef(key="PROJECT_MASTER_TEST_COMFY_TOKEN")),
    )

    assert profile.base_url == "http://localhost:8188"
    serialized = profile.model_dump_json()
    assert "PROJECT_MASTER_TEST_COMFY_TOKEN" in serialized
    assert "runtime-secret" not in serialized
    assert profile.authentication_headers(EnvironmentSecretResolver()) == {
        "Authorization": "Bearer runtime-secret"
    }


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/comfy",
        "http://user:password@127.0.0.1:8188",
        "http://127.0.0.1:8188?redirect=https://evil.example",
        "http://127.0.0.1:8188/../admin",
        "http://127.0.0.1:8188/safe%2F..%2Fadmin",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.50:8188",
        "https://untrusted.example",
    ],
)
def test_profile_rejects_unsafe_or_untrusted_endpoints(url: str) -> None:
    with pytest.raises(ValidationError):
        ComfyUIProfile(id="unsafe", name="Unsafe", base_url=url)


def test_remote_profile_requires_explicit_host_https_and_tls_verification() -> None:
    profile = ComfyUIProfile(
        id="remote",
        name="Remote GPU",
        base_url="https://render.example:8443/comfy/",
        trusted_hosts=("render.example",),
    )

    assert profile.base_url == "https://render.example:8443/comfy"
    assert join_api_url(profile.base_url, "system_stats") == (
        "https://render.example:8443/comfy/system_stats"
    )
    with pytest.raises(ValidationError):
        ComfyUIProfile(
            id="remote",
            name="Remote GPU",
            base_url="http://render.example:8188",
            trusted_hosts=("render.example",),
        )
    with pytest.raises(ValidationError):
        ComfyUIProfile(
            id="remote",
            name="Remote GPU",
            base_url="https://render.example",
            trusted_hosts=("render.example",),
            verify_tls=False,
        )


@pytest.mark.parametrize(
    ("filename", "subfolder"),
    [
        ("../secret.png", ""),
        ("folder/image.png", ""),
        ("image.png", "../../etc"),
        ("image.png", "/absolute"),
        ("image.png", r"..\windows"),
    ],
)
def test_output_reference_rejects_path_escape(filename: str, subfolder: str) -> None:
    with pytest.raises(ValidationError):
        OutputRef(filename=filename, subfolder=subfolder)


def test_output_url_encodes_metadata_without_treating_it_as_a_local_path() -> None:
    profile = ComfyUIProfile(id="local", name="Local")
    transport = HttpxComfyTransport(profile)
    output = OutputRef(filename="frame 01.png", subfolder="project shots")

    try:
        assert transport.output_url(output) == (
            "http://127.0.0.1:8188/view?filename=frame+01.png&subfolder=project+shots&type=output"
        )
    finally:
        asyncio.run(transport.aclose())


def test_http_transport_refuses_redirects_without_following_them() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    transport = HttpxComfyTransport(
        ComfyUIProfile(id="local", name="Local"),
        client=client,
    )

    async def invoke() -> None:
        with pytest.raises(ComfyTransportError, match="redirects"):
            await transport.system_stats()
        await client.aclose()

    asyncio.run(invoke())
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"


def test_http_transport_rejects_injected_redirect_following_client() -> None:
    client = httpx.AsyncClient(follow_redirects=True)
    try:
        with pytest.raises(ComfySecurityError, match="cannot follow redirects"):
            HttpxComfyTransport(
                ComfyUIProfile(id="local", name="Local"),
                client=client,
            )
    finally:
        asyncio.run(client.aclose())


def test_route_join_rejects_absolute_or_traversing_routes() -> None:
    with pytest.raises(ComfySecurityError):
        join_api_url("http://127.0.0.1:8188", "https://evil.example/status")
    with pytest.raises(ComfySecurityError):
        join_api_url("http://127.0.0.1:8188", "../status")
    with pytest.raises(ComfySecurityError):
        join_api_url("http://127.0.0.1:8188", "safe%2F..%2Fstatus")
