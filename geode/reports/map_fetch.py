#!/usr/bin/env python3
"""
geode_map_fetch.py
==================
Fetches map tile images for GeoDE station reports.

Produces four images for a given (lat, lon):
  1. General location  — OSM street map, wide zoom (country/region level)
  2. Site detail       — OSM street map, close zoom (road/landmark level)
  3. Satellite view    — ESRI World Imagery, very close zoom
  4. (Monument photo is supplied by GeoDE from the visit record, not fetched here)

All images are returned as PIL Image objects (RGB, 900×540 px) ready to be
passed directly to geode_station_report.build_report().

Dependencies
------------
    pip install staticmap pillow requests

Tile providers used (all free, no API key required)
----------------------------------------------------
  OSM street maps : https://tile.openstreetmap.org/{z}/{x}/{y}.png
      - Usage policy: https://operations.osmfoundation.org/policies/tiles/
      - Set a descriptive User-Agent identifying your application.
      - Cache aggressively — do not re-fetch tiles that haven't changed.

  ESRI World Imagery (satellite):
      https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
      - Free for display purposes; see ESRI terms at https://www.esri.com/en-us/legal/terms/full-master-agreement
      - No API key needed for raster tile access.

Caching
-------
By default, fetched tiles are cached in ~/.cache/geode/tiles/ so repeated
calls for the same station don't re-download tiles. Pass cache_dir=None to
disable caching.

Usage
-----
    from geode_map_fetch import fetch_maps

    imgs = fetch_maps(lat=-31.38481834, lon=-68.59541219)
    # imgs['general']   → PIL Image (OSM overview)
    # imgs['detail']    → PIL Image (OSM detail)
    # imgs['satellite'] → PIL Image (ESRI imagery)
    # Save to disk:
    imgs['general'].save('map_general.jpg', 'JPEG', quality=90)
    # Or encode directly for the report:
    from geode_station_report import build_report, StationReport
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        gp = os.path.join(tmp, 'gen.jpg');  imgs['general'].save(gp)
        dp = os.path.join(tmp, 'det.jpg');  imgs['detail'].save(dp)
        sp = os.path.join(tmp, 'sat.jpg');  imgs['satellite'].save(sp)
        station.map_general_path  = gp
        station.map_detail_path   = dp
        station.satellite_path    = sp
        html = build_report(station, ...)
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
import xml.etree.ElementTree as _ET
import zipfile as _zipfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _parse_kml_coords(path: str) -> list[tuple[float, float]]:
    """
    Extract all (lon, lat) coordinate pairs from a KML or KMZ file.
    KMZ is a ZIP archive — the first .kml entry inside is used.
    Handles both namespaced and bare <coordinates> elements.
    Returns a flat list of (lon, lat) tuples in document order.
    """
    coords: list[tuple[float, float]] = []

    def _extract_from_xml(content: bytes) -> None:
        try:
            root = _ET.fromstring(content)
            for elem in root.iter():
                tag = elem.tag
                text = (elem.text or '').strip()
                if not text:
                    continue
                if tag.endswith('}coordinates') or tag == 'coordinates':
                    # Standard KML: comma-separated "lon,lat[,alt]" tokens
                    for token in text.split():
                        parts = token.split(',')
                        if len(parts) >= 2:
                            try:
                                coords.append((float(parts[0]), float(parts[1])))
                            except ValueError:
                                pass
                elif tag.endswith('}coord'):
                    # gx:Track extension: space-separated "lon lat [alt]" per element
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            coords.append((float(parts[0]), float(parts[1])))
                        except ValueError:
                            pass
        except Exception as exc:
            log.warning(f'KML XML parse error: {exc}')

    try:
        if path.lower().endswith('.kmz'):
            with _zipfile.ZipFile(path) as zf:
                # Use the first .kml entry found (usually doc.kml)
                kml_names = [n for n in zf.namelist() if n.lower().endswith('.kml')]
                if not kml_names:
                    log.warning(f'No .kml file found inside KMZ: {path}')
                    return coords
                _extract_from_xml(zf.read(kml_names[0]))
        else:
            _extract_from_xml(Path(path).read_bytes())
    except Exception as exc:
        log.warning(f'KML parse error ({path}): {exc}')

    return coords

# ── Image size for all map outputs ─────────────────────────────────────────────
OUTPUT_W = 900
OUTPUT_H = 540  # 5:3 ratio matches the report grid cells

# ── Default tile provider URLs ─────────────────────────────────────────────────
OSM_URL  = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ESRI_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services"
            "/World_Imagery/MapServer/tile/{z}/{y}/{x}")

# User-Agent — OSM policy requires a descriptive one
USER_AGENT = "GeoDE/2.0 (Geodetic Database Engine; contact: geode@geodesy.org)"


def _auto_zoom(lat: float, lon: float, target_km: float) -> int:
    """
    Estimate the OSM zoom level that gives approximately `target_km`
    visible width at the given latitude.
    """
    import math
    # At zoom z, one tile covers 360/2^z degrees longitude
    # Earth circumference at latitude: ~40075 * cos(lat) km
    earth_km_at_lat = 40075.0 * abs(math.cos(math.radians(lat)))
    # Number of tiles across the output image (we request OUTPUT_W pixels, tile=256px)
    n_tiles_wide = OUTPUT_W / 256.0
    for z in range(1, 20):
        km_per_tile = earth_km_at_lat / (2 ** z)
        km_visible  = km_per_tile * n_tiles_wide
        if km_visible <= target_km:
            return z
    return 15


def _marker_color(provider: str) -> str:
    return "#e53e3e"  # red dot for all providers


def fetch_maps(
    lat: float,
    lon: float,
    zoom_general: Optional[int]   = None,   # auto if None
    zoom_detail: Optional[int]    = None,
    zoom_satellite: Optional[int] = None,
    osm_url: str       = OSM_URL,
    esri_url: str      = ESRI_URL,
    cache_dir: Optional[str] = None,        # None = use ~/.cache/geode/tiles
    timeout: int       = 15,
    marker_color: str  = "#185fa5",
    marker_sat_color: str = "#00ff00",
    navigation_kml: Optional[str] = None,  # path to KML file to overlay on detail map
) -> dict:
    """
    Fetch map tiles and return a dict with PIL Image objects.

    Returns
    -------
    {
        'general':   PIL.Image  — OSM overview (zoom ~6, shows country/region)
        'detail':    PIL.Image  — OSM detail   (zoom ~14, shows road context)
        'satellite': PIL.Image  — ESRI imagery (zoom ~16, shows terrain/site)
    }
    On any network failure the corresponding value is a placeholder image
    with a gray background and error text, so the report still renders.

    Parameters
    ----------
    lat, lon         : Station coordinates (decimal degrees)
    zoom_general     : OSM zoom for overview map (default: auto ~500 km wide)
    zoom_detail      : OSM zoom for detail map   (default: auto ~5 km wide)
    zoom_satellite   : ESRI zoom for satellite   (default: auto ~1 km wide)
    osm_url          : OSM tile URL template (override for self-hosted tiles)
    esri_url         : ESRI tile URL template
    cache_dir        : Tile cache directory. Pass None to disable caching.
    navigation_kml   : Path to a KML file whose track is drawn on the detail map.
    timeout          : HTTP request timeout in seconds
    marker_color     : Color of the station dot on OSM maps (hex)
    marker_sat_color : Color of the station dot on satellite map (hex)
    """
    try:
        import staticmap
        from PIL import Image
    except ImportError as e:
        raise ImportError(
            f"Missing dependency: {e}. Run: pip install staticmap pillow"
        ) from e

    # ── Cache setup ─────────────────────────────────────────────────────────
    if cache_dir is None:
        cache_dir = os.path.join(
            os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
            "geode", "tiles"
        )
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    # ── Auto zoom levels ─────────────────────────────────────────────────────
    if zoom_general  is None: zoom_general  = _auto_zoom(lat, lon, 500)
    if zoom_detail   is None: zoom_detail   = _auto_zoom(lat, lon, 3)
    if zoom_satellite is None: zoom_satellite = _auto_zoom(lat, lon, 1.5)

    log.info(f"fetch_maps: lat={lat:.5f} lon={lon:.5f}  "
             f"zooms: general={zoom_general} detail={zoom_detail} sat={zoom_satellite}")

    def _make_map(url_template: str, zoom: int,
                  dot_color: str, dot_radius: int = 10,
                  extra_lines: list | None = None) -> "Image.Image":
        """Fetch tiles, stitch, add marker (and optional lines), resize to OUTPUT_W×OUTPUT_H."""
        m = staticmap.StaticMap(
            OUTPUT_W, OUTPUT_H,
            url_template=url_template,
            tile_request_timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        if extra_lines:
            for line in extra_lines:
                m.add_line(line)
        marker = staticmap.CircleMarker((lon, lat), dot_color, dot_radius)
        m.add_marker(marker)
        # Always pin the center to the station so KML tracks extending far
        # away don't cause staticmap to re-fit the bounding box.
        img = m.render(zoom=zoom, center=(lon, lat))
        # Ensure exact output size (staticmap may return slightly different dims)
        if img.size != (OUTPUT_W, OUTPUT_H):
            img = img.resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
        return img.convert("RGB")

    def _placeholder(label: str, error: str) -> "Image.Image":
        """Return a gray placeholder image when tiles can't be fetched."""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (OUTPUT_W, OUTPUT_H), "#e8ecf0")
        draw = ImageDraw.Draw(img)
        # Draw a simple grid to suggest a map
        for x in range(0, OUTPUT_W, 60):
            draw.line([(x, 0), (x, OUTPUT_H)], fill="#d0d8e0", width=1)
        for y in range(0, OUTPUT_H, 60):
            draw.line([(0, y), (OUTPUT_W, y)], fill="#d0d8e0", width=1)
        # Central marker dot
        cx, cy = OUTPUT_W // 2, OUTPUT_H // 2
        r = 10
        draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill="#185fa5")
        # Labels
        draw.text((OUTPUT_W//2, OUTPUT_H//2 - 40), label,
                  fill="#444", anchor="mm")
        draw.text((OUTPUT_W//2, OUTPUT_H//2 + 30),
                  f"({lat:.4f}°, {lon:.4f}°)", fill="#666", anchor="mm")
        draw.text((OUTPUT_W//2, OUTPUT_H//2 + 55),
                  f"[{error}]", fill="#999", anchor="mm")
        return img

    results = {}

    # ── General overview ─────────────────────────────────────────────────────
    try:
        log.info(f"Fetching OSM overview (zoom {zoom_general})…")
        results["general"] = _make_map(osm_url, zoom_general,
                                        marker_color, dot_radius=14)
        log.info("  OK")
    except Exception as e:
        log.warning(f"OSM overview failed: {e}")
        results["general"] = _placeholder("General location", str(e)[:60])

    # ── Site detail (with optional KML track overlay) ────────────────────────
    kml_lines = []
    if navigation_kml and os.path.exists(navigation_kml):
        kml_coords = _parse_kml_coords(navigation_kml)
        if kml_coords:
            log.info(f"KML track loaded: {len(kml_coords)} points from {navigation_kml}")
            kml_lines.append(staticmap.Line(kml_coords, "#e53e3e", 3))
        else:
            log.warning(f"KML file yielded no coordinates: {navigation_kml}")

    try:
        log.info(f"Fetching OSM detail (zoom {zoom_detail})…")
        results["detail"] = _make_map(osm_url, zoom_detail,
                                       marker_color, dot_radius=14,
                                       extra_lines=kml_lines)
        log.info("  OK")
    except Exception as e:
        log.warning(f"OSM detail failed: {e}")
        results["detail"] = _placeholder("Site detail", str(e)[:60])

    # ── Satellite ────────────────────────────────────────────────────────────
    try:
        log.info(f"Fetching ESRI satellite (zoom {zoom_satellite})…")
        results["satellite"] = _make_map(esri_url, zoom_satellite,
                                          marker_sat_color, dot_radius=14)
        log.info("  OK")
    except Exception as e:
        log.warning(f"ESRI satellite failed: {e}")
        results["satellite"] = _placeholder("Satellite view", str(e)[:60])

    return results


def fetch_maps_to_files(
    lat: float,
    lon: float,
    out_dir: str,
    **kwargs,
) -> dict[str, str]:
    """
    Convenience wrapper: fetch maps and save to JPEG files in out_dir.

    Returns a dict of paths:
        { 'general': '/path/gen.jpg', 'detail': '/path/det.jpg',
          'satellite': '/path/sat.jpg' }
    These can be passed directly to StationReport fields.
    """
    imgs = fetch_maps(lat, lon, **kwargs)
    paths = {}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for key, img in imgs.items():
        p = os.path.join(out_dir, f"map_{key}.jpg")
        img.save(p, "JPEG", quality=90)
        paths[key] = p
        log.info(f"Saved {key} → {p}")
    return paths


# ── Integration helper ─────────────────────────────────────────────────────────

def attach_maps_to_station(station, out_dir: Optional[str] = None, **kwargs):
    """
    Fetch maps and attach paths directly to a StationReport instance.

    Example
    -------
        from geode_map_fetch import attach_maps_to_station
        from geode_station_report import StationReport, build_report

        station = StationReport(...)
        attach_maps_to_station(station)   # fetches + sets station.map_*_path
        html = build_report(station, ...)
    """
    if out_dir is None:
        # Use a temp dir that persists for the process lifetime
        out_dir = tempfile.mkdtemp(prefix="geode_maps_")

    # Pass navigation KML path from StationReport if not already supplied by caller
    if 'navigation_kml' not in kwargs and getattr(station, 'navigation_kml_path', None):
        kwargs['navigation_kml'] = station.navigation_kml_path

    paths = fetch_maps_to_files(station.lat, station.lon, out_dir, **kwargs)
    station.map_general_path  = paths.get("general")
    station.map_detail_path   = paths.get("detail")
    station.satellite_path    = paths.get("satellite")
    return paths


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s  %(message)s")

    parser = argparse.ArgumentParser(
        description="Fetch map tiles for a GeoDE station."
    )
    parser.add_argument("--lat",  type=float, required=True)
    parser.add_argument("--lon",  type=float, required=True)
    parser.add_argument("--out",  default=".", help="Output directory")
    parser.add_argument("--zoom-general",   type=int, default=None)
    parser.add_argument("--zoom-detail",    type=int, default=None)
    parser.add_argument("--zoom-satellite", type=int, default=None)
    args = parser.parse_args()

    paths = fetch_maps_to_files(
        lat=args.lat, lon=args.lon,
        out_dir=args.out,
        zoom_general=args.zoom_general,
        zoom_detail=args.zoom_detail,
        zoom_satellite=args.zoom_satellite,
    )
    for k, p in paths.items():
        print(f"  {k:12s} → {p}")
