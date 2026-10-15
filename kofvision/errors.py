from __future__ import annotations


class KOFVisionError(Exception):
    """Base exception for domain-level KOFvision errors."""


class VolumeComputeError(KOFVisionError):
    """Raised when volume computation cannot be completed."""


class ReportGenerationError(KOFVisionError):
    """Raised when section report generation fails."""


class RasterLoadError(KOFVisionError):
    """Raised when raster input cannot be loaded as requested."""


class ProjectPayloadValidationError(KOFVisionError):
    """Raised for invalid or unsupported project payloads."""
