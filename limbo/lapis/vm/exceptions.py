class LapisError(Exception):
    """Base Lapis error."""


class LapisValidationError(LapisError):
    """Raised when a Lapis contract tree is invalid."""


class LapisExecutionError(LapisError):
    """Raised when a Lapis action fails at runtime."""
