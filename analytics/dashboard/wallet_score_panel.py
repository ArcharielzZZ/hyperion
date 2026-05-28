"""Dash UI for the offline bundle research score panel."""

from __future__ import annotations

from typing import Any

from dash import html

from analytics.lib.wallet_scoring import BundleScoreResult, format_ts
from wallet_layout import BG_PLOT, BUY_GREEN, GRID, SELL_RED, TEXT_DIM, TWEET_CYAN

STYLE_LABELS = {
    "scalp": "Scalp",
    "momentum": "Momentum",
    "mean_reversion": "Mean Reversion",
    "swing": "Swing",
    "unknown": "Unbekannt",
}

SUB_SCORES = [
    ("consistency_score", "Consistency"),
    ("survivability_score", "Survivability"),
    ("timing_score", "Timing"),
    ("leverage_discipline_score", "Leverage"),
    ("conviction_score", "Conviction"),
]


def _score_bar(label: str, value: float, *, muted: bool = False) -> html.Div:
    pct = max(0.0, min(100.0, value))
    color = TEXT_DIM if muted else BUY_GREEN
    return html.Div(
        style={"marginBottom": "6px"},
        children=[
            html.Div(
                style={
                    "display": "flex",
                    "justifyContent": "space-between",
                    "fontSize": "11px",
                    "marginBottom": "2px",
                },
                children=[
                    html.Span(label, style={"opacity": 0.85 if not muted else 0.55}),
                    html.Span(f"{pct:.1f}", style={"fontFamily": "monospace"}),
                ],
            ),
            html.Div(
                style={
                    "height": "6px",
                    "backgroundColor": GRID,
                    "borderRadius": "3px",
                    "overflow": "hidden",
                },
                children=[
                    html.Div(
                        style={
                            "width": f"{pct:.1f}%",
                            "height": "100%",
                            "backgroundColor": color,
                            "opacity": 0.45 if muted else 0.9,
                        }
                    )
                ],
            ),
        ],
    )


def render_score_placeholder(message: str) -> html.Div:
    return html.Div(
        style={
            "padding": "12px 14px",
            "marginBottom": "12px",
            "border": f"1px solid {GRID}",
            "borderRadius": "6px",
            "backgroundColor": BG_PLOT,
            "fontSize": "12px",
            "opacity": 0.7,
        },
        children=[
            html.Span("Research Score (Bundle, Full History): ", style={"fontWeight": 600}),
            message,
        ],
    )


def render_score_panel(result: BundleScoreResult, bundle_stats: dict[str, Any]) -> html.Div:
    style_label = STYLE_LABELS.get(result.style, result.style)
    style_color = TWEET_CYAN if result.style != "unknown" else TEXT_DIM
    conf_pct = result.style_confidence * 100.0

    bars = []
    for attr, label in SUB_SCORES:
        value = getattr(result, attr)
        muted = attr == "leverage_discipline_score" and result.metrics.leverage_neutral
        bars.append(_score_bar(label, value, muted=muted))

    time_min = format_ts(bundle_stats.get("time_min"))
    time_max = format_ts(bundle_stats.get("time_max"))
    pulled = bundle_stats.get("pulled_at") or ""
    pulled_note = f" | Bundle-Stand: {pulled[:19]}" if pulled else ""

    footnotes = [
        "Offline-Vorschau aus Parquet-Fills (volle History). Kein Live/Postgres-Score.",
        "Leverage-Score neutral (50), da Bundle keine Leverage-Daten enthaelt.",
    ]
    if result.skip_reason:
        footnotes.insert(0, result.skip_reason)

    return html.Div(
        style={
            "padding": "12px 14px",
            "marginBottom": "12px",
            "border": f"1px solid {GRID}",
            "borderRadius": "6px",
            "backgroundColor": BG_PLOT,
        },
        children=[
            html.Div(
                style={
                    "display": "flex",
                    "justifyContent": "space-between",
                    "alignItems": "flex-start",
                    "flexWrap": "wrap",
                    "gap": "10px",
                    "marginBottom": "10px",
                },
                children=[
                    html.Div(
                        children=[
                            html.Div(
                                "Research Score (Bundle, Full History)",
                                style={"fontWeight": 700, "fontSize": "13px", "marginBottom": "4px"},
                            ),
                            html.Div(
                                style={"display": "flex", "gap": "10px", "alignItems": "center", "flexWrap": "wrap"},
                                children=[
                                    html.Span(
                                        style_label,
                                        style={
                                            "padding": "2px 8px",
                                            "borderRadius": "4px",
                                            "backgroundColor": "#1a2a3a",
                                            "color": style_color,
                                            "fontWeight": 600,
                                            "fontSize": "12px",
                                        },
                                    ),
                                    html.Span(
                                        f"Style-Konfidenz: {conf_pct:.0f}%",
                                        style={"fontSize": "11px", "opacity": 0.65},
                                    ),
                                ],
                            ),
                        ]
                    ),
                    html.Div(
                        style={"textAlign": "right"},
                        children=[
                            html.Div("Total", style={"fontSize": "11px", "opacity": 0.6}),
                            html.Div(
                                f"{result.total_score:.1f}",
                                style={
                                    "fontSize": "28px",
                                    "fontWeight": 700,
                                    "color": BUY_GREEN if result.total_score >= 55 else SELL_RED,
                                    "fontFamily": "monospace",
                                    "lineHeight": "1.1",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 1fr))",
                    "gap": "4px 16px",
                    "marginBottom": "10px",
                },
                children=bars,
            ),
            html.Div(
                style={"fontSize": "11px", "opacity": 0.65, "marginBottom": "6px"},
                children=(
                    f"Fills: {bundle_stats.get('fill_count', 0):,} | "
                    f"Aktive Tage: {result.metrics.active_days} | "
                    f"Zeitraum: {time_min} ... {time_max}{pulled_note}"
                ),
            ),
            html.Div(
                style={"fontSize": "10px", "opacity": 0.5},
                children=[html.Div(note) for note in footnotes],
            ),
        ],
    )
