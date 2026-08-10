"""Application-specific errors used across adapter boundaries."""


class SyncError(Exception):
    """Base class for expected ingestion failures."""


class ConfigurationError(SyncError):
    """Configuration is missing or inconsistent."""


class SchemaError(SyncError):
    """Input or destination data no longer matches the expected contract."""


class ConcurrentRunError(SyncError):
    """Another sync process already owns the run lock."""
