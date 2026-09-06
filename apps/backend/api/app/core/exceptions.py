class AppError(Exception):
    """Base application exception."""


class ResourceNotFound(AppError):
    """Raised when a requested resource does not exist."""


class ExternalProviderUnavailable(AppError):
    """Raised when an external provider is unavailable."""


class ValidationFailure(AppError):
    """Raised when domain validation fails."""
