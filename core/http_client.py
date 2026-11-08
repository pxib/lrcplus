"""Defer the requests stack until an HTTP feature actually uses it."""


def _module():
    # Keep a static import for frozen-build analysis. Python's import lock and
    # module cache handle concurrent first requests and subsequent reuse.
    import requests
    return requests


class _DeferredRequests:
    # Introspection must see the same namespace that writes and deletes use.
    # In particular, patch tools must restore attributes on the real module.
    __slots__ = ()

    def __getattr__(self, name):
        return getattr(_module(), name)

    def __setattr__(self, name, value):
        setattr(_module(), name, value)

    def __delattr__(self, name):
        delattr(_module(), name)


requests = _DeferredRequests()
