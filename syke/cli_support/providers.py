"""Provider-resolution helpers for the Syke CLI."""

from __future__ import annotations

import os

from syke.llm.env import evaluate_provider_readiness, provider_endpoint_configured


def provider_payload(cli_provider: str | None = None) -> dict[str, object]:
    from syke.llm.env import resolve_provider

    try:
        provider = resolve_provider(cli_provider=cli_provider)
        return describe_provider(provider.id, selection_source=resolve_source(cli_provider))
    except (ValueError, RuntimeError) as exc:
        return {
            "configured": False,
            "id": None,
            "source": None,
            "runtime_provider": None,
            "auth_source": None,
            "model": None,
            "model_source": None,
            "endpoint": None,
            "endpoint_source": None,
            "error": str(exc),
        }


def describe_provider(
    provider_id: str, *, selection_source: str | None = None
) -> dict[str, object]:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import (
        get_credential,
        get_default_model,
        get_default_provider,
        get_pi_auth_path,
        get_pi_models_path,
        get_provider_base_url,
        get_provider_override,
    )

    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider_id)
    if entry is None:
        return {
            "configured": False,
            "id": provider_id,
            "source": selection_source,
            "runtime_provider": None,
            "auth_source": None,
            "model": None,
            "model_source": None,
            "endpoint": None,
            "endpoint_source": None,
            "error": f"Unknown provider {provider_id!r} in Pi catalog",
        }

    readiness = evaluate_provider_readiness(provider_id)
    credential = get_credential(provider_id)
    default_provider = get_default_provider()
    default_model = get_default_model()
    endpoint_override = get_provider_base_url(provider_id)
    provider_override = get_provider_override(provider_id) or {}
    available_models = tuple(getattr(entry, "available_models", ()))
    override_has_request_auth = bool(
        provider_override.get("apiKey")
        or provider_override.get("headers")
        or provider_override.get("authHeader")
    )

    if credential is not None:
        auth_source = str(get_pi_auth_path())
        if credential.get("type") == "oauth":
            auth_source = f"{auth_source} (oauth)"
    elif override_has_request_auth:
        auth_source = f"{get_pi_models_path()} (request config)"
    elif available_models:
        auth_source = "catalog only (not daemon-safe)"
    elif entry.oauth:
        auth_source = "Pi native login"
    else:
        auth_source = "missing"

    if default_provider == provider_id and default_model:
        model = default_model
        model_source = "Pi settings defaultModel"
    elif entry.default_model:
        model = entry.default_model
        model_source = "Pi provider default"
    else:
        model = None
        model_source = None

    if endpoint_override:
        endpoint = endpoint_override
        endpoint_source = "Pi models.json baseUrl"
    elif getattr(entry, "requires_base_url", False):
        if provider_endpoint_configured(provider_id):
            endpoint = "Pi env/resource config"
            endpoint_source = "Pi env/config"
        else:
            endpoint = None
            endpoint_source = "required in Pi config"
    elif entry.models:
        endpoint = "provider default"
        endpoint_source = "Pi built-in/default"
    else:
        endpoint = None
        endpoint_source = None

    return {
        "configured": readiness.ready,
        "id": provider_id,
        "source": selection_source,
        "runtime_provider": provider_id,
        "auth_source": auth_source,
        "model": model,
        "model_source": model_source,
        "endpoint": endpoint,
        "endpoint_source": endpoint_source,
        "error": None if readiness.ready else readiness.detail,
    }


def resolve_source(cli_provider: str | None) -> str:
    if cli_provider:
        return "CLI --provider flag"
    if os.getenv("SYKE_PROVIDER"):
        return "SYKE_PROVIDER env"
    from syke.pi_state import get_default_provider

    if get_default_provider():
        return "Pi settings"
    return "unknown"
