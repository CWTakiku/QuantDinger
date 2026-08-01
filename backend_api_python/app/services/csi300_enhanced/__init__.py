"""CSI300 enhanced-index helpers: preprocess, optimize, sync stubs."""

from .optimizer import optimize_enhanced_index
from .preprocess import (
    build_equal_weight_alpha,
    cross_section_zscore,
    neutralize_industry_size,
    winsorize_mad,
)
from .bench import get_csi300_bench_weights
from .layered_alpha import (
    apply_icir_weights,
    apply_regime,
    build_neutralized_factor,
    combine_layers,
    load_industry_and_size,
)
from .tushare_sync import fetch_index_weights, normalize_index_weight_frame, weights_to_platform_map

__all__ = [
    "winsorize_mad",
    "cross_section_zscore",
    "neutralize_industry_size",
    "build_equal_weight_alpha",
    "apply_icir_weights",
    "apply_regime",
    "build_neutralized_factor",
    "combine_layers",
    "load_industry_and_size",
    "optimize_enhanced_index",
    "fetch_index_weights",
    "normalize_index_weight_frame",
    "weights_to_platform_map",
    "get_csi300_bench_weights",
]
