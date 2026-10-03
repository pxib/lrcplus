"""Registry for optional automatic reading generators."""
from __future__ import annotations

from typing import Callable, Any

_PROVIDERS: dict[str, dict[str, Any]] = {}

def register_reading_provider(provider_id, name, generator, *, description="", can_handle=None, batch_generator=None):
    if not callable(generator):
        raise TypeError("generator must be callable")
    if can_handle is not None and not callable(can_handle):
        raise TypeError("can_handle must be callable or None")
    if batch_generator is not None and not callable(batch_generator):
        raise TypeError("batch_generator must be callable or None")
    _PROVIDERS[str(provider_id)] = {
        "name": str(name),
        "generator": generator,
        "description": str(description),
        "can_handle": can_handle,
        "batch_generator": batch_generator,
    }

def unregister_reading_provider(provider_id):
    _PROVIDERS.pop(str(provider_id), None)

def available_reading_providers():
    return {key: value["name"] for key, value in _PROVIDERS.items()}

def reading_provider_info(provider_id=None):
    def public(value):
        return {k: v for k, v in value.items() if k not in {"generator", "can_handle", "batch_generator"}}
    if provider_id is None:
        return {key: public(value) for key, value in _PROVIDERS.items()}
    value = _PROVIDERS.get(str(provider_id))
    return None if value is None else public(value)

def generate_reading_segments(provider_id, text):
    entry = _PROVIDERS.get(str(provider_id))
    if entry is None:
        raise KeyError(f"Unknown reading provider: {provider_id}")
    return entry["generator"](str(text or ""))

def generate_reading_segments_batch(provider_id, texts):
    entry = _PROVIDERS.get(str(provider_id))
    if entry is None:
        raise KeyError(f"Unknown reading provider: {provider_id}")
    batch = entry.get("batch_generator")
    values = [str(text or "") for text in texts]
    if batch is not None:
        return batch(values)
    return [entry["generator"](text) for text in values]

def find_reading_provider(text, *, language=None):
    """Return the first registered provider that explicitly accepts *text*."""
    text = str(text or "")
    for provider_id, entry in _PROVIDERS.items():
        matcher = entry.get("can_handle")
        if matcher is None:
            continue
        try:
            accepted = matcher(text, language=language)
        except TypeError:
            accepted = matcher(text)
        except Exception:
            accepted = False
        if accepted:
            return provider_id
    return None
