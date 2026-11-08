"""Streamlit 互動儀表板模組 (app)

提供電腦硬體新品歷史走勢與二手行情比價視覺化介面。
遵循「刪到不能再刪」極簡標準。
"""

from app.main_app import (
    CATEGORIES,
    DEFAULT_SPEC_GROUPS,
    build_listings_dataframe,
    create_platform_comparison_chart,
    create_price_trend_chart,
    format_metric_card_data,
    get_db,
    get_filtered_listings,
    get_representative_product,
    get_spec_groups,
    get_status_badge,
    main,
    render_charts,
    render_metrics,
    render_sidebar,
    render_table,
)

__all__ = [
    "CATEGORIES",
    "DEFAULT_SPEC_GROUPS",
    "get_db",
    "get_spec_groups",
    "get_representative_product",
    "get_filtered_listings",
    "format_metric_card_data",
    "create_price_trend_chart",
    "create_platform_comparison_chart",
    "get_status_badge",
    "build_listings_dataframe",
    "render_sidebar",
    "render_metrics",
    "render_charts",
    "render_table",
    "main",
]
