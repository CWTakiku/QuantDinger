"""CSI300 enhanced-index helpers: preprocess, optimize, sync stubs."""

from .optimizer import optimize_enhanced_index, optimize_enhanced_index_partial
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
    load_consensus_panel,
    load_flow_panel,
    load_industry_and_size,
)
from .tushare_sync import fetch_index_weights, normalize_index_weight_frame, weights_to_platform_map
from .diagnostics import build_enhanced_index_diagnostics, summarize_enhanced_index_diagnostics

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
    "load_flow_panel",
    "load_consensus_panel",
    "optimize_enhanced_index",
    "optimize_enhanced_index_partial",
    "fetch_index_weights",
    "normalize_index_weight_frame",
    "weights_to_platform_map",
    "get_csi300_bench_weights",
    "build_enhanced_index_diagnostics",
    "summarize_enhanced_index_diagnostics",
]
