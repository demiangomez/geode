#!/usr/bin/env python3
"""
station_kmz.py
==============
Generates Google Earth KMZ files for GeoDE GNSS stations.

Each station is represented as a KML Folder containing:
  - A Placemark with a colour-coded dot icon and an HTML balloon popup
  - One sub-folder per visit that holds an embedded navigation KMZ
    (when api_visits.navigation_filename resolves to an existing file)

The balloon HTML is table-based for compatibility with Google Earth's
embedded Chromium renderer (420 px fixed width, inline styles only,
data: URIs for all images).
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Re-use helpers from the station report module
from .station_report import _encode_image, _encode_logo, _flag, _fmt_lat, _fmt_lon

# ── Status colour map (icon-class name → hex) ─────────────────────────────────
_STATUS_COLOR_MAP = {
    "green-icon":       "#28a745",
    "light-green-icon": "#6fcf97",
    "yellow-icon":      "#f0c040",
    "light-gray-icon":  "#d3d3d3",
    "gray-icon":        "#6c757d",
    "light-red-icon":   "#f08080",
    "granate-icon":     "#800000",
    "blue-icon":        "#185fa5",
    "lilac-icon":       "#c8a2c8",
    "purple-icon":      "#6f42c1",
    "light-blue-icon":  "#6ab0de",
    "orange-icon":      "#fd7e14",
}


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class KmzVisit:
    """One visit row for the visit history table and optional navigation folder."""
    visit_id:            int
    date:                str               # "YYYY-MM-DD"
    date_end:            Optional[str] = None
    campaign:            Optional[str] = None
    navigation_abs_path: Optional[str] = None   # resolved absolute path (or None)


@dataclass
class KmzStation:
    """All data needed to build one station's KMZ folder and balloon popup."""
    network:       str
    station:       str
    country:       str
    status:        str
    status_color:  Optional[str]
    station_type:  str
    comms:         bool
    lat:           float
    lon:           float
    height:        float
    location_desc: str
    monument:      str
    first_rinex:   Optional[str] = None
    last_rinex:    Optional[str] = None
    comments:      Optional[str] = None
    receiver:      Optional[str] = None    # most-recent receiver type
    antenna:       Optional[str] = None    # most-recent antenna type
    x_ecef:          float = 0.0
    y_ecef:          float = 0.0
    z_ecef:          float = 0.0
    monument_path:   Optional[str] = None    # absolute path to monument photo
    default_nav_path: Optional[str] = None  # absolute path to station-level default route KMZ
    station_images: list = field(default_factory=list)  # [(abs_path, caption), …]
    visits:        list = field(default_factory=list)   # list[KmzVisit]
    logo_path:     Optional[str] = None


# ── Icon helpers ───────────────────────────────────────────────────────────────

def _make_dot_png(hex_color: str, size: int = 32) -> bytes:
    """Generate a transparent PNG with a coloured circle for use as the KML icon."""
    try:
        from PIL import Image, ImageDraw
        img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        # White outer ring
        draw.ellipse([(1, 1), (size - 2, size - 2)], fill=(255, 255, 255, 200))
        # Coloured fill, inset
        m = 4
        draw.ellipse([(m, m), (size - 1 - m, size - 1 - m)], fill=(r, g, b, 255))
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue()
    except Exception:
        return b''


# ── Date-range formatting ─────────────────────────────────────────────────────

def _visit_period(date: str, date_end: Optional[str]) -> str:
    """Format a visit date (or date range) for the visit history table."""
    import datetime as _dt
    fmt = '%Y-%m-%d'
    if date_end and date_end != date:
        try:
            d1 = _dt.datetime.strptime(date[:10],     fmt)
            d2 = _dt.datetime.strptime(date_end[:10], fmt)
            if d1.month == d2.month:
                return f'{d1.strftime("%b %d")}\u2013{d2.strftime("%d, %Y")}'
            return f'{d1.strftime("%b %d")} \u2013 {d2.strftime("%b %d, %Y")}'
        except Exception:
            return f'{date[:10]}\u2013{date_end[:10]}'
    try:
        return _dt.datetime.strptime(date[:10], fmt).strftime('%b %d, %Y')
    except Exception:
        return date[:10]


# ── Chip helpers ──────────────────────────────────────────────────────────────

def _status_chip(status: str, status_color: Optional[str]) -> str:
    """Render the status chip with the DB colour when available."""
    hex_c = _STATUS_COLOR_MAP.get(status_color or '', None)
    if hex_c:
        h = hex_c.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        bg = f'rgba({r},{g},{b},0.15)'
        return (f'<span style="background:{bg};color:{hex_c};font-size:10px;'
                f'font-weight:700;padding:2px 8px;border-radius:999px">'
                f'&#10003; {status}</span>')
    # fallback
    if (status or '').lower() == 'active':
        return (f'<span style="background:#eaf3de;color:#3b6d11;font-size:10px;'
                f'font-weight:700;padding:2px 8px;border-radius:999px">'
                f'&#10003; {status}</span>')
    return (f'<span style="background:#faeeda;color:#854f0b;font-size:10px;'
            f'font-weight:700;padding:2px 8px;border-radius:999px">{status}</span>')


# ── Photo grid helper ─────────────────────────────────────────────────────────

def _photo_grid_html(images: list) -> str:
    """
    Build a 2-column photo grid table from a list of (data_uri, caption) pairs.
    Up to 4 images; odd-image rows pad the second cell with gray.
    Returns empty string if images is empty.
    """
    if not images:
        return ''
    display   = images[:4]
    rows_html = ''
    n = len(display)
    for i in range(0, n, 2):
        pair       = display[i:i + 2]
        is_last    = (i + 2 >= n)
        cells      = ''
        for j, (uri, cap) in enumerate(pair):
            br = '' if (j == 1 or len(pair) == 1) else 'border-right:0.5px solid #e0ded8;'
            bb = '' if is_last else 'border-bottom:0.5px solid #e0ded8;'
            cells += (
                f'<td style="width:50%;padding:0;{br}{bb}">'
                f'<div style="position:relative;overflow:hidden">'
                f'<img src="{uri}" width="100%" style="display:block">'
                f'<div style="position:absolute;bottom:0;left:0;right:0;'
                f'background:rgba(0,0,0,0.52);color:#fff;font-size:9px;'
                f'font-weight:600;padding:3px 7px">{cap}</div>'
                f'</div></td>'
            )
        if len(pair) == 1:
            cells += '<td style="width:50%;background:#f5f5f3"></td>'
        rows_html += f'<tr>{cells}</tr>'

    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:0.5px solid #e0ded8;border-radius:6px;'
        f'overflow:hidden;margin-bottom:4px"><tbody>'
        f'{rows_html}</tbody></table>'
    )


# ── Main balloon builder ───────────────────────────────────────────────────────

def build_balloon_html(s: KmzStation) -> str:
    """
    Return the full HTML string for one station's Google Earth balloon popup.

    Layout (top to bottom)
    ----------------------
    1. Dark header   — network · station, location_desc, logo
    2. Status chips  — country flag + code, status, comms, station type
    3. Coordinates   — 3-column table (lat, lon, height)
    4. Station info  — kv table (type, monument, receiver, antenna, RINEX dates)
    5. Site imagery  — 2×2 grid of station photos + full-width monument photo
    6. Visit history — 2-column table (period, campaign)
    7. Comments      — amber comment box (omitted when empty)
    8. Footer        — GeoDE label + station tag
    """
    # ── Logo ──────────────────────────────────────────────────────────────────
    logo_uri = _encode_logo(s.logo_path) or ''
    logo_img = (f'<img src="{logo_uri}" style="height:28px;width:auto;opacity:.9">'
                if logo_uri else '')

    # ── 1. Header ─────────────────────────────────────────────────────────────
    header = (
        f'<table width="100%" cellpadding="0" cellspacing="0"'
        f' style="background:#1a1a18;border-radius:8px 8px 0 0;margin-bottom:0">'
        f'<tr>'
        f'<td style="padding:10px 14px">'
        f'<div style="color:#fff;font-size:16px;font-weight:700;letter-spacing:.01em">'
        f'{s.network.upper()} &middot; {s.station.upper()}</div>'
        f'<div style="color:#9b9a96;font-size:10px;margin-top:2px">{s.location_desc}</div>'
        f'</td>'
        f'<td style="padding:10px 14px;text-align:right;vertical-align:middle">{logo_img}</td>'
        f'</tr></table>'
    )

    # ── 2. Status chips ───────────────────────────────────────────────────────
    flag_img     = _flag(s.country)
    country_chip = (
        f'<span style="background:#e6f1fb;color:#0c447c;font-size:10px;font-weight:700;'
        f'padding:2px 8px;border-radius:999px">{flag_img} {s.country}</span>'
    )
    status_chip  = _status_chip(s.status, s.status_color)
    type_chip    = (
        f'<span style="background:#e6f1fb;color:#0c447c;font-size:10px;font-weight:700;'
        f'padding:2px 8px;border-radius:999px">{s.station_type}</span>'
    )
    chips = (
        f'<div style="background:#f5f5f3;padding:7px 14px;display:flex;gap:5px;'
        f'flex-wrap:wrap;border-bottom:1px solid #e0ded8">'
        f'{country_chip}{status_chip}{type_chip}</div>'
    )

    # ── 3. Coordinates ────────────────────────────────────────────────────────
    def _fmt_ecef(v: float) -> str:
        sign = '&minus;' if v < 0 else '+'
        return f'{sign}{abs(v):,.3f} m'

    _br = 'border-right:0.5px solid #e0ded8;'
    _bt = 'border-top:0.5px solid #e0ded8;'
    coords = (
        f'<div style="padding:10px 14px 4px">'
        f'<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;color:#9b9a96;margin-bottom:6px">Coordinates</div>'
        f'<table width="100%" cellpadding="0" cellspacing="0"'
        f' style="background:#f5f5f3;border-radius:6px;overflow:hidden">'
        f'<tr>'
        f'<td style="padding:6px 10px;{_br}width:33%">'
        f'<div style="font-size:9.5px;color:#6b6a66">Latitude</div>'
        f'<div style="font-size:11.5px;font-weight:700">{_fmt_lat(s.lat)}</div></td>'
        f'<td style="padding:6px 10px;{_br}width:33%">'
        f'<div style="font-size:9.5px;color:#6b6a66">Longitude</div>'
        f'<div style="font-size:11.5px;font-weight:700">{_fmt_lon(s.lon)}</div></td>'
        f'<td style="padding:6px 10px;width:33%">'
        f'<div style="font-size:9.5px;color:#6b6a66">Height</div>'
        f'<div style="font-size:11.5px;font-weight:700">{s.height:.3f} m</div></td>'
        f'</tr>'
        f'<tr>'
        f'<td style="padding:6px 10px;{_br}{_bt}width:33%">'
        f'<div style="font-size:9.5px;color:#6b6a66">X (ECEF)</div>'
        f'<div style="font-size:11.5px;font-weight:700">{_fmt_ecef(s.x_ecef)}</div></td>'
        f'<td style="padding:6px 10px;{_br}{_bt}width:33%">'
        f'<div style="font-size:9.5px;color:#6b6a66">Y (ECEF)</div>'
        f'<div style="font-size:11.5px;font-weight:700">{_fmt_ecef(s.y_ecef)}</div></td>'
        f'<td style="padding:6px 10px;{_bt}width:33%">'
        f'<div style="font-size:9.5px;color:#6b6a66">Z (ECEF)</div>'
        f'<div style="font-size:11.5px;font-weight:700">{_fmt_ecef(s.z_ecef)}</div></td>'
        f'</tr>'
        f'</table></div>'
    )

    # ── 4. Station info ───────────────────────────────────────────────────────
    def _kv(label, value, bg, last=False):
        bb = '' if last else 'border-bottom:0.5px solid #eee;'
        return (f'<tr style="background:{bg}">'
                f'<td style="padding:5px 10px;font-size:11px;color:#6b6a66;width:40%;{bb}">{label}</td>'
                f'<td style="padding:5px 10px;font-size:11px;font-weight:600;{bb}">{value}</td>'
                f'</tr>')

    station_info = (
        f'<div style="padding:8px 14px 4px">'
        f'<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;color:#9b9a96;margin-bottom:6px">Station Info</div>'
        f'<table width="100%" cellpadding="0" cellspacing="0"'
        f' style="border:0.5px solid #e0ded8;border-radius:6px;overflow:hidden">'
        + _kv('Station type', s.station_type,              '#f5f5f3')
        + _kv('Monument',     s.monument,                  '#fff')
        + _kv('Receiver',     s.receiver or '&mdash;',     '#f5f5f3')
        + _kv('Antenna',      s.antenna  or '&mdash;',     '#fff')
        + _kv('First RINEX',  (s.first_rinex or 'N/A')[:10], '#f5f5f3')
        + _kv('Last RINEX',   (s.last_rinex  or 'N/A')[:10], '#fff', last=True)
        + f'</table></div>'
    )

    # ── 5. Site imagery ───────────────────────────────────────────────────────
    imagery_section = ''
    encoded_photos  = []
    for path, cap in (s.station_images or [])[:4]:
        uri = _encode_image(path, max_size=(400, 300), quality=82)
        if uri:
            encoded_photos.append((uri, cap))
    mon_uri = _encode_image(s.monument_path, max_size=(840, 480), quality=85)

    if encoded_photos or mon_uri:
        imagery_section = (
            f'<div style="padding:8px 14px 4px">'
            f'<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.06em;color:#9b9a96;margin-bottom:6px">Site imagery</div>'
        )
        if encoded_photos:
            imagery_section += _photo_grid_html(encoded_photos)
        if mon_uri:
            imagery_section += (
                f'<table width="100%" cellpadding="0" cellspacing="0"'
                f' style="border:0.5px solid #e0ded8;border-radius:6px;overflow:hidden">'
                f'<tr><td style="padding:0">'
                f'<div style="position:relative;overflow:hidden">'
                f'<img src="{mon_uri}" width="100%" style="display:block">'
                f'<div style="position:absolute;bottom:0;left:0;right:0;'
                f'background:rgba(0,0,0,0.52);color:#fff;font-size:9px;'
                f'font-weight:600;padding:3px 7px">Monument &middot; {s.monument}</div>'
                f'</div></td></tr></table>'
            )
        imagery_section += '</div>'

    # ── 6. Visit history ──────────────────────────────────────────────────────
    visits_section = ''
    if s.visits:
        n_v       = len(s.visits)
        rows_html = ''
        for i, v in enumerate(s.visits):
            bg     = '#f8f8f6' if i % 2 == 0 else '#ffffff'
            period = _visit_period(v.date, v.date_end)
            last   = (i == n_v - 1)
            bb     = '' if last else 'border-bottom:0.5px solid #eee;'
            camp   = (
                f'<span style="color:#185fa5;font-size:10px;font-weight:600">{v.campaign}</span>'
                if v.campaign else
                '<span style="color:#aaa;font-size:10px;font-style:italic">No campaign</span>'
            )
            rows_html += (
                f'<tr style="background:{bg}">'
                f'<td style="padding:4px 8px;font-size:11px;color:#444;white-space:nowrap;{bb}">'
                f'{period}</td>'
                f'<td style="padding:4px 8px;{bb}">{camp}</td>'
                f'</tr>'
            )
        visits_section = (
            f'<div style="padding:8px 14px 4px">'
            f'<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.06em;color:#9b9a96;margin-bottom:6px">'
            f'Visit history &nbsp;&middot;&nbsp; {n_v} visit{"s" if n_v != 1 else ""}</div>'
            f'<table width="100%" cellpadding="0" cellspacing="0"'
            f' style="border:0.5px solid #e0ded8;border-radius:6px;overflow:hidden">'
            f'<tr style="background:#e6f1fb">'
            f'<th style="padding:5px 8px;font-size:10px;font-weight:700;text-align:left;'
            f'color:#185fa5;border-bottom:0.5px solid #c5d9ef">Date</th>'
            f'<th style="padding:5px 8px;font-size:10px;font-weight:700;text-align:left;'
            f'color:#185fa5;border-bottom:0.5px solid #c5d9ef">Campaign</th>'
            f'</tr>'
            f'{rows_html}'
            f'</table></div>'
        )

    # ── 7. Comments ───────────────────────────────────────────────────────────
    comments_section = ''
    if s.comments:
        comments_section = (
            f'<div style="margin:8px 14px 4px;background:#fff8ed;'
            f'border:0.5px solid #d4a855;border-left:3px solid #d4a855;'
            f'border-radius:0 6px 6px 0;padding:8px 12px">'
            f'<div style="font-size:10px;font-weight:700;color:#854f0b;margin-bottom:4px">'
            f'General comments</div>'
            f'<div style="font-size:10.5px;color:#5a4a2f;line-height:1.55">{s.comments}</div>'
            f'</div>'
        )

    # ── 8. Footer ─────────────────────────────────────────────────────────────
    footer = (
        f'<table width="100%" cellpadding="0" cellspacing="0"'
        f' style="background:#f5f5f3;border-top:0.5px solid #e0ded8;'
        f'border-radius:0 0 8px 8px;margin-top:8px">'
        f'<tr>'
        f'<td style="padding:6px 14px;font-size:9.5px;color:#9b9a96;text-align:left">'
        f'GeoDE &middot; Geodetic Database Engine</td>'
        f'<td style="padding:6px 14px;font-size:9.5px;color:#9b9a96;text-align:right">'
        f'{s.network.upper()}.{s.station.upper()} ({s.country})</td>'
        f'</tr></table>'
    )

    return (
        '<html>\n'
        '<head><meta charset="UTF-8"></head>\n'
        '<body style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
        'color:#1a1a18;margin:0;padding:0;width:420px">\n'
        + header
        + chips
        + coords
        + station_info
        + imagery_section
        + visits_section
        + comments_section
        + footer
        + '\n</body>\n</html>'
    )


# ── Database query ─────────────────────────────────────────────────────────────

def station_data_from_db(cnn,
                          network_code: str,
                          station_code: str,
                          media_path:   Optional[str] = None) -> KmzStation:
    """
    Build a KmzStation by querying the GeoDE PostgreSQL database.

    Parameters
    ----------
    cnn          : geode.dbConnection.Cnn — active DB connection
    network_code : str  — e.g. 'ARS'
    station_code : str  — e.g. 'AT47'
    media_path   : str  — MEDIA_ROOT from gnss_data.cfg [archive] media
    """
    nc = network_code
    sc = station_code.lower()

    if media_path and not os.path.isdir(media_path):
        print(f' !! Warning: media path not accessible: {media_path}', file=sys.stderr)
        print(f' !! Images will be omitted.', file=sys.stderr)
        media_path = None

    def _media(rel):
        if not rel or not media_path:
            return None
        full = os.path.join(media_path, str(rel))
        return full if os.path.exists(full) else None

    # ── 1. Core station row ──────────────────────────────────────────────────
    rows = cnn.query_float(
        f"""SELECT "NetworkCode", "StationCode", "StationName",
                   lat, lon, height, auto_x, auto_y, auto_z,
                   country_code, api_id
            FROM stations
            WHERE "NetworkCode" = '{nc}' AND "StationCode" = '{sc}'""",
        as_dict=True)
    if not rows:
        raise ValueError(f'Station {nc.upper()}.{sc.upper()} not found in the database')
    s      = rows[0]
    api_id = int(s['api_id'])
    lat    = float(s['lat']    or 0)
    lon    = float(s['lon']    or 0)
    height = float(s['height'] or 0)
    x_ecef = float(s['auto_x'] or 0)
    y_ecef = float(s['auto_y'] or 0)
    z_ecef = float(s['auto_z'] or 0)
    country   = str(s['country_code'] or '')
    loc_desc  = str(s['StationName']  or f'{nc}.{sc}')

    # ── 2. Station metadata ──────────────────────────────────────────────────
    status        = 'Unknown'
    status_color  = None
    station_type  = 'Unknown'
    has_comms     = False
    monument      = 'N/A'
    monument_path = None
    comments      = None

    default_nav_path = None

    meta = cnn.query_float(
        f"""SELECT m.has_communications, m.comments,
                   m.navigation_file,
                   st.name        AS status_name,
                   stype.name     AS station_type_name,
                   mon.name       AS monument_type_name,
                   mon.photo_path AS monument_image,
                   scolor.color   AS status_color
            FROM api_stationmeta m
            JOIN api_stationstatus         st    ON m.status_id        = st.id
            LEFT JOIN api_stationstatuscolor scolor ON st.color_id    = scolor.id
            JOIN api_stationtype           stype  ON m.station_type_id = stype.id
            LEFT JOIN api_monumenttype     mon    ON m.monument_type_id = mon.id
            WHERE m.station_id = {api_id}""",
        as_dict=True)
    if meta:
        m                = meta[0]
        status           = str(m.get('status_name')        or 'Unknown')
        status_color     = str(m.get('status_color')       or '').strip() or None
        station_type     = str(m.get('station_type_name')  or 'Unknown')
        has_comms        = bool(m.get('has_communications', False))
        monument         = str(m.get('monument_type_name') or 'N/A')
        monument_path    = _media(m.get('monument_image')) if m.get('monument_image') else None
        comments         = (str(m.get('comments') or '').strip()) or None
        default_nav_path = _media(m.get('navigation_file')) if m.get('navigation_file') else None

    # ── 3. RINEX date range ──────────────────────────────────────────────────
    rinex = cnn.query_float(
        f"""SELECT MIN("ObservationSTime") AS first_rinex,
                   MAX("ObservationETime") AS last_rinex
            FROM rinex_proc
            WHERE "NetworkCode" = '{nc}' AND "StationCode" = '{sc}'
              AND "Completion" >= 0.5""",
        as_dict=True)
    first_rinex = last_rinex = None
    if rinex and rinex[0].get('first_rinex') is not None:
        first_rinex = str(rinex[0]['first_rinex'])[:10]
        last_rinex  = str(rinex[0]['last_rinex'])[:10]

    # ── 4. Most-recent instrument session ────────────────────────────────────
    receiver = antenna = None
    instr = cnn.query_float(
        f"""SELECT "ReceiverCode", "AntennaCode"
            FROM stationinfo
            WHERE "NetworkCode" = '{nc}' AND "StationCode" = '{sc}'
            ORDER BY "DateStart" DESC LIMIT 1""",
        as_dict=True)
    if instr:
        receiver = str(instr[0].get('ReceiverCode') or '').strip() or None
        antenna  = str(instr[0].get('AntennaCode')  or '').strip() or None

    # ── 5. Station photos (up to 4 for the 2×2 grid) ────────────────────────
    station_images = []
    img_rows = cnn.query_float(
        f"""SELECT image, description FROM api_stationimages
            WHERE station_id = {api_id}
            ORDER BY id LIMIT 4""",
        as_dict=True)
    for r in img_rows:
        p = _media(r.get('image'))
        if p:
            cap = str(r.get('description') or '').strip()
            station_images.append((p, cap))

    # ── 6. Visits (DESC order; navigation file resolved to absolute path) ────
    visits = []
    visit_rows = cnn.query_float(
        f"""SELECT v.id, v.date, v.navigation_file, v.navigation_filename,
                   c.name AS campaign_name
            FROM api_visits v
            LEFT JOIN api_campaigns c ON v.campaign_id = c.id
            WHERE v.station_id = {api_id}
            ORDER BY v.date DESC""",
        as_dict=True)
    for vr in visit_rows:
        nav_rel  = vr.get('navigation_file')      # Django FileField — relative path within media
        nav_name = vr.get('navigation_filename')   # original display name
        nav_abs  = _media(nav_rel) if nav_rel else None
        visit_date = str(vr['date'])[:10] if vr.get('date') else ''
        if nav_rel:
            if nav_abs:
                print(f'    visit {visit_date}: nav [OK] {nav_abs}', file=sys.stderr)
            else:
                tried = os.path.join(media_path, nav_rel) if media_path else '(media_path is None)'
                print(f'    visit {visit_date}: nav [NOT FOUND] tried={tried!r}',
                      file=sys.stderr)
        visits.append(KmzVisit(
            visit_id            = int(vr['id']),
            date                = visit_date,
            campaign            = vr.get('campaign_name') or None,
            navigation_abs_path = nav_abs,
        ))

    # ── Logo (bundled with the package) ──────────────────────────────────────
    _logo     = Path(__file__).parent / 'geode_logo.png'
    logo_path = str(_logo) if _logo.exists() else None

    return KmzStation(
        network        = nc.upper(),
        station        = sc.upper(),
        country        = country,
        status         = status,
        status_color   = status_color,
        station_type   = station_type,
        comms          = has_comms,
        lat            = lat,
        lon            = lon,
        height         = height,
        location_desc  = loc_desc,
        monument       = monument,
        first_rinex    = first_rinex,
        last_rinex     = last_rinex,
        comments       = comments,
        receiver       = receiver,
        antenna        = antenna,
        x_ecef         = x_ecef,
        y_ecef         = y_ecef,
        z_ecef         = z_ecef,
        monument_path    = monument_path,
        default_nav_path = default_nav_path,
        station_images   = station_images,
        visits           = visits,
        logo_path        = logo_path,
    )


# ── Navigation KML embedding ──────────────────────────────────────────────────

_KML_NS = 'http://www.opengis.net/kml/2.2'


def _read_kml_from_file(path: str) -> Optional[bytes]:
    """Return raw KML bytes from a .kml or .kmz file, or None on failure."""
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            names = zf.namelist()
            kn = 'doc.kml' if 'doc.kml' in names else next(
                (n for n in names if n.endswith('.kml')), None)
            return zf.read(kn) if kn else None
    except zipfile.BadZipFile:
        # Plain KML file
        try:
            with open(path, 'rb') as fh:
                return fh.read()
        except OSError:
            return None


def _embed_nav_content(output_path: str,
                        nav_embed: list) -> None:
    """
    Post-process the saved KMZ to embed navigation KML/KMZ content directly
    inside the matching visit sub-folders.

    nav_embed : list of (station_folder_name, visit_date, abs_path_to_nav_file)

    The output KMZ is rewritten in-place.
    """
    ET.register_namespace('', _KML_NS)

    def _tag(local):
        return f'{{{_KML_NS}}}{local}'

    def _find_child_folder(parent, name):
        for child in parent:
            if child.tag == _tag('Folder'):
                n = child.find(_tag('name'))
                if n is not None and n.text == name:
                    return child
        return None

    # Read the freshly-written KMZ
    with zipfile.ZipFile(output_path, 'r') as zin:
        names    = zin.namelist()
        kml_name = 'doc.kml' if 'doc.kml' in names else next(
            (n for n in names if n.endswith('.kml')), None)
        if not kml_name:
            return
        doc_bytes = zin.read(kml_name)
        embedded  = {n: zin.read(n) for n in names if n != kml_name}

    root   = ET.fromstring(doc_bytes)
    doc_el = root.find(_tag('Document')) or root

    modified = False
    for station_name, visit_date, nav_path in nav_embed:
        stn_el   = _find_child_folder(doc_el, station_name)
        if stn_el is None:
            continue
        visit_el = _find_child_folder(stn_el, visit_date)
        if visit_el is None:
            continue

        nav_kml_bytes = _read_kml_from_file(nav_path)
        if nav_kml_bytes is None:
            print(f' !! could not read nav KML from {nav_path}', file=sys.stderr)
            continue

        # Also copy any support files (icons, images) from nav KMZ
        try:
            with zipfile.ZipFile(nav_path, 'r') as nav_zip:
                prefix = f'files/nav_{visit_date.replace("-", "")}_'
                for nn in nav_zip.namelist():
                    if not nn.endswith('.kml'):
                        key = prefix + os.path.basename(nn)
                        embedded[key] = nav_zip.read(nn)
        except zipfile.BadZipFile:
            pass   # plain KML — no support files

        try:
            nav_root = ET.fromstring(nav_kml_bytes)
        except ET.ParseError as exc:
            print(f' !! malformed KML in {nav_path}: {exc}', file=sys.stderr)
            continue

        # Append children of the nav Document (skip its <name>)
        nav_doc = nav_root.find(_tag('Document')) or nav_root
        for child in nav_doc:
            if child.tag != _tag('name'):
                visit_el.append(child)

        print(f'    embedded nav content from {os.path.basename(nav_path)} '
              f'into {station_name}/{visit_date}', file=sys.stderr)
        modified = True

    if not modified:
        return

    # Rewrite the KMZ with embedded nav content
    fd, tmp_path = tempfile.mkstemp(suffix='.kmz')
    os.close(fd)
    try:
        new_kml = ('<?xml version="1.0" encoding="utf-8"?>\n'
                   + ET.tostring(root, encoding='unicode'))
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            zout.writestr(kml_name, new_kml.encode('utf-8'))
            for name, data in embedded.items():
                zout.writestr(name, data)
        shutil.move(tmp_path, output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── KMZ builder ───────────────────────────────────────────────────────────────

def build_kmz(stations: list, output_path: str) -> None:
    """
    Build a Google Earth KMZ for one or more KmzStation objects and write it to
    ``output_path``.

    KMZ structure
    -------------
    Document
      Folder "NC.SC"  ← one per station
        Placemark           ← coloured dot icon + HTML balloon
        Folder "Default route"  ← station-level route (api_stationmeta.navigation_file)
        Folder "YYYY-MM-DD"     ← one per visit with a navigation file
          (nav KML content embedded directly in each folder)
      ScreenOverlay    ← GeoDE logo at the bottom-left of the GE window
    """
    try:
        import simplekml
    except ImportError:
        raise ImportError('simplekml is required. Run: pip install simplekml')

    kml     = simplekml.Kml(name='GeoDE Stations')
    tmp_dir = tempfile.mkdtemp(prefix='geode_kmz_')

    try:
        _icon_hrefs: dict = {}   # hex_color → KMZ-internal href string
        nav_embed:   list = []   # (station_name, visit_date, nav_abs_path)

        for station in stations:
            stn_name = f'{station.network}.{station.station}'
            s_folder = kml.newfolder(name=stn_name)

            # ── Placemark ─────────────────────────────────────────────────────
            pnt = s_folder.newpoint(
                name   = stn_name,
                coords = [(station.lon, station.lat, station.height)],
            )
            pnt.description             = build_balloon_html(station)
            pnt.style.balloonstyle.text = '$[description]'

            # Coloured dot icon — one PNG per unique status colour
            hex_c = _STATUS_COLOR_MAP.get(station.status_color or '', '#185fa5')
            if hex_c not in _icon_hrefs:
                dot_bytes = _make_dot_png(hex_c)
                if dot_bytes:
                    icon_name = f'dot_{hex_c.lstrip("#")}.png'
                    icon_tmp  = os.path.join(tmp_dir, icon_name)
                    Path(icon_tmp).write_bytes(dot_bytes)
                    _icon_hrefs[hex_c] = kml.addfile(icon_tmp)
                else:
                    _icon_hrefs[hex_c] = ''

            if _icon_hrefs[hex_c]:
                pnt.style.iconstyle.icon.href = _icon_hrefs[hex_c]
                pnt.style.iconstyle.scale     = 0.7

            # ── Default route (station-level navigation) ──────────────────────
            _DEFAULT_FOLDER = 'Default route'
            if station.default_nav_path and os.path.exists(station.default_nav_path):
                s_folder.newfolder(name=_DEFAULT_FOLDER)
                nav_embed.append((stn_name, _DEFAULT_FOLDER, station.default_nav_path))

            # ── Visit folders (nav content will be embedded in post-process) ──
            for v in station.visits:
                nav_path = v.navigation_abs_path
                if nav_path and os.path.exists(nav_path):
                    s_folder.newfolder(name=v.date)
                    nav_embed.append((stn_name, v.date, nav_path))

        # ── ScreenOverlay: GeoDE logo at bottom-left of GE viewport ──────────
        logo_path = next((st.logo_path for st in stations if st.logo_path), None)
        if logo_path and os.path.exists(logo_path):
            logo_href = kml.addfile(logo_path)
            so = kml.newscreenoverlay(name='GeoDE Logo')
            so.icon.href  = logo_href
            so.overlayxy  = simplekml.OverlayXY(
                x=0, y=0,
                xunits=simplekml.Units.fraction, yunits=simplekml.Units.fraction)
            so.screenxy   = simplekml.ScreenXY(
                x=0.01, y=0.03,
                xunits=simplekml.Units.fraction, yunits=simplekml.Units.fraction)
            so.rotationxy = simplekml.RotationXY(
                x=0.5, y=0.5,
                xunits=simplekml.Units.fraction, yunits=simplekml.Units.fraction)
            so.size = simplekml.Size(
                x=200, y=96,
                xunits=simplekml.Units.pixels, yunits=simplekml.Units.pixels)

        kml.savekmz(output_path)

        # Post-process: embed navigation KML content directly into visit folders
        if nav_embed:
            _embed_nav_content(output_path, nav_embed)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
