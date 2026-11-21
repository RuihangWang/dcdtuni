# extension.py imports lazily; keep this import-light so parsers stay omni-free for tests.
try:
    from .extension import CfdBridgeExtension  # noqa: F401  (exposed for Kit IExt discovery)
except Exception:
    pass  # omni not importable (e.g. pytest importing parsers) — keep package light
