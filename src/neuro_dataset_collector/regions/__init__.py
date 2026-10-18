""" Зоны для вырезки. """

from .providers import (
    FullFrameRegions,
    LabelMeRegions,
    RegionProvider,
    StaticRegions,
    create_region_provider,
    suggest_regions_file,
)

__all__ = [
    "FullFrameRegions",
    "LabelMeRegions",
    "RegionProvider",
    "StaticRegions",
    "create_region_provider",
    "suggest_regions_file",
]
