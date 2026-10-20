""" Зоны для вырезки. """

from .providers import (
    FullFrameRegions,
    LabelMeRegions,
    RegionProvider,
    RegionsOrigin,
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
    "RegionProvider",
    "RegionsOrigin",
    "StaticRegions",
    "create_region_provider",
    "find_regions_file",
    "sidecar_regions_file",
    "suggest_regions_file",
    "write_labelme",
]
