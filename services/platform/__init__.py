"""Small platform execution boundary; authorization belongs to Core."""
from services.platform.resolver import PlatformCapabilityUnavailable, PlatformOperationError, resolve_platform

__all__ = ['PlatformCapabilityUnavailable', 'PlatformOperationError', 'resolve_platform']
