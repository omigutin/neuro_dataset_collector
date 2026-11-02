""" Зоны для вырезки. """

from .markup import (
    MarkupInfo,
    describe_markup,
    legacy_sidecar_file,
    migrate_legacy_sidecar,
    production_key,
    resolve_markup,
    uses_video_markup,
    with_video_markup,
)
from .providers import (
    FullFrameRegions,
    LabelMeRegions,
    RegionProvider,
    StaticRegions,
    create_region_provider,
)

__all__ = [
    "FullFrameRegions",
    "LabelMeRegions",
    "MarkupInfo",
    "RegionProvider",
    "StaticRegions",
    "create_region_provider",
    "describe_markup",
    "legacy_sidecar_file",
    "migrate_legacy_sidecar",
    "production_key",
    "resolve_markup",
    "uses_video_markup",
    "with_video_markup",
]
