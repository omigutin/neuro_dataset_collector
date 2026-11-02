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
    RegionsOrigin,
    ShapeKind,
    StaticRegions,
    create_region_provider,
    find_regions_file,
    sidecar_regions_file,
    suggest_regions_file,
    write_labelme,
)

__all__ = [
    "FullFrameRegions",
    "LabelMeRegions",
    "MarkupInfo",
    "RegionProvider",
    "RegionsOrigin",
    "ShapeKind",
    "StaticRegions",
    "create_region_provider",
    "describe_markup",
    "find_regions_file",
    "legacy_sidecar_file",
    "migrate_legacy_sidecar",
    "production_key",
    "resolve_markup",
    "sidecar_regions_file",
    "suggest_regions_file",
    "uses_video_markup",
    "with_video_markup",
    "write_labelme",
]
