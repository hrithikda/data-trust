"""Domain exceptions. Callers (CLI, UI) translate these into friendly messages."""


class DataTrustError(Exception):
    """Base class for expected, user-actionable failures."""


class DatabaseUnavailableError(DataTrustError):
    """PostgreSQL cannot be reached with the configured settings."""


class MetadataNotInitializedError(DataTrustError):
    """The metadata schema or a required table has not been created yet."""


class ArtifactsMissingError(DataTrustError):
    """dbt artifacts (manifest.json, ...) have not been generated."""


class ConfigurationError(DataTrustError):
    """A configuration file (rules, governance, priority model) is invalid."""


class RuleExecutionError(DataTrustError):
    """A quality rule could not be compiled or executed."""
