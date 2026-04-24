"""
Marine map component — dash-leaflet map for offshore sites, wave buoys, and land stations.
"""
import pandas as pd
import dash_leaflet as dl
from dash import html

from src.utils.constants import MAP_TILES, AUSTRALIA_CENTER, AUSTRALIA_ZOOM


def _dark_tile_layer():
    """Return a CartoDB Dark tile layer."""
    return dl.TileLayer(
        url=MAP_TILES["dark"],
        attribution='&copy; <a href="https://carto.com/">CARTO</a>',
    )


def create_marine_combined_map(
    land_sites_df,
    marine_sites_dict,
    selected_site=None,
    buoy_sites_dict=None,
    map_id="marine-combined-map",
    height="clamp(280px, 50vh, 400px)",
):
    """
    Create a dash-leaflet map showing offshore marine sites, wave buoys, and land stations.

    Args:
        land_sites_df: DataFrame with columns: site, lat, lon (weather stations)
        marine_sites_dict: dict of {name: {"lat": ..., "lon": ...}} (MARINE_SITES)
        selected_site: Currently selected site name
        buoy_sites_dict: dict of {name: {"lat": ..., "lon": ..., "provider": ...}} (IMOS_WAVE_BUOYS)
        map_id: Component ID
        height: CSS height
    """
    markers = []

    center = AUSTRALIA_CENTER
    zoom = AUSTRALIA_ZOOM

    # Marine markers (cyan, larger)
    for name, info in marine_sites_dict.items():
        is_selected = selected_site == name
        markers.append(
            dl.CircleMarker(
                center=[info["lat"], info["lon"]],
                radius=10 if is_selected else 7,
                color="#ffffff" if is_selected else "#06b6d4",
                fillColor="#06b6d4",
                fillOpacity=0.9 if is_selected else 0.7,
                weight=3 if is_selected else 2,
                children=dl.Tooltip(f"{name} (Offshore)"),
                id={"type": "marine-map-marker", "site": name},
                n_clicks=0,
            )
        )

    # Wave buoy markers (green, medium)
    if buoy_sites_dict:
        for name, info in buoy_sites_dict.items():
            buoy_value = f"buoy:{name}"
            is_selected = selected_site == buoy_value
            markers.append(
                dl.CircleMarker(
                    center=[info["lat"], info["lon"]],
                    radius=9 if is_selected else 6,
                    color="#ffffff" if is_selected else "#10b981",
                    fillColor="#10b981",
                    fillOpacity=0.9 if is_selected else 0.7,
                    weight=3 if is_selected else 2,
                    children=dl.Tooltip(f"{name} (Wave Buoy)"),
                    id={"type": "marine-map-marker", "site": buoy_value},
                    n_clicks=0,
                )
            )

    # Land station markers (blue, smaller)
    if land_sites_df is not None and not land_sites_df.empty:
        for _, row in land_sites_df.iterrows():
            is_selected = selected_site == row["site"]
            markers.append(
                dl.CircleMarker(
                    center=[row["lat"], row["lon"]],
                    radius=7 if is_selected else 4,
                    color="#ef4444" if is_selected else "#3b82f6",
                    fillColor="#ef4444" if is_selected else "#3b82f6",
                    fillOpacity=0.9 if is_selected else 0.5,
                    weight=2 if is_selected else 1,
                    children=dl.Tooltip(f"{row['site']} (Land)"),
                    id={"type": "marine-map-marker", "site": row["site"]},
                    n_clicks=0,
                )
            )

    return dl.Map(
        id=map_id,
        center=center,
        zoom=zoom,
        style={"height": height, "borderRadius": "8px"},
        children=[
            _dark_tile_layer(),
            dl.LayerGroup(children=markers),
        ],
    )
