"""
report.py
=========
HTML report generation for the GeoDE Campaign Planner.

Single public function: generate_html(plan, config) -> str
Returns a complete, self-contained HTML string — no external files needed.
"""

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from io import BytesIO

import requests

_DAY_COLORS = ['#2563eb', '#dc2626', '#16a34a', '#ea580c', '#9333ea']

_LEAFLET_CSS_URL = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'
_LEAFLET_JS_URL  = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
_LEAFLET_TIMEOUT = 15

_LOGO_PATH = os.path.join(os.path.dirname(__file__), '..', 'reports', 'geode_logo.png')

log = logging.getLogger('CampaignPlanner')


def _encode_logo() -> str:
    """Return a data: URI for the GeoDE logo PNG, or empty string on failure."""
    try:
        with open(_LOGO_PATH, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        return f'data:image/png;base64,{b64}'
    except Exception:
        return ''


def _generate_static_map(plan: dict, width: int = 900, height: int = 420) -> str:
    """
    Render a static PNG route map using the staticmap library.
    Returns a data: URI string for embedding, or '' on any failure.
    The image is used only for the print view; the interactive Leaflet map
    is shown on screen.
    """
    try:
        from staticmap import StaticMap, CircleMarker, Line as SLine

        m = StaticMap(width, height,
                      url_template='https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                      padding_x=30, padding_y=30,
                      tile_request_timeout=15)

        for day_idx, day in enumerate(plan['days']):
            color = _DAY_COLORS[day_idx % len(_DAY_COLORS)]
            for stop in day['stops']:
                if stop.get('geometry') and len(stop['geometry']) > 1:
                    coords = [(c[0], c[1]) for c in stop['geometry']]
                    m.add_line(SLine(coords, color, 3))
                if stop.get('lat') is not None and stop.get('lon') is not None:
                    stype = stop['type']
                    if stype == 'station':
                        fill, size = '#dc2626', 12
                    elif stype == 'new_site':
                        fill, size = '#f59e0b', 12
                    else:
                        fill, size = '#2563eb', 16
                    m.add_marker(CircleMarker((stop['lon'], stop['lat']), fill, size))

        image = m.render()
        buf = BytesIO()
        image.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        log.debug('Static map rendered (%dx%d px)', width, height)
        return f'data:image/png;base64,{b64}'
    except Exception as exc:
        log.warning('Static map generation failed (%s); print map will be unavailable.', exc)
        return ''


def _fetch_leaflet() -> tuple:
    """
    Download Leaflet CSS and JS from unpkg.com.
    Returns (css_text, js_text) on success, (None, None) on any failure.
    """
    try:
        css = requests.get(_LEAFLET_CSS_URL, timeout=_LEAFLET_TIMEOUT)
        css.raise_for_status()
        js  = requests.get(_LEAFLET_JS_URL,  timeout=_LEAFLET_TIMEOUT)
        js.raise_for_status()
        log.debug('Leaflet assets fetched for inline embedding (%d + %d bytes)',
                  len(css.content), len(js.content))
        return css.text, js.text
    except Exception as exc:
        log.warning('Could not fetch Leaflet assets (%s); falling back to CDN link.', exc)
        return None, None


def _fmt_minutes(total_minutes: int) -> str:
    """Format integer minutes as 'Xh Ym' or 'Ym'."""
    h, m = divmod(total_minutes, 60)
    return f'{h}h {m:02d}m' if h else f'{m}m'


def _fmt_date_long(date_str: str) -> str:
    """'2025-09-01' → 'Monday, 1 September 2025'"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return dt.strftime('%A, %-d %B %Y')


def _fmt_time(t: str) -> str:
    """Render 'HH:MM' or 'HH:MM +N' — the +N part as a small superscript."""
    if not t:
        return '&mdash;'
    if ' +' in t:
        hhmm, offset = t.split(' ', 1)
        return (f'{hhmm}&thinsp;<sup style="color:#9b9a96;font-size:.75em">'
                f'{offset}</sup>')
    return t


def _stop_rows(day: dict, fuel_per_km: float) -> str:
    rows = []
    for s in day['stops']:
        if s.get('type') == 'intermediate':
            warn_style = ' style="background:#f0f0ee"'
        elif s.get('warning'):
            warn_style = ' style="background:#fef9c3"'
        else:
            warn_style = ''
        drive_cell = (
            f'{_fmt_minutes(s["leg_drive_minutes"])}' if s['leg_drive_minutes'] else '&mdash;'
        )
        km_cell    = f'{s["leg_km"]:,.0f}' if s['leg_km'] > 0 else '&mdash;'
        fuel_cell  = (f'${s["leg_fuel_cost"]:.2f}'
                      if fuel_per_km and s['leg_km'] > 0 else '&mdash;')
        notes      = s.get('warning') or ''
        code = s.get('code', '')
        if code and s['name'].lower() != code.lower():
            name_cell = f'{s["name"]} <span style="color:#9b9a96">({code.upper()})</span>'
        else:
            name_cell = s['name']
        type_labels = {
            'station':     'Station',
            'new_site':    'New site',
            'origin':      'Origin',
            'destination': 'Destination',
            'intermediate': 'Overnight',
        }
        type_label = type_labels.get(s['type'], s['type'].capitalize())
        rows.append(
            f'<tr{warn_style}>'
            f'<td>{name_cell}</td>'
            f'<td class="center">{type_label}</td>'
            f'<td class="center">{_fmt_time(s["arrival"])}</td>'
            f'<td class="center">{_fmt_time(s["departure"])}</td>'
            f'<td class="center">{drive_cell}</td>'
            f'<td class="center">{km_cell}</td>'
            f'<td class="center">{fuel_cell}</td>'
            f'<td class="notes">{notes}</td>'
            f'</tr>'
        )
    # Totals row
    fuel_total = (f'${day["day_total_fuel_cost"]:.2f}'
                  if fuel_per_km else '&mdash;')
    rows.append(
        f'<tr class="totals-row">'
        f'<td colspan="4">Day totals</td>'
        f'<td class="center">{_fmt_minutes(day["day_total_drive_minutes"])}</td>'
        f'<td class="center">{day["day_total_km"]:,.0f} km</td>'
        f'<td class="center">{fuel_total}</td>'
        f'<td></td>'
        f'</tr>'
    )
    return '\n'.join(rows)


def _day_sections(plan: dict, fuel_per_km: float) -> str:
    sections = []
    for day in plan['days']:
        color   = _DAY_COLORS[(day['day_number'] - 1) % len(_DAY_COLORS)]
        heading = f'Day {day["day_number"]} &nbsp;&mdash;&nbsp; {_fmt_date_long(day["date"])}'
        dot     = (f'<span style="display:inline-block;width:12px;height:12px;'
                   f'border-radius:50%;background:{color};margin-right:8px;'
                   f'vertical-align:middle"></span>')
        sections.append(f'''
<section class="day-section">
  <h2>{dot}{heading}</h2>
  <table class="plan-table">
    <colgroup>
      <col style="width:32%">
      <col style="width:9%">
      <col style="width:8%">
      <col style="width:8%">
      <col style="width:9%">
      <col style="width:7%">
      <col style="width:9%">
      <col style="width:18%">
    </colgroup>
    <thead>
      <tr>
        <th>Site or City</th>
        <th class="center">Type</th>
        <th class="center">Arrive</th>
        <th class="center">Done</th>
        <th class="center">Drive</th>
        <th class="center">Km</th>
        <th class="center">Fuel</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>
{_stop_rows(day, fuel_per_km)}
    </tbody>
  </table>
</section>''')
    return '\n'.join(sections)


def _cost_summary_html(plan: dict, config: dict) -> str:
    """Return the HTML for the campaign cost summary table."""
    summary           = plan['summary']
    fuel_per_km       = float(config.get('fuel_cost_per_km', 0.0))
    lodging_per_night = float(config.get('lodging_cost_per_night', 0.0))
    time_on_site_min  = int(config.get('time_on_site_minutes', 120))
    n_stations        = summary['total_stations']
    n_nights          = max(summary['total_days'] - 1, 0)
    total_km          = summary['total_km']
    fuel_cost         = summary['total_fuel_cost']
    lodging_cost      = summary['total_lodging_cost']
    total_on_site_min = n_stations * time_on_site_min
    grand_total       = fuel_cost + lodging_cost

    def _row(label, qty, rate, amount):
        amt = f'${amount:,.2f}' if amount is not None else '&mdash;'
        return (f'<tr>'
                f'<td>{label}</td>'
                f'<td class="center">{qty}</td>'
                f'<td class="center">{rate}</td>'
                f'<td class="right">{amt}</td>'
                f'</tr>')

    rows = [
        _row('Driving distance',
             f'{total_km:,.0f} km',
             '&mdash;', None),
        _row('Driving time',
             _fmt_minutes(summary["total_drive_minutes"]),
             '&mdash;', None),
        _row('Time on site',
             (f'{n_stations} station{"s" if n_stations != 1 else ""}'
              f' &times; {_fmt_minutes(time_on_site_min)}'
              f' = {_fmt_minutes(total_on_site_min)} total'),
             '&mdash;', None),
    ]

    if fuel_per_km:
        rows.append(_row('Fuel',
                         f'{total_km:,.0f} km',
                         f'${fuel_per_km:.4g}/km',
                         fuel_cost))

    nights_label = f'{n_nights} night{"s" if n_nights != 1 else ""}'
    if lodging_per_night:
        rows.append(_row('Lodging',
                         nights_label,
                         f'${lodging_per_night:.2f}/night',
                         lodging_cost))
    else:
        rows.append(_row('Nights away', nights_label, '&mdash;', None))

    if fuel_per_km or lodging_per_night:
        rows.append(
            f'<tr class="totals-row">'
            f'<td colspan="3">Total estimated cost</td>'
            f'<td class="right">${grand_total:,.2f}</td>'
            f'</tr>'
        )

    return f'''
<section class="day-section cost-summary" style="page-break-before:auto">
  <h2>Campaign Cost Summary</h2>
  <table class="plan-table">
    <thead>
      <tr>
        <th style="width:45%">Item</th>
        <th class="center">Quantity</th>
        <th class="center">Rate</th>
        <th class="right" style="width:12%">Amount</th>
      </tr>
    </thead>
    <tbody>
{''.join(rows)}
    </tbody>
  </table>
</section>'''


def generate_html(plan: dict, config: dict) -> str:
    """
    Return a complete, self-contained HTML string for the campaign plan.

    Parameters
    ----------
    plan   : dict returned by services.compute_plan()
    config : validated config dict (start_date, stations, start_city, etc.)
    """
    summary         = plan['summary']
    fuel_per_km     = float(config.get('fuel_cost_per_km', 0.0))
    lodging_per_night = float(config.get('lodging_cost_per_night', 0.0))
    start_date      = config['start_date']
    end_date_dt     = (datetime.strptime(start_date, '%Y-%m-%d')
                       + timedelta(days=summary['total_days'] - 1))
    end_date        = end_date_dt.strftime('%Y-%m-%d')
    generated_at    = datetime.now().strftime('%d %b %Y %H:%M')

    # Date range for the header
    start_long = _fmt_date_long(start_date)
    end_long   = _fmt_date_long(end_date)
    date_range = start_long if start_date == end_date else f'{start_long} – {end_long}'

    # Logo
    logo_uri  = _encode_logo()
    logo_html = (f'<img src="{logo_uri}" alt="GeoDE" '
                 f'style="height:38px;width:auto;display:block">'
                 if logo_uri else '')

    # Summary bar values
    fuel_box = (f'<div class="box"><div class="box-label">Fuel cost</div>'
                f'<div class="box-value">${summary["total_fuel_cost"]:.2f}</div></div>'
                if fuel_per_km else
                f'<div class="box"><div class="box-label">Fuel cost</div>'
                f'<div class="box-value">N/A</div></div>')

    lodging_box = (f'<div class="box"><div class="box-label">Lodging</div>'
                   f'<div class="box-value">${summary["total_lodging_cost"]:.2f}</div></div>'
                   if lodging_per_night else
                   f'<div class="box"><div class="box-label">Lodging</div>'
                   f'<div class="box-value">N/A</div></div>')

    summary_bar = f'''
<div class="summary-bar">
  <div class="box">
    <div class="box-label">Total distance</div>
    <div class="box-value">{summary["total_km"]:,.0f} km</div>
  </div>
  <div class="box">
    <div class="box-label">Driving time</div>
    <div class="box-value">{_fmt_minutes(summary["total_drive_minutes"])}</div>
  </div>
  {fuel_box}
  {lodging_box}
  <div class="box">
    <div class="box-label">Days</div>
    <div class="box-value">{summary["total_days"]}</div>
  </div>
  <div class="box">
    <div class="box-label">Stations</div>
    <div class="box-value">{summary["total_stations"]}</div>
  </div>
</div>'''

    day_sections      = _day_sections(plan, fuel_per_km)
    cost_summary      = _cost_summary_html(plan, config)

    # Grand totals footer
    fuel_total_str    = (f'${summary["total_fuel_cost"]:.2f}' if fuel_per_km else 'N/A')
    lodging_total_str = (f'${summary["total_lodging_cost"]:.2f}' if lodging_per_night else 'N/A')
    footer_totals  = (f'{summary["total_km"]:,.0f} km &nbsp;·&nbsp; '
                      f'{_fmt_minutes(summary["total_drive_minutes"])} driving &nbsp;·&nbsp; '
                      f'{fuel_total_str} fuel &nbsp;·&nbsp; '
                      f'{lodging_total_str} lodging')

    plan_json = json.dumps(plan, ensure_ascii=False)

    # Static map for print (generated now while network is available)
    log.info('Rendering static map for print...')
    static_map_uri  = _generate_static_map(plan)
    static_map_html = (f'<img id="map-static" src="{static_map_uri}" alt="Campaign route map">'
                       if static_map_uri else '')

    # Embed Leaflet inline so the HTML is fully self-contained (works offline /
    # from file:// URLs where CDN links fail due to CORS restrictions).
    leaflet_css, leaflet_js = _fetch_leaflet()
    if leaflet_css and leaflet_js:
        leaflet_head  = f'<style>\n{leaflet_css}\n</style>'
        leaflet_prejs = f'<script>\n{leaflet_js}\n</script>\n'
    else:
        # Fallback: CDN link (requires network at view time)
        leaflet_head = (
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"'
            ' integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>'
        )
        leaflet_prejs = (
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"'
            ' integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV/XN/WLs="'
            ' crossorigin=""></script>\n'
        )

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campaign Plan</title>
{leaflet_head}
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  font-size: 13px;
  color: #1a1a18;
  background: #f5f5f3;
  padding-bottom: 40px;
}}
header {{
  background: #1a1a18;
  color: #fff;
  padding: 18px 28px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}}
header h1 {{
  font-size: 20px;
  font-weight: 700;
  letter-spacing: .01em;
}}
header .subtitle {{
  font-size: 11px;
  color: #9b9a96;
  margin-top: 4px;
}}
.summary-bar {{
  display: flex;
  gap: 0;
  background: #fff;
  border-bottom: 1px solid #e0ded8;
}}
.box {{
  flex: 1;
  padding: 14px 20px;
  border-right: 1px solid #e0ded8;
  text-align: center;
}}
.box:last-child {{ border-right: none; }}
.box-label {{
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: #9b9a96;
  margin-bottom: 4px;
}}
.box-value {{
  font-size: 22px;
  font-weight: 700;
  color: #1a1a18;
}}
#map {{
  width: 100%;
  height: 450px;
  border-bottom: 1px solid #e0ded8;
}}
.content {{
  max-width: 960px;
  margin: 0 auto;
  padding: 0 20px;
}}
.day-section {{
  margin-top: 32px;
}}
.day-section h2 {{
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 10px;
  color: #1a1a18;
}}
.plan-table {{
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  background: #fff;
  border: 1px solid #e0ded8;
  border-radius: 6px;
  overflow: hidden;
  font-size: 12px;
}}
.plan-table th {{
  background: #f5f5f3;
  padding: 7px 10px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: #6b6a66;
  border-bottom: 1px solid #e0ded8;
  text-align: left;
}}
.plan-table td {{
  padding: 7px 10px;
  border-bottom: 1px solid #f0eeea;
  vertical-align: middle;
}}
.plan-table tr:last-child td {{ border-bottom: none; }}
.plan-table .center {{ text-align: center; }}
.plan-table .right  {{ text-align: right; }}
.plan-table .notes  {{ color: #854f0b; }}
.totals-row td {{
  font-weight: 700;
  background: #f5f5f3;
  border-top: 1px solid #e0ded8;
}}
footer.page-footer {{
  margin-top: 40px;
  padding: 14px 20px;
  text-align: center;
  font-size: 11px;
  color: #9b9a96;
  border-top: 1px solid #e0ded8;
  background: #fff;
}}
#map-static {{
  display: none;
  width: 100%;
  border-bottom: 1px solid #e0ded8;
}}
@page {{
  size: A4 portrait;
  margin: 1.5cm;
}}
@media print {{
  body {{ background: #fff; font-size: 11px; }}
  header {{
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .summary-bar {{
    flex-wrap: nowrap;
    border: 1px solid #e0ded8;
    margin-bottom: 2px;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .box {{
    flex: 1;
    padding: 6px 8px;
    border-right: 1px solid #e0ded8;
  }}
  .box:last-child {{ border-right: none; }}
  .box-value {{ font-size: 12px; }}
  .box-label {{ font-size: 7px; }}
  #map {{ display: none !important; }}
  #map-static {{
    display: block !important;
    width: 100%;
    height: auto;
    page-break-after: avoid;
  }}
  .content {{ max-width: 100%; padding: 0; }}
  .day-section {{ page-break-inside: avoid; margin-top: 20px; }}
  .day-section h2 {{ font-size: 12px; margin-bottom: 6px; }}
  .plan-table {{ font-size: 9px; }}
  .plan-table th {{ font-size: 8px; padding: 3px 5px; }}
  .plan-table td {{
    padding: 3px 5px;
    white-space: nowrap;
  }}
  /* Allow the stop name (col 1) and notes (col 8) to wrap if needed */
  .plan-table td:first-child {{ white-space: normal; }}
  .plan-table td:last-child  {{ white-space: normal; }}
  footer.page-footer {{ font-size: 10px; margin-top: 20px; }}
}}
</style>
</head>
<body>
<header>
  <div>
    <h1>Campaign Plan</h1>
    <div class="subtitle">{config.get("start_city", "")} &rarr; {config.get("end_city", "")}</div>
    <div class="subtitle">{date_range} &nbsp;&middot;&nbsp; Generated {generated_at}</div>
  </div>
  {logo_html}
</header>

{summary_bar}

<div id="map"></div>
{static_map_html}

<div class="content">
{day_sections}
{cost_summary}
</div>

<footer class="page-footer">
  {footer_totals} &nbsp;&middot;&nbsp; GeoDE &mdash; Geodetic Database Engine
</footer>

{leaflet_prejs}<script>
const PLAN_DATA = {plan_json};

(function () {{
  const map = L.map('map');
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
  }}).addTo(map);

  const DAY_COLORS = {json.dumps(_DAY_COLORS)};
  const allLatLngs = [];

  PLAN_DATA.days.forEach(function(day, di) {{
    const color = DAY_COLORS[di % DAY_COLORS.length];
    day.stops.forEach(function(stop) {{
      // Draw the driving leg polyline
      if (stop.geometry && stop.geometry.length > 1) {{
        const latlngs = stop.geometry.map(function(c) {{ return [c[1], c[0]]; }});
        L.polyline(latlngs, {{color: color, weight: 4, opacity: 0.75}}).addTo(map);
        latlngs.forEach(function(ll) {{ allLatLngs.push(ll); }});
      }}
      // Circle marker (skip intermediate stops — no coordinates)
      if (stop.lat !== null && stop.lon !== null) {{
        const markerColor = stop.type === 'station'  ? '#dc2626'
                          : stop.type === 'new_site' ? '#f59e0b'
                          : '#2563eb';
        const markerRadius = (stop.type === 'origin' || stop.type === 'destination') ? 9 : 7;
        L.circleMarker([stop.lat, stop.lon], {{
          radius:      markerRadius,
          fillColor:   markerColor,
          color:       '#ffffff',
          weight:      2,
          fillOpacity: 0.9
        }}).addTo(map);
        allLatLngs.push([stop.lat, stop.lon]);
      }}
    }});
  }});

  if (allLatLngs.length > 0) {{
    map.fitBounds(allLatLngs, {{padding: [30, 30]}});
  }}
}})();
</script>
</body>
</html>
'''
