""" Зоны для вырезки. """

from .providers import FullFrameRegions, LabelMeRegions, RegionProvider, StaticRegions, create_region_provider

__all__ = ["FullFrameRegions", "LabelMeRegions", "RegionProvider", "StaticRegions", "create_region_provider"]
