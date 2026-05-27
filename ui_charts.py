# ui_charts.py
# Sistema de estilo de gráficas (Plotly) para CombatIQ
# CS-018A — Mejora visual: paleta, helpers de trazas, leyenda

from __future__ import annotations
from typing import Optional, Callable
import plotly.graph_objects as go

_FONT_FAMILY = "Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial"

# Paleta oficial CombatIQ — se aplica automáticamente a todas las gráficas
# Orden: teal, ámbar, coral, violeta, verde, salmón, azul suave
PS_PALETTE = [
    "#0ea5e9",  # sky-500 — primario, vivo en dark y light
    "#f0a832",  # ámbar  — secundario / wellness
    "#e45a5a",  # coral  — alerta
    "#7b6fff",  # violeta
    "#27c98f",  # verde
    "#ff8c69",  # salmón
    "#2fb7c4",  # teal suave
]


def _safe(call: Callable, *args, **kwargs) -> None:
    """Aplica un update de estilo sin romper si la versión de Plotly no soporta alguna prop."""
    try:
        call(*args, **kwargs)
    except Exception:
        return


def apply_chart_style(
    fig: go.Figure,
    *,
    title: Optional[str] = None,
    x_title: Optional[str] = None,
    y_title: Optional[str] = None,
    height: int = 420,
) -> go.Figure:
    """Aplica el estilo visual unificado de CombatIQ a cualquier figura Plotly."""

    base_layout = dict(
        height=height,
        margin=dict(l=40, r=18, t=48, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_FONT_FAMILY, size=12, color="#E7ECF3"),
        colorway=PS_PALETTE,
        bargap=0.24,
        hoverdistance=80,
        spikedistance=80,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(255,255,255,0.06)",
            borderwidth=1,
            font=dict(size=10, color="#C9D3E3"),
        ),
    )
    _safe(fig.update_layout, **base_layout)
    _safe(fig.update_layout, barcornerradius=6)

    if title is not None:
        _safe(
            fig.update_layout,
            title=dict(
                text=title,
                x=0.01,
                xanchor="left",
                y=0.98,
                yanchor="top",
                font=dict(size=14, color="#E7ECF3", family=_FONT_FAMILY),
            ),
        )

    _safe(fig.update_layout, hovermode="x unified")
    _safe(
        fig.update_layout,
        hoverlabel=dict(
            bgcolor="#0d1520",
            bordercolor="#2fb7c4",
            font=dict(family=_FONT_FAMILY, size=12, color="#E7ECF3"),
            namelength=-1,
        ),
    )

    _safe(fig.update_layout, uirevision="powersync")

    if x_title is not None:
        _safe(fig.update_xaxes, title_text=x_title)
    if y_title is not None:
        _safe(fig.update_yaxes, title_text=y_title)

    axis_common = dict(
        showgrid=True,
        gridcolor="rgba(255,255,255,0.06)",
        zeroline=False,
        showline=True,
        linecolor="rgba(255,255,255,0.12)",
        ticks="outside",
        ticklen=4,
        tickcolor="rgba(255,255,255,0.18)",
        tickfont=dict(size=10.5, color="#8fa3bf"),
        title=dict(font=dict(size=12, color="#C9D3E3")),
        automargin=True,
    )
    _safe(fig.update_xaxes, **axis_common)
    _safe(fig.update_yaxes, **axis_common)

    _safe(
        fig.update_xaxes,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="solid",
        spikecolor="rgba(47,183,196,0.35)",
        spikethickness=1,
    )
    _safe(
        fig.update_yaxes,
        showspikes=False,
    )

    _safe(fig.update_layout, xaxis=dict(constrain="domain"))

    return fig


# ---------------------------------------------------------------------------
# Helpers de trazas — úsalos para crear series con estilo consistente
# ---------------------------------------------------------------------------

def make_line_trace(
    x,
    y,
    name: str,
    color: str = PS_PALETTE[0],
    *,
    width: float = 2.0,
    dash: str = "solid",
) -> go.Scatter:
    """Línea simple con estilo CombatIQ."""
    return go.Scatter(
        x=x,
        y=y,
        name=name,
        mode="lines",
        line=dict(color=color, width=width, dash=dash),
        hovertemplate=f"%{{x}}<br>%{{y}}<extra>{name}</extra>",
    )


def make_area_trace(
    x,
    y,
    name: str,
    color: str = PS_PALETTE[0],
    *,
    fill_opacity: float = 0.12,
    width: float = 2.0,
) -> go.Scatter:
    """Área rellena para tendencias — línea sólida + relleno semitransparente."""
    # Convierte "#rrggbb" a rgba para el fill
    def _hex_to_rgba(h: str, a: float) -> str:
        h = h.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{a})"

    fill_color = _hex_to_rgba(color, fill_opacity) if color.startswith("#") else color

    return go.Scatter(
        x=x,
        y=y,
        name=name,
        mode="lines",
        line=dict(color=color, width=width),
        fill="tozeroy",
        fillcolor=fill_color,
        hovertemplate=f"%{{x}}<br>%{{y}}<extra>{name}</extra>",
    )


def make_line_marker_trace(
    x,
    y,
    name: str,
    color: str = PS_PALETTE[1],
    *,
    width: float = 2.5,
    marker_size: int = 7,
    dash: str = "solid",
) -> go.Scatter:
    """Línea con markers — ideal para métricas semanales o puntos discretos."""
    return go.Scatter(
        x=x,
        y=y,
        name=name,
        mode="lines+markers",
        line=dict(color=color, width=width, dash=dash),
        marker=dict(
            color=color,
            size=marker_size,
            symbol="circle",
            line=dict(color="rgba(255,255,255,0.85)", width=1.3),
        ),
        hovertemplate=f"%{{x}}<br>%{{y}}<extra>{name}</extra>",
        connectgaps=False,
    )


def make_bar_trace(
    x,
    y,
    name: str,
    color: str = PS_PALETTE[0],
    *,
    opacity: float = 0.82,
) -> go.Bar:
    """Barra limpia sin borde — estilo CombatIQ."""
    return go.Bar(
        x=x,
        y=y,
        name=name,
        marker=dict(
            color=color,
            opacity=opacity,
            line=dict(width=0),
        ),
        hovertemplate=f"%{{x}}<br>%{{y}}<extra>{name}</extra>",
    )


def add_last_point_highlight(
    fig: go.Figure,
    x,
    y,
    *,
    name: str = "Último dato",
    color: str = PS_PALETTE[0],
    size: int = 10,
) -> go.Figure:
    pairs = [(xi, yi) for xi, yi in zip(x or [], y or []) if yi is not None]
    if not pairs:
        return fig

    last_x, last_y = pairs[-1]
    fig.add_trace(
        go.Scatter(
            x=[last_x],
            y=[last_y],
            name=name,
            mode="markers",
            showlegend=False,
            marker=dict(
                color=color,
                size=size,
                symbol="circle",
                line=dict(color="rgba(255,255,255,0.92)", width=2),
            ),
            hovertemplate=f"%{{x}}<br>%{{y}}<extra>{name}</extra>",
        )
    )
    return fig


def add_reference_band(
    fig: go.Figure,
    *,
    y0: float,
    y1: float,
    fillcolor: str = "rgba(39,201,143,0.08)",
) -> go.Figure:
    _safe(
        fig.add_hrect,
        y0=y0,
        y1=y1,
        line_width=0,
        layer="below",
        fillcolor=fillcolor,
    )
    return fig


def graph_config() -> dict:
    """Config estándar para dcc.Graph (desktop)."""
    return {
        "displayModeBar": False,
        "responsive": True,
        "scrollZoom": False,
    }


def placeholder_figure(height: int = 360) -> dict:
    """Dict plano para dcc.Graph en layouts — cero overhead Plotly en server.
    Usa empty_figure() solo en callbacks donde necesites mostrar mensaje estilizado."""
    return {
        "data": [],
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "height": height,
            "margin": {"l": 40, "r": 18, "t": 48, "b": 40},
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
        },
    }


def empty_figure(title: str = "", message: str = "Sin datos", height: int = 360) -> go.Figure:
    """Figura vacía con mensaje centrado — usada como valor inicial en dcc.Graph."""
    fig = go.Figure()
    apply_chart_style(fig, height=height)
    if title:
        fig.update_layout(title_text=title)
    fig.add_annotation(
        text=message,
        showarrow=False,
        x=0.5, y=0.5,
        xref="paper", yref="paper",
        font=dict(size=13, color="#8fa3bf"),
    )
    fig.update_xaxes(showgrid=False, showticklabels=False, zeroline=False)
    fig.update_yaxes(showgrid=False, showticklabels=False, zeroline=False)
    return fig
