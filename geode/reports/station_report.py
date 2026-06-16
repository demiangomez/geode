#!/usr/bin/env python3
"""
geode_station_report.py
=======================
Generates a GeoDE GNSS station report as a self-contained HTML file
and optionally renders it to PDF via weasyprint.

Usage (standalone / dev):
    python geode_station_report.py --output report.html
    python geode_station_report.py --output report.pdf --pdf

Integration with GeoDE:
    from geode_station_report import build_report, render_pdf
    html = build_report(station)
    render_pdf(html, "ARS_AT47_report.pdf")

The `station` dict is designed to match GeoDE's DB schema — adapt the
`station_from_db()` helper at the bottom to query your actual tables.
"""

from __future__ import annotations

import base64
import datetime
import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Optional map fetcher ──────────────────────────────────────────────────────
try:
    from .map_fetch import attach_maps_to_station
    MAP_FETCH_AVAILABLE = True
except ImportError:
    MAP_FETCH_AVAILABLE = False

# ── Optional PDF backend ───────────────────────────────────────────────────────
try:
    import weasyprint
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class InstrumentSession:
    """One row from the station_info / gamit_station_info table."""
    date_start:  str          # "YYYY-MM-DD"
    date_end:    str          # "YYYY-MM-DD"
    receiver:    str
    rcvr_sn:     str
    firmware:    str
    version:     str
    antenna:     str
    ant_sn:      str
    ant_h:       float        # metres
    height_code: str
    radome:      str


@dataclass
class Visit:
    """One visit/campaign entry."""
    date:          str                    # "YYYY-MM-DD"
    date_end:      Optional[str] = None  # if multi-day
    campaign:      Optional[str] = None
    personnel:     list[str] = field(default_factory=list)
    obs_files:       list[str] = field(default_factory=list)
    att_files:       list[str] = field(default_factory=list)
    obs_files_total: int = 0   # DB count (may exceed len(obs_files) when capped)
    att_files_total: int = 0
    photo_paths:     list[str] = field(default_factory=list)  # local file paths
    photo_captions:  list[str] = field(default_factory=list)
    comments:        Optional[str] = None


@dataclass
class Contact:
    initials:  str
    name:      str
    role:      str   # "Local Coordinator" | "Site Owner" | "Station Manager" | "Network Manager"
    phone:     Optional[str] = None
    email:     Optional[str] = None
    note:      Optional[str] = None


@dataclass
class StationReport:
    # Identity
    network:     str
    station:     str
    country:     str           # ISO-3166 alpha-3, e.g. "ARG"
    status:      str           # "Active" | "Inactive"
    comms:       bool          # has remote communication
    station_type:str           # "Campaign" | "Continuous"

    # Location
    lat:         float         # decimal degrees, negative = South
    lon:         float         # decimal degrees, negative = West
    height:      float         # ellipsoidal height, metres
    x_ecef:      float
    y_ecef:      float
    z_ecef:      float
    location_desc: str         # e.g. "Ruta Nacional 40 · San Juan Province, Argentina"

    # Monument
    monument:    str           # e.g. "Bevis Pin"

    # Status colour (hex from api_stationstatuscolor, e.g. "#28a745")
    status_color: Optional[str] = None

    # Navigation KML track (plotted on the detail map)
    navigation_kml_path: Optional[str] = None

    # RINEX
    first_rinex: Optional[str] = None  # "YYYY-MM-DD HH:MM:SS"
    last_rinex:  Optional[str]  = None
    battery:     bool = False

    # Comments
    comments:    Optional[str] = None

    # Related data
    instrument_history: list[InstrumentSession] = field(default_factory=list)
    instrument_history_total: int = 0   # total sessions in DB (may exceed len(instrument_history))
    visits:             list[Visit]             = field(default_factory=list)
    contacts:           list[Contact]           = field(default_factory=list)

    # Optional image paths (local files or URLs)
    logo_path:        Optional[str] = None   # GeoDE logo PNG
    map_general_path: Optional[str] = None
    map_detail_path:  Optional[str] = None
    satellite_path:   Optional[str] = None
    monument_path:    Optional[str] = None   # photo of the monument (first station image)
    station_images:   list = field(default_factory=list)  # [(path, caption), …] all station images

    # Pre-rendered base64 plots (raw base64, no data: prefix)
    etm_base64:   Optional[str] = None   # ETM time-series plot
    rinex_base64: Optional[str] = None   # RINEX data availability plot

    # Geodynamic events (List[Earthquake] from ScoreTable)
    geodynamic_events: list = field(default_factory=list)


# ── Image utilities ────────────────────────────────────────────────────────────

def _encode_image(path: Optional[str],
                  max_size: tuple[int, int] = (900, 540),
                  quality: int = 85) -> Optional[str]:
    """Load an image from disk, resize, and return a data: URI string."""
    if not path or not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        # Crop to target aspect ratio symmetrically
        tw, th = max_size
        iw, ih = img.size
        target_ratio = tw / th
        current_ratio = iw / ih
        if current_ratio > target_ratio:
            new_w = int(ih * target_ratio)
            left = (iw - new_w) // 2
            img = img.crop((left, 0, left + new_w, ih))
        elif current_ratio < target_ratio:
            new_h = int(iw / target_ratio)
            top = (ih - new_h) // 2
            img = img.crop((0, top, iw, top + new_h))
        img.thumbnail(max_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"[report] Warning: could not load image {path}: {e}", file=sys.stderr)
        return None


def _encode_logo(path: Optional[str]) -> Optional[str]:
    """Encode logo PNG as-is (preserve transparency)."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        print(f"[report] Warning: could not load logo {path}: {e}", file=sys.stderr)
        return None


import logging as _logging
_logging.getLogger('country_converter').setLevel(_logging.WARNING)
import country_converter as _coco

_FLAG_DIR = Path(__file__).parent / 'flags'
_flag_cache: dict = {}

def _flag(country: str) -> str:
    """Return an <img> tag with the SVG flag for an ISO 3166-1 alpha-3 country code.
    SVG files are expected in geode/reports/flags/{iso2_lower}.svg
    (e.g. 'ar.svg' for Argentina).
    """
    if not country:
        return ''
    key = country.upper()
    if key in _flag_cache:
        return _flag_cache[key]
    try:
        alpha2 = _coco.convert(key, to='ISO2', not_found=None)
        if not alpha2 or alpha2 == 'not found':
            _flag_cache[key] = ''
            return ''
        svg_path = _FLAG_DIR / f'{alpha2.lower()}.svg'
        if not svg_path.exists():
            _flag_cache[key] = ''
            return ''
        b64 = base64.b64encode(svg_path.read_bytes()).decode()
        html = (f'<img src="data:image/svg+xml;base64,{b64}" alt="{alpha2}"'
                f' style="width:16px;height:12px;vertical-align:middle;'
                f'margin-right:4px;border-radius:2px;object-fit:cover;">')
    except Exception:
        html = ''
    _flag_cache[key] = html
    return html


# ── Coordinate formatting ──────────────────────────────────────────────────────

def _fmt_lat(deg: float) -> str:
    hemi = "S" if deg < 0 else "N"
    d = abs(deg)
    dd = int(d)
    mm = int((d - dd) * 60)
    ss = (d - dd - mm / 60) * 3600
    return f"{dd}&deg;{mm:02d}&prime;{ss:06.4f}&Prime;&thinsp;{hemi}"


def _fmt_lon(deg: float) -> str:
    hemi = "W" if deg < 0 else "E"
    d = abs(deg)
    dd = int(d)
    mm = int((d - dd) * 60)
    ss = (d - dd - mm / 60) * 3600
    return f"{dd}&deg;{mm:02d}&prime;{ss:06.4f}&Prime;&thinsp;{hemi}"


def _fmt_ecef(v: float) -> str:
    sign = "&minus;" if v < 0 else "+"
    return f"{sign}{abs(v):,.3f} m"


# ── Data-span helper ───────────────────────────────────────────────────────────

def _data_span(first: Optional[str], last: Optional[str]) -> str:
    if not first or not last:
        return "N/A"
    try:
        fmt = "%Y-%m-%d"
        d1 = datetime.datetime.strptime(first[:10], fmt)
        d2 = datetime.datetime.strptime(last[:10], fmt)
        years = (d2 - d1).days / 365.25
        return f"~{years:.1f} years"
    except Exception:
        return "N/A"


# ── CSS ────────────────────────────────────────────────────────────────────────

_CSS = """
*, *::before, *::after {
  box-sizing: border-box; margin: 0; padding: 0;
  -webkit-print-color-adjust: exact !important;
  print-color-adjust: exact !important;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 13px; line-height: 1.55; color: #1a1a18; background: #f3f3f0;
}
a { color: #185fa5; text-decoration: none; }
.page { max-width: 860px; margin: 0 auto; background: #fff; }

/* Header */
.top-bar {
  background: #1a1a18 !important; color: #fff;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px 0 24px; min-height: 52px; gap: 16px;
}
.station-id { font-size: 17px; font-weight: 700; letter-spacing:.01em; line-height:1.2; color:#fff !important; }
.report-sub { font-size: 10.5px; color: #9b9a96 !important; margin-top: 1px; }
.top-bar-right { display:flex; align-items:center; gap:16px; flex-shrink:0; }
.meta-text { font-size:10.5px; color:#9b9a96 !important; text-align:right; line-height:1.6; white-space:nowrap; }
.geode-logo { height:36px; width:auto; display:block; }

.content { padding: 20px 26px 36px; }

/* Chips */
.chips { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:5px; align-items:center; }
.chip {
  display:inline-block;
  font-size:11px; font-weight:600; padding:3px 9px; border-radius:999px; line-height:1.6;
}
.chip-blue  { background:#e6f1fb !important; color:#0c447c !important; }
.chip-green { background:#eaf3de !important; color:#3b6d11 !important; }
.chip-amber { background:#faeeda !important; color:#854f0b !important; }
.chip-gray  { background:#f1efe8 !important; color:#5f5e5a !important; }
.location-line { font-size:12px; color:#6b6a66; margin-bottom:14px; }

/* Section header */
.section-header {
  display:flex; align-items:center; gap:7px;
  margin:18px 0 8px; padding-bottom:5px; border-bottom:0.5px solid #e0ded8;
}
.section-header .sq { width:8px; height:8px; background:#185fa5 !important; border-radius:2px; flex-shrink:0; }
.section-header span { font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:#6b6a66; }

/* 2x2 image grid */
.img-grid-2x2 {
  display:grid; grid-template-columns:1fr 1fr; gap:0;
  border:0.5px solid #e0ded8; overflow:hidden; border-radius:8px;
}
.img-grid-2x2 .cell { position:relative; overflow:hidden; aspect-ratio:5/3; }
.img-grid-2x2 .cell img { width:100%; height:100%; object-fit:cover; display:block; }
.img-grid-2x2 .cell .cap {
  position:absolute; bottom:0; left:0; right:0;
  background:rgba(0,0,0,0.55) !important; color:#fff !important;
  font-size:10.5px; font-weight:500; padding:5px 10px; line-height:1.3;
}
.img-grid-2x2 .cell:nth-child(1) { border-right:1px solid #888; border-bottom:1px solid #888; }
.img-grid-2x2 .cell:nth-child(2) { border-bottom:1px solid #888; }
.img-grid-2x2 .cell:nth-child(3) { border-right:1px solid #888; }

/* Metric cards */
.metric-row { display:grid; border:0.5px solid #e0ded8; border-radius:6px; overflow:hidden; margin-bottom:4px; }
.metric-row.cols-3 { grid-template-columns:1fr 1fr 1fr; }
.metric-card { background:#f5f5f3 !important; padding:9px 12px; }
.metric-card + .metric-card { border-left:0.5px solid #e0ded8; }
.metric-card .lbl { font-size:10.5px; color:#6b6a66; margin-bottom:3px; }
.metric-card .val { font-size:13px; font-weight:600; color:#1a1a18; }
.metric-card .sub { font-size:10px; color:#9b9a96; font-family:"SF Mono","Fira Mono","Courier New",monospace; margin-top:2px; }

/* Paired cards (metadata / instrument) */
.card-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.info-card { border:0.5px solid #e0ded8; border-radius:8px; overflow:hidden; }
.info-card .card-hdr {
  background:#e6f1fb !important; padding:7px 12px;
  display:flex; align-items:center; gap:7px;
  font-size:12px; font-weight:600; color:#0c447c !important;
}
/* icon span removed — using text labels */
.info-card .kv-row {
  display:flex; justify-content:space-between; align-items:baseline;
  padding:6px 12px; border-bottom:0.5px solid #f0ede8; gap:8px;
}
.info-card .kv-row:last-child { border-bottom:none; }
.info-card .kv-key { font-size:11px; color:#6b6a66; white-space:nowrap; }
.info-card .kv-val { font-size:12px; font-weight:600; text-align:right; }
.kv-val-muted { color:#9b9a96 !important; font-weight:400 !important; font-style:italic; }

/* Comment box */
.comment-box {
  background:#fff8ed !important; border:0.5px solid #d4a855;
  border-left:3px solid #d4a855; border-radius:0 8px 8px 0;
  padding:10px 14px; margin-top:8px; break-inside:avoid;
}
.comment-box .comment-title { font-size:11px; font-weight:700; color:#854f0b !important; margin-bottom:5px; }
.comment-box p { font-size:12px; color:#5a4a2f; line-height:1.65; }

/* Metadata + comment no-break wrapper */
.meta-block { break-inside:avoid; }

/* Instrument history session cards */
.instr-session-card { border-radius:8px; overflow:hidden; margin-bottom:7px; }
.instr-session-hdr { padding:7px 14px; display:flex; align-items:center; justify-content:space-between; }
.instr-period { font-size:12px; font-weight:600; color:#1a1a18; font-family:"SF Mono","Fira Mono","Courier New",monospace; }
.instr-session-body { display:grid; grid-template-columns:1fr 1fr; background:#fff; }
.instr-col { padding:8px 14px; }
.instr-col-title { font-size:11px; font-weight:700; color:#185fa5 !important; margin-bottom:7px; }
.instr-kv { display:flex; justify-content:space-between; align-items:baseline; padding:4px 0; border-bottom:0.5px solid #f0ede8; gap:8px; }
.instr-kv:last-child { border-bottom:none; }
.instr-k { font-size:11px; color:#6b6a66; white-space:nowrap; }
.instr-v { font-size:11.5px; font-weight:600; text-align:right; }

/* Badges */
.badge { display:inline-block; font-size:10px; font-weight:700; padding:2px 7px; border-radius:999px; margin-bottom:4px; }
.badge-teal   { background:#e1f5ee !important; color:#0f6e56 !important; }
.badge-green  { background:#eaf3de !important; color:#3b6d11 !important; }
.badge-blue   { background:#e6f1fb !important; color:#0c447c !important; }
.badge-purple { background:#eeedfe !important; color:#3c3489 !important; }

/* Contact cards */
.contact-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.contact-card {
  border:0.5px solid #e0ded8; border-radius:8px;
  background:#fff !important; padding:10px 12px;
  display:flex; gap:11px; align-items:flex-start;
}
.av { width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex-shrink:0; }
.av-teal   { background:#e1f5ee !important; color:#0f6e56 !important; }
.av-green  { background:#eaf3de !important; color:#3b6d11 !important; }
.av-blue   { background:#e6f1fb !important; color:#0c447c !important; }
.av-purple { background:#eeedfe !important; color:#3c3489 !important; }
.cc-body { flex:1; min-width:0; }
.cc-name { font-size:13px; font-weight:600; margin-bottom:3px; }
.cc-phone { font-size:11.5px; font-family:"SF Mono","Fira Mono","Courier New",monospace; color:#6b6a66; margin-top:3px; }
.cc-email { font-size:11.5px; color:#185fa5 !important; margin-top:1px; }
.cc-note  { font-size:10.5px; color:#9b9a96; margin-top:4px; line-height:1.4; }

/* Full-width images */
.full-img-wrap { border:0.5px solid #e0ded8; border-radius:6px; overflow:hidden; }
.full-img-wrap img { width:100%; display:block; }
/* Portrait RINEX plot: scale to fit the page, centred */
.rinex-portrait-wrap {
  border:0.5px solid #e0ded8; border-radius:6px; overflow:hidden;
  text-align:center;
}
.rinex-portrait-wrap img {
  display:block; margin:0 auto;
  max-width:100%; max-height:80vh; width:auto; height:auto;
}
@media print {
  .rinex-portrait-wrap img { max-height:240mm; }
}
.img-caption { font-size:10.5px; color:#9b9a96; text-align:center; margin-top:4px; line-height:1.4; }
.sub-note { font-size:11.5px; color:#6b6a66; margin-bottom:6px; line-height:1.5; }

/* Visit cards */
.visit-card { border:0.5px solid #e0ded8; border-radius:8px; overflow:hidden; margin-bottom:0; break-inside:avoid; }
.visit-hdr { background:#e6f1fb !important; display:table; width:100%; padding:0; }
.visit-hdr-date { display:table-cell; font-size:13px; font-weight:700; color:#0c447c !important; padding:8px 14px; vertical-align:middle; }
.visit-hdr-right { display:table-cell; text-align:right; padding:8px 14px; vertical-align:middle; white-space:nowrap; }
/* .vdate merged into .visit-hdr-date */
.vcampaign-none { font-size:11px; color:#9b9a96; font-style:italic; }
.visit-body { display:grid; grid-template-columns:1fr 1fr 1fr; }
.visit-col { padding:9px 12px; }
.visit-col + .visit-col { border-left:0.5px solid #e0ded8; }
.col-title { font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#9b9a96; margin-bottom:6px; }
.person-row { display:flex; align-items:center; gap:7px; margin-bottom:4px; }
.person-av {
  width:24px; height:24px; border-radius:50%;
  background:#e6f1fb !important; color:#0c447c !important;
  font-size:8px; font-weight:700;
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
}
.person-name { font-size:12px; }
.file-chip {
  display:inline-block; font-size:10.5px;
  font-family:"SF Mono","Fira Mono","Courier New",monospace;
  color:#185fa5 !important; background:#e6f1fb !important;
  padding:2px 6px; border-radius:4px; margin:2px 2px 2px 0;
  line-height:1.5; word-break:break-all;
}
.no-data { font-size:11px; color:#9b9a96; font-style:italic; }

/* Visit photo grid — table layout for weasyprint compat */
.visit-photos { width:100%; border-top:0.5px solid #e0ded8; border-collapse:collapse; display:table; table-layout:fixed; }
.visit-photos-row { display:table-row; }
.visit-photo-cell { display:table-cell; width:33.333%; position:relative; overflow:hidden; border-right:0.5px solid #e0ded8; vertical-align:top; }
.visit-photo-cell:last-child { border-right:none; }
.visit-photo-cell img { width:100%; height:auto; display:block; }
.visit-photo-cell .photo-cap {
  display:block; background:rgba(0,0,0,0.55) !important; color:#fff !important;
  font-size:9.5px; font-weight:500; padding:4px 8px; line-height:1.3;
}

/* Footer */
.footer { border-top:0.5px solid #e0ded8; padding:10px 26px; display:flex; justify-content:space-between; font-size:10.5px; color:#9b9a96; }

@media print {
  @page { margin:12mm 14mm; size:A4; }
  body { background:#fff; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  .page { max-width:100%; box-shadow:none; }
  .info-card, .contact-card, .contact-grid,
  .metric-row, .card-row, .instr-session-card, .meta-block { break-inside:avoid; }
  .section-header { break-after:avoid; }
}

/* Geodynamic event cards */
.geo-card {
  display:flex; border:0.5px solid #e0ded8; border-radius:8px; overflow:hidden;
  margin-bottom:7px; break-inside:avoid;
}
.geo-card-coseismic  { border-left:3px solid #c0392b !important; }
.geo-card-postseismic { border-left:3px solid #e67e22 !important; }
.geo-bb {
  flex:0 0 110px; display:flex; flex-direction:column;
  align-items:center; justify-content:center;
  padding:10px 8px; background:#f5f5f3 !important; border-right:0.5px solid #e0ded8;
}
.geo-bb img { width:80px; height:80px; display:block; }
.geo-bb-placeholder {
  width:80px; height:80px; display:flex; align-items:center; justify-content:center;
  background:#e8ecf0 !important; border-radius:50%;
  font-size:9.5px; color:#9b9a96; text-align:center; line-height:1.4;
}
.geo-body { flex:1; padding:10px 14px; min-width:0; }
.geo-title {
  font-size:13.5px; font-weight:700; color:#1a1a18; margin-bottom:4px; line-height:1.3;
}
.geo-title a { color:#185fa5 !important; text-decoration:none; }
.geo-meta {
  font-size:11px; color:#6b6a66; margin-bottom:7px; line-height:1.6;
  font-family:"SF Mono","Fira Mono","Courier New",monospace;
}
.geo-chips { display:flex; gap:5px; flex-wrap:wrap; }
.geo-chip-cos  { background:#fde8e8 !important; color:#c0392b !important; }
.geo-chip-post { background:#fef3e2 !important; color:#b35a00 !important; }
"""


# ── HTML section builders ──────────────────────────────────────────────────────

def _chip(text: str, cls: str, flag_html: str = "") -> str:
    return f'<span class="chip {cls}">{flag_html}{text}</span>'


def _section(label: str) -> str:
    return f'<div class="section-header"><div class="sq"></div><span>{label}</span></div>'


def _kv(key: str, value: str, muted: bool = False) -> str:
    val_cls = ' kv-val-muted' if muted else ''
    return (f'<div class="kv-row">'
            f'<span class="kv-key">{key}</span>'
            f'<span class="kv-val{val_cls}">{value}</span>'
            f'</div>')


def _info_card(icon: str, title: str, rows: list[tuple[str, str, bool]]) -> str:
    kv_html = "".join(_kv(k, v, m) for k, v, m in rows)
    icon_html = f'<span class="icon">{icon}</span> ' if icon else ""
    return (f'<div class="info-card">'
            f'<div class="card-hdr">{icon_html}{title}</div>'
            f'{kv_html}</div>')


def _role_style(role: str) -> tuple[str, str]:
    """Returns (avatar_class, badge_class) for a contact role."""
    mapping = {
        "Local Coordinator": ("av-teal",   "badge-teal"),
        "Site Owner":        ("av-green",  "badge-green"),
        "Station Manager":   ("av-blue",   "badge-blue"),
        "Network Manager":   ("av-purple", "badge-purple"),
    }
    return mapping.get(role, ("av-blue", "badge-blue"))


def _contact_card(c: Contact) -> str:
    av_cls, badge_cls = _role_style(c.role)
    phone_html = f'<div class="cc-phone">{c.phone}</div>' if c.phone else ""
    email_html = f'<div class="cc-email">{c.email}</div>' if c.email else ""
    note_html  = f'<div class="cc-note">{c.note}</div>'  if c.note  else ""
    return (f'<div class="contact-card">'
            f'<div class="av {av_cls}">{c.initials}</div>'
            f'<div class="cc-body">'
            f'<div class="cc-name">{c.name}</div>'
            f'<span class="badge {badge_cls}">{c.role}</span>'
            f'{phone_html}{email_html}{note_html}'
            f'</div></div>')


def _person_av(name: str) -> str:
    parts = name.split()
    initials = (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()
    return (f'<div class="person-row">'
            f'<div class="person-av">{initials}</div>'
            f'<span class="person-name">{name}</span>'
            f'</div>')


def _visit_date_range(v: Visit) -> str:
    if v.date_end and v.date_end != v.date:
        # Format: "Jan 09–11, 2016" or "Jun 30 – Jul 02, 2015"
        try:
            d1 = datetime.datetime.strptime(v.date, "%Y-%m-%d")
            d2 = datetime.datetime.strptime(v.date_end, "%Y-%m-%d")
            if d1.month == d2.month:
                return f"{d1.strftime('%b %d')} - {d2.strftime('%d, %Y')}"
            else:
                return f"{d1.strftime('%b %d')} - {d2.strftime('%b %d, %Y')}"
        except Exception:
            return f"{v.date} – {v.date_end}"
    try:
        return datetime.datetime.strptime(v.date, "%Y-%m-%d").strftime("%b %d, %Y")
    except Exception:
        return v.date


def _instr_session_card(s: InstrumentSession, is_current: bool = False) -> str:
    hdr_bg = "background:#dbeeff !important;" if is_current else "background:#f5f5f3 !important;"
    border  = "border:0.5px solid #b5d4f4;"    if is_current else "border:0.5px solid #e0ded8;"
    badge   = (' <span class="badge badge-green" '
               'style="font-size:9px;margin-left:6px;vertical-align:middle">current</span>'
               if is_current else "")
    period  = f"{s.date_start} &ndash; {s.date_end}"
    return (
        f'<div class="instr-session-card" style="{border}">'
        f'<div class="instr-session-hdr" style="{hdr_bg}">'
        f'<span class="instr-period">{period}{badge}</span></div>'
        f'<div class="instr-session-body">'
        f'<div class="instr-col">'
        f'<div class="instr-col-title">Receiver</div>'
        f'<div class="instr-kv"><span class="instr-k">Type</span><span class="instr-v">{s.receiver}</span></div>'
        f'<div class="instr-kv"><span class="instr-k">Serial no.</span><span class="instr-v">{s.rcvr_sn}</span></div>'
        f'<div class="instr-kv"><span class="instr-k">Firmware</span><span class="instr-v">{s.firmware}</span></div>'
        f'<div class="instr-kv"><span class="instr-k">Version</span><span class="instr-v">{s.version}</span></div>'
        f'</div>'
        f'<div class="instr-col" style="border-left:0.5px solid #e0ded8;">'
        f'<div class="instr-col-title">Antenna</div>'
        f'<div class="instr-kv"><span class="instr-k">Type</span><span class="instr-v">{s.antenna}</span></div>'
        f'<div class="instr-kv"><span class="instr-k">Serial no.</span><span class="instr-v">{s.ant_sn}</span></div>'
        f'<div class="instr-kv"><span class="instr-k">Height</span><span class="instr-v">{s.ant_h:.4f} m</span></div>'
        f'<div class="instr-kv"><span class="instr-k">Radome</span><span class="instr-v">{s.radome}</span></div>'
        f'</div></div></div>'
    )


def _visit_card(v: Visit) -> str:
    # Header
    date_str = _visit_date_range(v)
    if v.campaign:
        right_html = f'<span class="chip chip-blue" style="font-size:10px">Campaign: {v.campaign}</span>'
    else:
        right_html = '<div class="vcampaign-none">No campaign assigned</div>'

    # Personnel column
    if v.personnel:
        pers_html = "".join(_person_av(p) for p in v.personnel)
    else:
        pers_html = '<div class="no-data">None on record</div>'

    # Observation files column (DB query already capped at 4; use stored total for overflow)
    if v.obs_files:
        obs_html = "".join(f'<span class="file-chip">{f}</span>' for f in v.obs_files)
        extra = v.obs_files_total - len(v.obs_files)
        if extra > 0:
            obs_html += f'<div class="no-data" style="margin-top:4px">…and {extra} more</div>'
    else:
        obs_html = '<div class="no-data">None found</div>'

    # Attached files column (DB query already capped at 4)
    if v.att_files:
        att_html = "".join(f'<span class="file-chip">{f}</span>' for f in v.att_files)
        extra = v.att_files_total - len(v.att_files)
        if extra > 0:
            att_html += f'<div class="no-data" style="margin-top:4px">…and {extra} more</div>'
    else:
        att_html = '<div class="no-data">None found</div>'

    # Photos — capped at 12 (4 rows × 3) to keep card height predictable for PDF pagination
    _PHOTO_LIMIT = 12
    photo_html = ""
    valid_photos = list(zip(v.photo_paths, v.photo_captions)) if v.photo_paths else []
    if valid_photos:
        total_photos   = len(valid_photos)
        display_photos = valid_photos[:_PHOTO_LIMIT]
        hidden         = total_photos - len(display_photos)
        # Build rows of 3 cells for table-based layout (weasyprint compatible)
        rows_html = ""
        for i in range(0, len(display_photos), 3):
            chunk = display_photos[i:i+3]
            cells_html = ""
            for path, cap in chunk:
                uri = _encode_image(path, max_size=(400, 300), quality=82)
                if uri:
                    cells_html += (f'<td class="visit-photo-cell">'
                                   f'<img src="{uri}" alt="">'
                                   f'<div class="photo-cap">{cap}</div>'
                                   f'</td>')
            # Pad to 3 columns
            while cells_html.count('<td') < 3:
                cells_html += '<td class="visit-photo-cell" style="background:#f5f5f3"></td>'
            rows_html += f'<tr class="visit-photos-row">{cells_html}</tr>'
        if hidden > 0:
            rows_html += (
                f'<tr><td colspan="3" style="text-align:center;padding:7px 12px;'
                f'font-size:11px;color:#9b9a96;font-style:italic;background:#f5f5f3;'
                f'border-top:0.5px solid #e0ded8;">'
                f'+{hidden} more image{"s" if hidden != 1 else ""} not shown</td></tr>'
            )
        if rows_html:
            photo_html = f'<table class="visit-photos">{rows_html}</table>'

    # When comments follow, flatten the card's bottom corners and carry the
    # bottom margin on the comment box instead; otherwise keep the full radius.
    if v.comments:
        card_style = 'margin-bottom:0;border-radius:8px 8px 0 0;'
    else:
        card_style = 'margin-bottom:8px;'
    card_html = (
        f'<div class="visit-card" style="{card_style}">'
        f'<div class="visit-hdr">'
        f'<div class="visit-hdr-date">{date_str}</div>'
        f'<div class="visit-hdr-right">{right_html}</div>'
        f'</div>'
        f'<div class="visit-body">'
        f'<div class="visit-col"><div class="col-title">Personnel</div>{pers_html}</div>'
        f'<div class="visit-col"><div class="col-title">Observation files</div>{obs_html}</div>'
        f'<div class="visit-col"><div class="col-title">Attached files</div>{att_html}</div>'
        f'</div>{photo_html}</div>'
    )

    # Comments are returned separately so they sit outside the card div.
    # This keeps the card height predictable for PDF page-break control while
    # still appearing visually attached (no bottom-radius, no gap).
    comment_html = ""
    if v.comments:
        comment_html = (
            f'<div class="comment-box" style="margin-top:0;margin-bottom:8px;'
            f'border-radius:0 0 8px 8px;border-top:none;">'
            f'<div class="comment-title">Comments</div>'
            f'<p>{v.comments}</p></div>'
        )

    return card_html, comment_html


def _beachball_b64(strike: float, dip: float, rake: float,
                   facecolor: str = 'k') -> Optional[str]:
    """Render a focal-mechanism beachball to a base64 PNG string, or None on failure.

    We intentionally avoid obspy's ``outfile`` path because it calls
    ``fig.savefig(..., transparent=True)`` via PIL, which raises
    ``SystemError: tile cannot extend outside image`` on some PIL/matplotlib
    version combinations.  Instead we supply our own figure, let obspy draw
    into it, then save with FigureCanvasAgg directly.
    """
    import warnings
    try:
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from obspy.imaging.beachball import beachball
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            fig = plt.figure(figsize=(1.8, 1.8), facecolor='none')
            beachball([strike, dip, rake], width=180, linewidth=1,
                      facecolor=facecolor, fig=fig)
            canvas = FigureCanvasAgg(fig)
            buf = io.BytesIO()
            canvas.print_png(buf)
            plt.close(fig)
        buf.seek(0)
        data = buf.read()
        if not data:
            return None
        return base64.b64encode(data).decode()
    except Exception:
        return None


def _geo_event_card(eq) -> str:
    """Render one Earthquake (from ScoreTable) as an HTML card."""
    try:
        from ..etm.core.type_declarations import JumpType as _JT
    except Exception:
        _JT = None

    is_cos = (_JT is not None and eq.jump_type == _JT.COSEISMIC_JUMP_DECAY)
    card_cls  = 'geo-card-coseismic'  if is_cos else 'geo-card-postseismic'
    chip_cls  = 'chip geo-chip-cos'   if is_cos else 'chip geo-chip-post'
    type_lbl  = 'Coseismic + Postseismic' if is_cos else 'Postseismic Only'
    bb_color  = '#c0392b' if is_cos else '#e67e22'

    # Focal mechanism beachball
    if eq.strike:
        bb_b64 = _beachball_b64(eq.strike[0], eq.dip[0], eq.rake[0],
                                 facecolor=bb_color)
        if bb_b64:
            bb_html = f'<img src="data:image/png;base64,{bb_b64}" alt="Focal mechanism">'
        else:
            bb_html = '<div class="geo-bb-placeholder"><span>Render<br>error</span></div>'
    else:
        bb_html = '<div class="geo-bb-placeholder"><span>No focal<br>mechanism</span></div>'

    # USGS link
    usgs_url  = f'https://earthquake.usgs.gov/earthquakes/eventpage/{eq.id}'
    magnitude = f'M&thinsp;{eq.magnitude:.1f}'
    title_html = (f'<a href="{usgs_url}" target="_blank">'
                  f'{magnitude} &ndash; {eq.location}</a>')

    # Date + epicenter + depth  (pyDate.Date stores hour/minute/second)
    try:
        dt_str = (f'{eq.date.year}-{eq.date.month:02d}-{eq.date.day:02d}'
                  f' {eq.date.hour:02d}:{eq.date.minute:02d}:{eq.date.second:02d} (UTC)')
    except Exception:
        dt_str = str(eq.date)
    lat_str = _fmt_lat(eq.lat)
    lon_str = _fmt_lon(eq.lon)
    meta_html = (f'{dt_str}<br>'
                 f'{lat_str}&nbsp;&nbsp;{lon_str}&nbsp;&nbsp;'
                 f'{eq.depth:.1f}&thinsp;km depth')

    dist_chip = f'<span class="chip chip-gray">{eq.distance:.0f}&thinsp;km from station</span>'

    return (
        f'<div class="geo-card {card_cls}">'
        f'<div class="geo-bb">{bb_html}</div>'
        f'<div class="geo-body">'
        f'<div class="geo-title">{title_html}</div>'
        f'<div class="geo-meta">{meta_html}</div>'
        f'<div class="geo-chips">'
        f'<span class="{chip_cls}">{type_lbl}</span>{dist_chip}'
        f'</div></div></div>'
    )


# ── Main HTML builder ──────────────────────────────────────────────────────────

def build_report(s: StationReport,
                 etm_image_path: Optional[str] = None,
                 etm_caption: str = "",
                 etm_subtitle: str = "",
                 rinex_image_path: Optional[str] = None,
                 rinex_caption: str = "",
                 rinex_subtitle: str = "",
                 show_instruments: bool = True,
                 show_timeseries: bool = True,
                 show_rinex: bool = True,
                 show_contacts: bool = True,
                 show_visits: bool = True,
                 show_geodynamics: bool = True) -> str:
    """
    Build the full station report HTML string.

    Parameters
    ----------
    s : StationReport
        All station data.
    etm_image_path : str, optional
        Path to the ETM time-series plot image.
    etm_caption / etm_subtitle : str
        Caption and subtitle text for the ETM section.
    rinex_image_path : str, optional
        Path to the RINEX availability plot image.
    rinex_caption / rinex_subtitle : str
        Caption and subtitle text for the RINEX section.
    """
    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Encode images
    logo_uri      = _encode_logo(s.logo_path)
    map_gen_uri   = _encode_image(s.map_general_path, (900, 540))
    map_det_uri   = _encode_image(s.map_detail_path,  (900, 540))
    sat_uri       = _encode_image(s.satellite_path,   (900, 540))
    mon_uri       = _encode_image(s.monument_path,    (900, 540))

    # ETM and RINEX: prefer pre-rendered base64 from StationReport; fall back to file
    if s.etm_base64:
        etm_uri = f"data:image/png;base64,{s.etm_base64}"
    else:
        etm_uri = _encode_image(etm_image_path, (1600, 800), quality=90)

    if s.rinex_base64:
        rinex_uri = f"data:image/png;base64,{s.rinex_base64}"
    else:
        rinex_uri = _encode_image(rinex_image_path, (2500, 800), quality=90)

    flag_html  = _flag(s.country)
    comms_chip = "chip-blue" if s.comms else "chip-gray"

    # Map api_stationstatuscolor icon-class names → hex colours for the report
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

    # Status chip: use DB colour when available, fall back to green/amber
    _hex = _STATUS_COLOR_MAP.get(s.status_color or "") if s.status_color else None
    if _hex:
        h = _hex.lstrip('#')
        _r, _g, _b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        _bg = f'rgba({_r},{_g},{_b},0.15)'
        status_chip_html = (
            f'<span class="chip" style="background:{_bg} !important;'
            f'color:{_hex} !important;">&#10003; {s.status}</span>'
        )
    else:
        _fallback = "chip-green" if (s.status or "").lower() == "active" else "chip-amber"
        status_chip_html = _chip(f"&#10003; {s.status}", _fallback)

    # ── Header ─────────────────────────────────────────────────────────────────
    logo_html = (f'<img class="geode-logo" src="{logo_uri}" alt="GeoDE">'
                 if logo_uri else "")

    header = f"""
<div class="top-bar">
  <div>
    <div class="station-id">{s.network} &middot; {s.station}</div>
    <div class="report-sub">{s.location_desc}</div>
  </div>
  <div class="top-bar-right">
    <div class="meta-text">GeoDE Station Report<br>Printed {today}</div>
    {logo_html}
  </div>
</div>"""

    # ── Chips ──────────────────────────────────────────────────────────────────
    chips = f"""
<div class="chips" style="margin-top:6px">
  {_chip(s.country, "chip-blue", flag_html)}
  {_chip(f"Network: {s.network}", "chip-blue")}
  {_chip(f"Station: {s.station}", "chip-blue")}
  {status_chip_html}
  {_chip(s.station_type, "chip-blue")}
</div>
<div class="location-line">{s.location_desc}</div>"""

    # ── Location 2×2 grid ──────────────────────────────────────────────────────
    def _cell(uri, cap, borders=""):
        if not uri:
            return ""
        return (f'<div class="cell" style="{borders}">'
                f'<img src="{uri}" alt="{cap}">'
                f'<div class="cap">{cap}</div></div>')

    location_section = ""
    # Monument cell: show placeholder when no image is available
    if mon_uri:
        mon_cell = (
            f'<div class="cell">'
            f'<img src="{mon_uri}" alt="Monument">'
            f'<div class="cap">Monument &middot; {s.monument}</div></div>'
        )
    else:
        mon_cell = (
            f'<div class="cell">'
            f'<div style="width:100%;height:100%;display:flex;flex-direction:column;'
            f'align-items:center;justify-content:center;background:#f5f5f3;">'
            f'<span style="font-size:36px;color:#d0cec8;">&#9635;</span>'
            f'<span style="font-size:11px;font-weight:600;color:#6b6a66;margin-top:8px;">'
            f'{s.monument}</span>'
            f'<span style="font-size:10px;color:#9b9a96;margin-top:3px;">No image available</span>'
            f'</div>'
            f'<div class="cap">Monument &middot; {s.monument}</div></div>'
        )
    cells_html = (
        _cell(map_gen_uri, "General location",
              "border-right:1px solid #888;border-bottom:1px solid #888;")
        + _cell(map_det_uri, "Site detail",
                "border-bottom:1px solid #888;")
        + _cell(sat_uri,    "Satellite view &middot; station marker visible",
                "border-right:1px solid #888;")
        + mon_cell
    )
    if map_gen_uri or map_det_uri or sat_uri or mon_uri:
        location_section = (
            _section("Location and Monument")
            + f'<div class="img-grid-2x2">{cells_html}</div>'
        )

    # ── Coordinates ────────────────────────────────────────────────────────────
    lat_dd   = f"{s.lat:.8f}&deg;"
    lon_dd   = f"{s.lon:.8f}&deg;"

    coords_section = f"""
{_section("Geodetic Coordinates")}
<div class="metric-row cols-3" style="margin-bottom:4px">
  <div class="metric-card">
    <div class="lbl">Latitude</div>
    <div class="val">{_fmt_lat(s.lat)}</div>
    <div class="sub">{lat_dd}</div>
  </div>
  <div class="metric-card">
    <div class="lbl">Longitude</div>
    <div class="val">{_fmt_lon(s.lon)}</div>
    <div class="sub">{lon_dd}</div>
  </div>
  <div class="metric-card">
    <div class="lbl">Ellipsoidal height</div>
    <div class="val">{s.height:.3f} m</div>
  </div>
</div>
<div class="metric-row cols-3">
  <div class="metric-card"><div class="lbl">X (ECEF)</div><div class="val">{_fmt_ecef(s.x_ecef)}</div></div>
  <div class="metric-card"><div class="lbl">Y (ECEF)</div><div class="val">{_fmt_ecef(s.y_ecef)}</div></div>
  <div class="metric-card"><div class="lbl">Z (ECEF)</div><div class="val">{_fmt_ecef(s.z_ecef)}</div></div>
</div>"""

    # ── Station Metadata ───────────────────────────────────────────────────────
    span  = _data_span(s.first_rinex, s.last_rinex)
    gen_card = _info_card("", "General", [
        ("Station type",   s.station_type,              False),
        ("Monument",       s.monument,                  False),
        ("Communications", "Yes" if s.comms else "No",  False),
        ("Battery",        "Yes" if s.battery else "No",False),
    ])
    span_card = _info_card("", "Data span", [
        ("First RINEX", s.first_rinex or "N/A",   False),
        ("Last RINEX",  s.last_rinex  or "N/A",   False),
        ("Data span",   span,                      False),
        ("Contacts",    f"{len(s.contacts)} people" if s.contacts else "None on record",
                        not s.contacts),
    ])
    comment_html = ""
    if s.comments:
        comment_html = (f'<div class="comment-box">'
                        f'<div class="comment-title">General comments</div>'
                        f'<p>{s.comments}</p></div>')

    metadata_section = (
        _section("Station Metadata")
        + f'<div class="meta-block">'
        + f'<div class="card-row">{gen_card}{span_card}</div>'
        + comment_html
        + f'</div>'
    )

    # ── Instrument history ─────────────────────────────────────────────────────
    instr_section = ""
    if show_instruments and s.instrument_history:
        cards = ""
        for sess in s.instrument_history:
            is_current = (sess.date_end == 'present')
            cards += _instr_session_card(sess, is_current)
        total = s.instrument_history_total
        shown = len(s.instrument_history)
        if total > shown:
            note = (f'<div style="font-size:11px;color:#9b9a96;font-style:italic;'
                    f'margin-top:4px;">Showing {shown} most recent of {total} total sessions</div>')
        else:
            note = ""
        instr_section = _section("Instrument History") + cards + note

    # ── Contacts ───────────────────────────────────────────────────────────────
    contacts_section = ""
    if show_contacts:
        if s.contacts:
            cards_html = "".join(_contact_card(c) for c in s.contacts)
            contacts_section = (
                _section("Contact Information")
                + f'<div class="contact-grid">{cards_html}</div>'
            )
        else:
            contacts_section = (
                _section("Contact Information")
                + f'<div class="comment-box" style="background:#f5f5f3 !important;'
                f'border-color:#d0ced8;border-left-color:#9b9a96;">'
                f'<p style="color:#9b9a96;font-style:italic;">No contact information on record for this station.</p>'
                f'</div>'
            )

    # ── ETM time series ────────────────────────────────────────────────────────
    etm_section = ""
    if show_timeseries and etm_uri:
        etm_section = (
            _section("Position Time Series &middot; ETM")
            + (f'<div class="sub-note">{etm_subtitle}</div>' if etm_subtitle else "")
            + f'<div class="full-img-wrap"><img src="{etm_uri}" alt="ETM time series"></div>'
            + (f'<div class="img-caption">{etm_caption}</div>' if etm_caption else "")
        )

    # ── RINEX availability ─────────────────────────────────────────────────────
    rinex_section = ""
    if show_rinex and rinex_uri:
        rinex_section = (
            _section("RINEX Data Availability")
            + (f'<div class="sub-note">{rinex_subtitle}</div>' if rinex_subtitle else "")
            + f'<div class="rinex-portrait-wrap"><img src="{rinex_uri}" alt="RINEX availability"></div>'
            + (f'<div class="img-caption">{rinex_caption}</div>' if rinex_caption else "")
        )

    # ── Station photos ─────────────────────────────────────────────────────────
    station_photos_section = ""
    if s.station_images:
        rows_html = ""
        for i in range(0, len(s.station_images), 3):
            chunk = s.station_images[i:i+3]
            cells_html = ""
            for img_path, img_cap in chunk:
                uri = _encode_image(img_path, max_size=(400, 300), quality=82)
                if uri:
                    cells_html += (f'<td class="visit-photo-cell">'
                                   f'<img src="{uri}" alt="">'
                                   f'<div class="photo-cap">{img_cap}</div>'
                                   f'</td>')
            while cells_html.count('<td') < 3:
                cells_html += '<td class="visit-photo-cell" style="background:#f5f5f3"></td>'
            rows_html += f'<tr class="visit-photos-row">{cells_html}</tr>'
        if rows_html:
            station_photos_section = (
                _section(f"Station Photos &nbsp;&middot;&nbsp; {len(s.station_images)} image(s)")
                + f'<table class="visit-photos">{rows_html}</table>'
            )

    # ── Visit history ──────────────────────────────────────────────────────────
    visits_section = ""
    if show_visits:
        if s.visits:
            n_visits = len(s.visits)
            year_range = f"{s.visits[-1].date[:4]}&ndash;{s.visits[0].date[:4]}"
            visit_cards = "".join(card + comment for card, comment in
                                   (_visit_card(v) for v in s.visits))
            visits_section = (
                _section(f"Visit History &nbsp;&middot;&nbsp; {n_visits} visits &nbsp;&middot;&nbsp; {year_range}")
                + visit_cards
            )
        else:
            visits_section = (
                _section("Visit History")
                + f'<div class="comment-box" style="background:#f5f5f3 !important;'
                f'border-color:#d0ced8;border-left-color:#9b9a96;">'
                f'<p style="color:#9b9a96;font-style:italic;">No visits on record for this station.</p>'
                f'</div>'
            )

    # ── Geodynamic events ──────────────────────────────────────────────────────
    geo_section = ""
    if show_geodynamics:
        if s.geodynamic_events:
            n_geo = len(s.geodynamic_events)
            cards_html = "".join(_geo_event_card(eq) for eq in s.geodynamic_events)
            geo_section = (
                _section(f"Geodynamic Events &nbsp;&middot;&nbsp; {n_geo} event(s)")
                + cards_html
            )
        else:
            geo_section = (
                _section("Geodynamic Events")
                + f'<div class="comment-box" style="background:#f5f5f3 !important;'
                f'border-color:#d0ced8;border-left-color:#9b9a96;">'
                f'<p style="color:#9b9a96;font-style:italic;">'
                f'No significant seismic events (M&thinsp;&ge;&thinsp;6.0) found for the station observation period.</p>'
                f'</div>'
            )

    # ── Footer ─────────────────────────────────────────────────────────────────
    footer = (f'<div class="footer">'
              f'<span>GeoDE &middot; Geodetic Database Engine</span>'
              f'<span>{s.network}.{s.station} ({s.country}) &nbsp;&middot;&nbsp; '
              f'Report generated {today}</span></div>')

    # ── Assemble ───────────────────────────────────────────────────────────────
    body_parts = [
        chips,
        location_section,
        station_photos_section,
        coords_section,
        metadata_section,
        instr_section,
        contacts_section,
        etm_section,
        rinex_section,
        visits_section,
        geo_section,
    ]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GeoDE Station Report &middot; {s.network}.{s.station}</title>
<style>
{_CSS}
</style>
</head>
<body>
<div class="page">
{header}
<div class="content">
{''.join(body_parts)}
</div>
{footer}
</div>
</body>
</html>"""


# ── PDF rendering ──────────────────────────────────────────────────────────────

def render_pdf(html: str, output_path: str) -> None:
    """
    Render HTML to PDF using weasyprint.

    Install: pip install weasyprint
    Note: weasyprint requires system libraries (pango, cairo).
    On Ubuntu/Debian: apt install libpango-1.0-0 libpangoft2-1.0-0
    On macOS: brew install pango
    """
    if not WEASYPRINT_AVAILABLE:
        raise ImportError(
            "weasyprint is not installed. Run: pip install weasyprint\n"
            "System deps (Ubuntu): apt install libpango-1.0-0 libpangoft2-1.0-0"
        )
    wp = weasyprint.HTML(string=html)
    wp.write_pdf(output_path)
    print(f"[report] PDF written: {output_path}")


# ── Database query ─────────────────────────────────────────────────────────────

def station_from_db(cnn,
                    network_code: str,
                    station_code: str,
                    media_path: Optional[str] = None,
                    show_instruments:  bool = True,
                    show_timeseries:   bool = True,
                    show_rinex:        bool = True,
                    show_contacts:     bool = True,
                    show_visits:       bool = True,
                    show_geodynamics:  bool = True) -> StationReport:
    """
    Build a StationReport from the GeoDE PostgreSQL database.

    Parameters
    ----------
    cnn          : Cnn   — active geode.dbConnection.Cnn instance
    network_code : str   — e.g. 'ARS'
    station_code : str   — e.g. 'AT47'
    media_path   : str   — root of the Django MEDIA_ROOT folder
                           (from gnss_data.cfg [archive] media).
                           When None, file-backed fields are omitted.
    """
    nc = network_code          # keep as-is (DB stores NetworkCode in lowercase)
    sc = station_code.lower()

    if media_path and not os.path.isdir(media_path):
        print(f' !! Warning: media path does not exist or is not accessible: {media_path}',
              file=sys.stderr)
        print(f' !! Images (monument, station photos, visit photos) will be omitted.',
              file=sys.stderr)
        media_path = None

    def _media(rel: Optional[str]) -> Optional[str]:
        """Resolve a relative Django media path to an absolute path."""
        if not rel or not media_path:
            return None
        full = os.path.join(media_path, rel)
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
        raise ValueError(f"Station {nc.upper()}.{sc} not found in the database")
    s = rows[0]

    api_id        = int(s['api_id'])
    lat           = float(s['lat']    or 0)
    lon           = float(s['lon']    or 0)
    height        = float(s['height'] or 0)
    x_ecef        = float(s['auto_x'] or 0)
    y_ecef        = float(s['auto_y'] or 0)
    z_ecef        = float(s['auto_z'] or 0)
    country       = str(s['country_code'] or '')
    location_desc = str(s['StationName']  or f'{nc}.{sc}')

    # ── 2. Station metadata (status, type, battery, comms, monument, comments) ─
    status        = 'Unknown'
    status_color  = None
    station_type  = 'Unknown'
    has_battery   = False
    has_comms     = False
    monument      = 'N/A'
    monument_path = None
    comments      = None

    navigation_kml_path = None

    meta = cnn.query_float(
        f"""SELECT m.has_battery, m.has_communications, m.comments,
                   m.navigation_file,
                   st.name       AS status_name,
                   stype.name    AS station_type_name,
                   mon.name      AS monument_type_name,
                   mon.photo_path AS monument_image,
                   scolor.color  AS status_color
            FROM api_stationmeta m
            JOIN api_stationstatus       st     ON m.status_id        = st.id
            LEFT JOIN api_stationstatuscolor scolor ON st.color_id = scolor.id
            JOIN api_stationtype         stype  ON m.station_type_id  = stype.id
            LEFT JOIN api_monumenttype   mon    ON m.monument_type_id  = mon.id
            WHERE m.station_id = {api_id}""",
        as_dict=True)
    if meta:
        m = meta[0]
        status        = str(m.get('status_name')        or 'Unknown')
        status_color  = str(m.get('status_color')       or '').strip() or None
        station_type  = str(m.get('station_type_name')  or 'Unknown')
        has_battery   = bool(m.get('has_battery',  False))
        has_comms     = bool(m.get('has_communications', False))
        monument      = str(m.get('monument_type_name') or 'N/A')
        monument_path = _media(m.get('monument_image')) if m.get('monument_image') else None
        raw_comments  = m.get('comments') or ''
        comments      = raw_comments.strip() or None
        navigation_kml_path = _media(m.get('navigation_file')) if m.get('navigation_file') else None

    # ── 3. RINEX date range (from rinex_proc, completion ≥ 0.5) ─────────────
    rinex = cnn.query_float(
        f"""SELECT MIN("ObservationSTime") AS first_rinex,
                   MAX("ObservationETime") AS last_rinex
            FROM rinex_proc
            WHERE "NetworkCode" = '{nc}' AND "StationCode" = '{sc}'
              AND "Completion" >= 0.5""",
        as_dict=True)
    first_rinex = None
    last_rinex  = None
    if rinex and rinex[0].get('first_rinex') is not None:
        first_rinex = str(rinex[0]['first_rinex'])[:19]
        last_rinex  = str(rinex[0]['last_rinex'])[:19]

    # ── 4. Instrument history (stationinfo) ──────────────────────────────────
    instrument_history = []
    instrument_history_total = 0
    if show_instruments:
        instr_rows = cnn.query_float(
            f"""SELECT "DateStart", "DateEnd", "ReceiverCode", "ReceiverSerial",
                       "ReceiverFirmware", "ReceiverVers",
                       "AntennaCode", "AntennaSerial", "AntennaHeight",
                       "HeightCode", "RadomeCode"
                FROM stationinfo
                WHERE "NetworkCode" = '{nc}' AND "StationCode" = '{sc}'
                ORDER BY "DateStart" DESC""",
            as_dict=True)
        instrument_history_total = len(instr_rows)
        for r in instr_rows[:4]:
            ds = str(r['DateStart'])[:10] if r.get('DateStart') else ''
            de = str(r['DateEnd'])[:10]   if r.get('DateEnd')   else 'present'
            if de.startswith('9999'):
                de = 'present'
            instrument_history.append(InstrumentSession(
                date_start  = ds,
                date_end    = de,
                receiver    = str(r.get('ReceiverCode')     or ''),
                rcvr_sn     = str(r.get('ReceiverSerial')   or 'N/A'),
                firmware    = str(r.get('ReceiverFirmware') or 'N/A'),
                version     = str(r.get('ReceiverVers')     or '&mdash;'),
                antenna     = str(r.get('AntennaCode')      or ''),
                ant_sn      = str(r.get('AntennaSerial')    or 'N/A'),
                ant_h       = float(r['AntennaHeight']) if r.get('AntennaHeight') is not None else 0.0,
                height_code = str(r.get('HeightCode')       or ''),
                radome      = str(r.get('RadomeCode')       or 'NONE'),
            ))

    # ── 5. Contacts (api_rolepersonstation → api_person, api_stationrole) ────
    contacts = []
    if show_contacts:
        contact_rows = cnn.query_float(
            f"""SELECT p.first_name, p.last_name, p.phone, p.email,
                       r.name AS role
                FROM api_rolepersonstation rps
                JOIN api_person      p ON rps.person_id = p.id
                JOIN api_stationrole r ON rps.role_id   = r.id
                WHERE rps.station_id = {api_id}
                ORDER BY r.name, p.last_name""",
            as_dict=True)
        for c in contact_rows:
            first    = str(c.get('first_name') or '')
            last     = str(c.get('last_name')  or '')
            initials = (first[:1] + last[:1]).upper() if (first or last) else '?'
            contacts.append(Contact(
                initials = initials,
                name     = f"{first} {last}".strip(),
                role     = str(c.get('role') or 'Contact'),
                phone    = c.get('phone') or None,
                email    = c.get('email') or None,
            ))

    # ── 6. Visits ────────────────────────────────────────────────────────────
    visits = []
    if show_visits:
        visit_rows = cnn.query_float(
            f"""SELECT v.id, v.date,
                       v.log_sheet_filename, v.navigation_filename,
                       v.comments,
                       c.name AS campaign_name
                FROM api_visits v
                LEFT JOIN api_campaigns c ON v.campaign_id = c.id
                WHERE v.station_id = {api_id}
                ORDER BY v.date DESC""",
            as_dict=True)
        for vr in visit_rows:
            vid = int(vr['id'])

            # Personnel
            pers = cnn.query_float(
                f"""SELECT p.first_name, p.last_name
                    FROM api_visits_people vp
                    JOIN api_person p ON vp.person_id = p.id
                    WHERE vp.visits_id = {vid}
                    ORDER BY p.last_name""",
                as_dict=True)
            personnel = [f"{r['first_name']} {r['last_name']}".strip() for r in pers]

            # GNSS data observation files — count total, fetch first 4 only
            gf_cnt = cnn.query_float(
                f"SELECT COUNT(*) AS n FROM api_visitgnssdatafiles WHERE visit_id = {vid}",
                as_dict=True)
            obs_files_total = int(gf_cnt[0]['n']) if gf_cnt else 0
            gf = cnn.query_float(
                f"""SELECT filename FROM api_visitgnssdatafiles
                    WHERE visit_id = {vid}
                    ORDER BY filename LIMIT 4""",
                as_dict=True)
            obs_files = [r['filename'] for r in gf if r.get('filename')]

            # Attached files: log sheet + navigation file + api_visitattachedfiles
            att_prefix: list = []
            if str(vr.get('log_sheet_filename') or '').strip():
                att_prefix.append(str(vr['log_sheet_filename']))
            if str(vr.get('navigation_filename') or '').strip():
                att_prefix.append(str(vr['navigation_filename']))
            af_cnt = cnn.query_float(
                f"SELECT COUNT(*) AS n FROM api_visitattachedfiles WHERE visit_id = {vid}",
                as_dict=True)
            att_extra_total = int(af_cnt[0]['n']) if af_cnt else 0
            att_files_total = len(att_prefix) + att_extra_total
            needed = max(0, 4 - len(att_prefix))
            if needed:
                af = cnn.query_float(
                    f"""SELECT filename FROM api_visitattachedfiles
                        WHERE visit_id = {vid}
                        ORDER BY filename LIMIT {needed}""",
                    as_dict=True)
                att_prefix += [r['filename'] for r in af if r.get('filename')]
            att_files = att_prefix

            # Photos (api_visitimages.image is a relative path from media root)
            photos = cnn.query_float(
                f"""SELECT image, name, description
                    FROM api_visitimages
                    WHERE visit_id = {vid}
                    ORDER BY id""",
                as_dict=True)
            photo_paths    = [_media(r['image']) for r in photos]
            photo_captions = [str(r.get('description') or r.get('name') or '') for r in photos]
            valid = [(p, c) for p, c in zip(photo_paths, photo_captions) if p]
            if valid:
                photo_paths, photo_captions = map(list, zip(*valid))
            else:
                photo_paths, photo_captions = [], []

            visits.append(Visit(
                date             = str(vr['date'])[:10] if vr.get('date') else '',
                campaign         = vr.get('campaign_name') or None,
                personnel        = personnel,
                obs_files        = obs_files,
                att_files        = att_files,
                obs_files_total  = obs_files_total,
                att_files_total  = att_files_total,
                photo_paths      = list(photo_paths),
                photo_captions   = list(photo_captions),
                comments         = str(vr.get('comments') or '').strip() or None,
            ))

    # ── 7. Station photos (all api_stationimages entries) ───────────────────
    # monument_path already set from api_monumenttype in step 2
    station_images = []
    img_rows = cnn.query_float(
        f"""SELECT image, description FROM api_stationimages
            WHERE station_id = {api_id}
            ORDER BY id""",
        as_dict=True)
    for r in img_rows:
        p = _media(r.get('image'))
        if p:
            caption = str(r.get('description') or '').strip()
            station_images.append((p, caption))

    # ── 8. ETM time-series plot (PPP solution, base64 PNG) ───────────────────
    etm_base64 = None
    if show_timeseries:
        try:
            from io import BytesIO as _BytesIO
            from ..etm.core.etm_config import EtmConfig, SolutionOptions
            from ..etm.core.type_declarations import SolutionType
            from ..etm.core.etm_engine import EtmEngine

            _sol = SolutionOptions()
            _sol.solution_type = SolutionType.PPP
            _sol.stack_name    = 'ppp'
            _cfg = EtmConfig(nc, sc, cnn=cnn, solution_options=_sol)
            _cfg.plotting_config.file_io = _BytesIO()
            _etm = EtmEngine(_cfg, cnn=cnn)
            _etm.run_adjustment(cnn=cnn, try_save_to_db=False, try_loading_db=True)
            etm_base64 = _etm.plot()
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning(f'ETM plot failed for {nc}.{sc}: {_e}')

    # ── 9. RINEX data availability plot (base64 PNG) ─────────────────────────
    rinex_base64 = None
    if show_rinex:
        try:
            from ..Utils import plot_rinex_completion
            rinex_base64 = plot_rinex_completion(cnn, nc, sc, landscape=False) or None
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning(f'RINEX plot failed for {nc}.{sc}: {_e}')

    # ── 10. Geodynamic events (ScoreTable, M ≥ 6.0) ─────────────────────────
    geodynamic_events = []
    if show_geodynamics and first_rinex and last_rinex:
        try:
            from ..etm.core.s_score import ScoreTable
            from ..pyDate import Date as _Date
            _sdate = _Date(year=int(first_rinex[:4]) - 5,
                           month=int(first_rinex[5:7]),
                           day=int(first_rinex[8:10]))
            _edate = _Date(year=int(last_rinex[:4]),
                           month=int(last_rinex[5:7]),
                           day=int(last_rinex[8:10]))
            _score = ScoreTable(cnn, nc, sc, lat, lon, _sdate, _edate,
                                magnitude_limit=6.0, include_all_events=True)
            geodynamic_events = _score.table
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning(
                f'Geodynamic events failed for {nc}.{sc}: {_e}')

    # ── Logo (bundled with the package) ──────────────────────────────────────
    _logo = Path(__file__).parent / 'geode_logo.png'
    logo_path = str(_logo) if _logo.exists() else None

    return StationReport(
        network            = nc.upper(),
        station            = sc.upper(),
        country            = country,
        status             = status,
        status_color       = status_color,
        comms              = has_comms,
        battery            = has_battery,
        station_type       = station_type,
        lat                = lat,
        lon                = lon,
        height             = height,
        x_ecef             = x_ecef,
        y_ecef             = y_ecef,
        z_ecef             = z_ecef,
        location_desc      = location_desc,
        monument           = monument,
        first_rinex        = first_rinex,
        last_rinex         = last_rinex,
        comments           = comments,
        instrument_history = instrument_history,
        instrument_history_total = instrument_history_total,
        visits             = visits,
        contacts           = contacts,
        monument_path      = monument_path,
        navigation_kml_path = navigation_kml_path,
        station_images     = station_images,
        etm_base64         = etm_base64,
        rinex_base64       = rinex_base64,
        geodynamic_events  = geodynamic_events,
        logo_path          = logo_path,
    )


# ── Example data — ARS.AT47 ────────────────────────────────────────────────────

def _example_station() -> StationReport:
    """ARS.AT47 — hardcoded example matching the real station data."""
    BASE = Path(__file__).parent / "report_assets"

    def asset(name: str) -> Optional[str]:
        p = BASE / name
        return str(p) if p.exists() else None

    return StationReport(
        network="ARS",
        station="AT47",
        country="ARG",
        status="Active",
        comms=False,
        battery=False,
        station_type="Campaign",
        lat=-31.38481834,
        lon=-68.59541219,
        height=886.071,
        x_ecef=1989222.136,
        y_ecef=-5074700.477,
        z_ecef=-3302852.694,
        location_desc="Ruta Nacional 40 &nbsp;&middot;&nbsp; San Juan Province, Argentina",
        monument="Bevis Pin",
        first_rinex="1998-07-17 00:19:30",
        last_rinex="2016-01-11 00:00:00",
        comments=(
            "To request access to the station, first send a message (WhatsApp is okay) "
            "to Peter Faulconer. He will then direct you to Bruno Mele, who lives in the "
            "estancia (capataz) &mdash; he is very helpful. For problems related to the "
            "internet connection, contact Tomas Argibay. He is not in the estancia but "
            "will know the general status of the connection."
        ),
        logo_path=str(Path("/mnt/user-data/uploads/Lowres-GeoDe-Logo-final-13-White.png")),
        map_general_path=asset("grid_map_general.jpg"),
        map_detail_path=asset("grid_map_detail.jpg"),
        satellite_path=asset("grid_satellite.jpg"),
        monument_path=asset("grid_monument.jpg"),
        instrument_history=[
            InstrumentSession("1998-07-17","1998-07-18","ASHTECH Z-XII3","CR13088","1E95","&mdash;","ASH700936D_M","LP00482", 1.1574,"DHARP","NONE"),
            InstrumentSession("2002-07-13","2002-07-14","ASHTECH Z-XII3","CR13075","CD00","&mdash;","ASH700936D_M","LP01716", 1.1574,"DHARP","NONE"),
            InstrumentSession("2004-07-27","2004-07-28","ASHTECH UZ-12", "N/A",    "CN00","&mdash;","ASH700936D_M","N/A",     1.1574,"DHARP","NONE"),
            InstrumentSession("2015-06-30","2015-07-03","TRIMBLE NETRS", "CR13088","1.3-1","1.30","ASH700936D_M","4611206678",0.6574,"DHARP","NONE"),
            InstrumentSession("2016-01-09","2016-01-11","TRIMBLE NETRS", "CR13088","1.3-1","1.30","ASH700936D_M","4611206678",0.6574,"DHARP","NONE"),
            InstrumentSession("2018-09-17","2018-09-19","TRIMBLE 5700",  "4814150290","2.32","0.00","TRM39105.00","12578162",1.0470,"DHARP","NONE"),
        ],
        contacts=[
            Contact("BM","Bruno Mele",        "Local Coordinator", "+54 9 2944 809533", None,                         "Lives in the estancia (capataz). Main on-site point of contact."),
            Contact("FB","Francisco Bunge",   "Site Owner",        None,                "estanciaromolinos@gmail.com", None),
            Contact("PF","Peter Faulconer",   "Station Manager",   "+54 9 2944 808114", None,                         "First contact for access requests. Will direct you to Bruno Mele."),
            Contact("JP","Juan Pablo Parola", "Station Manager",   "+54 9 2995 391666", "agrimjuanpabloparola@gmail.com", None),
            Contact("TA","Tomas Argibay",     "Network Manager",   "+54 9 2984 126864", None,                         "Not on-site. Contact for internet/connection issues."),
        ],
        visits=[
            Visit("2016-01-09","2016-01-11","Maule Jan 2016",
                  ["Hugo Baigorri","Leonardo Videla"],
                  ["AT47201601090000a.T00","AT47201601100000a.T00","AT47201601110000a.T00"],
                  ["AT47_2016-01-09.pdf"],
                  [asset(f"visit_2016_photo{i}.jpg") for i in range(1,5) if asset(f"visit_2016_photo{i}.jpg")],
                  ["Antenna installation · view south","Antenna and receiver unit","Site overview · rocky outcrop","Antenna detail · view north"]),
            Visit("2015-06-30","2015-07-02",None,
                  [],
                  ["AT47201506300000a.T00","AT47201507010000a.T00","AT47201507020000a.T00"],
                  ["AT47_2015-06-30.pdf"],
                  [asset(f"visit_2015_photo{i}.jpg") for i in range(1,6) if asset(f"visit_2015_photo{i}.jpg")],
                  ["Antenna setup · close view","Site environment · arid terrain","Antenna with RN40 in background","Site panorama · view east","Antenna tripod detail"]),
            Visit("2004-07-27","2004-07-28",None,["Diego Denett","Carlos Gaitan"],[],["AT47_2004-07-27.pdf","AT47_2004-07-28.pdf"],[],[]),
            Visit("2002-07-13","2002-07-14",None,["Horacio Barrera","Adolfo Garcia"],[],["AT47_2002-07-13.pdf","AT47_2002-07-14.pdf"],[],[]),
            Visit("1998-07-17","1998-07-18",None,["Carlos Gardini","Knight (first name unconfirmed)"],[],["AT47_1998-07-17.pdf","AT47_1998-07-18.pdf"],[],[]),
        ],
    )
