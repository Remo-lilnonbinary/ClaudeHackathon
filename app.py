"""
AYUDA — crisis coordination API: WhatsApp reports + geospatial crisis layers.
"""

from __future__ import annotations

import csv
import io
import math
import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any
from xml.sax.saxutils import escape

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

from bot import bot_bp

load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__, template_folder="templates")
CORS(app)
app.register_blueprint(bot_bp)

FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "DEMO_KEY")
NASA_FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"

# In-memory store (citizen reports from WhatsApp)
reports: list[dict[str, Any]] = []
_report_id_seq = 0


def _next_report_id() -> int:
    global _report_id_seq
    _report_id_seq += 1
    return _report_id_seq


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _demo_timestamp_minutes_ago(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _seed_demo_reports() -> list[dict[str, Any]]:
    """Replace in-memory reports with 10 Napa/Sonoma wildfire + flood scenario rows."""
    global reports, _report_id_seq
    rows: list[dict[str, Any]] = [
        {
            "id": 1,
            "phone": "+1***4821",
            "lat": 38.58,
            "lng": -122.60,
            "description": "Fire jumped Highway 29 near Calistoga. Thick smoke, zero visibility.",
            "image_url": "https://picsum.photos/seed/ayuda-calistoga-hwy29/800/600",
            "timestamp": _demo_timestamp_minutes_ago(108.0),
            "status": "reviewed",
            "confidence": "corroborated",
            "tags": ["fire"],
            "urgency": "normal",
        },
        {
            "id": 2,
            "phone": "+1***9034",
            "lat": 38.56,
            "lng": -122.56,
            "description": "Flames on Silverado Trail. Embers crossing the road.",
            "image_url": "https://picsum.photos/seed/ayuda-silverado-trail/800/600",
            "timestamp": _demo_timestamp_minutes_ago(94.5),
            "status": "new",
            "confidence": "unverified",
            "tags": ["fire"],
            "urgency": "normal",
        },
        {
            "id": 3,
            "phone": "+1***7712",
            "lat": 38.57,
            "lng": -122.63,
            "description": "Hillside behind Angwin fully ablaze. Moving downhill fast.",
            "image_url": "https://picsum.photos/seed/ayuda-angwin-hillside/800/600",
            "timestamp": _demo_timestamp_minutes_ago(82.0),
            "status": "actioned",
            "confidence": "corroborated",
            "tags": ["fire"],
            "urgency": "normal",
        },
        {
            "id": 4,
            "phone": "+1***2208",
            "lat": 38.30,
            "lng": -122.30,
            "description": "Smoke and ash in downtown Napa but no evacuation order. Why?",
            "image_url": "https://picsum.photos/seed/ayuda-napa-downtown-smoke/800/600",
            "timestamp": _demo_timestamp_minutes_ago(71.0),
            "status": "new",
            "confidence": "flagged",
            "tags": ["fire"],
            "urgency": "normal",
        },
        {
            "id": 5,
            "phone": "+1***6643",
            "lat": 38.50,
            "lng": -122.75,
            "description": "Burning tree blocking Dry Creek Road. No emergency services here.",
            "image_url": "https://picsum.photos/seed/ayuda-dry-creek-tree/800/600",
            "timestamp": _demo_timestamp_minutes_ago(58.0),
            "status": "new",
            "confidence": "flagged",
            "tags": ["fire", "trapped"],
            "urgency": "high",
        },
        {
            "id": 6,
            "phone": "+1***1190",
            "lat": 38.24,
            "lng": -122.64,
            "description": "Seeing flames from Petaluma. This is NOT on any evacuation map.",
            "image_url": "https://picsum.photos/seed/ayuda-petaluma-flames/800/600",
            "timestamp": _demo_timestamp_minutes_ago(45.0),
            "status": "new",
            "confidence": "flagged",
            "tags": ["fire"],
            "urgency": "normal",
        },
        {
            "id": 7,
            "phone": "+1***5588",
            "lat": 38.40,
            "lng": -122.36,
            "description": "Creek behind Yountville overflowed. Water across road and rising fast.",
            "image_url": "https://picsum.photos/seed/ayuda-yountville-creek/800/600",
            "timestamp": _demo_timestamp_minutes_ago(33.0),
            "status": "reviewed",
            "confidence": "corroborated",
            "tags": ["flood"],
            "urgency": "high",
        },
        {
            "id": 8,
            "phone": "+1***3401",
            "lat": 38.29,
            "lng": -122.46,
            "description": "Storm drain flooding Sonoma Plaza. Ankle-deep in shops.",
            "image_url": "https://picsum.photos/seed/ayuda-sonoma-plaza-flood/800/600",
            "timestamp": _demo_timestamp_minutes_ago(22.0),
            "status": "new",
            "confidence": "unverified",
            "tags": ["flood"],
            "urgency": "normal",
        },
        {
            "id": 9,
            "phone": "+1***9927",
            "lat": 38.55,
            "lng": -122.58,
            "description": "Family trapped. Road blocked both sides. 3 children with us. Please help.",
            "image_url": "https://picsum.photos/seed/ayuda-trapped-family/800/600",
            "timestamp": _demo_timestamp_minutes_ago(14.0),
            "status": "new",
            "confidence": "unverified",
            "tags": ["trapped"],
            "urgency": "critical",
        },
        {
            "id": 10,
            "phone": "+1***4056",
            "lat": 38.48,
            "lng": -122.52,
            "description": "Elderly neighbor cannot move. Smoke getting into house. Address: 1420 Spring St.",
            "image_url": "https://picsum.photos/seed/ayuda-spring-st-neighbor/800/600",
            "timestamp": _demo_timestamp_minutes_ago(6.0),
            "status": "new",
            "confidence": "unverified",
            "tags": ["trapped", "fire"],
            "urgency": "critical",
        },
    ]
    reports = rows
    _report_id_seq = len(rows)
    return rows


@app.route("/api/seed-demo", methods=["GET"])
def seed_demo():
    try:
        data = _seed_demo_reports()
        return jsonify({"ok": True, "count": len(data), "reports": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _bbox_from_center(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """West, south, east, north in degrees."""
    lat_rad = math.radians(lat)
    cos_lat = math.cos(lat_rad)
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * cos_lat) if abs(cos_lat) > 1e-6 else radius_km / 111.0
    west = lng - dlng
    south = lat - dlat
    east = lng + dlng
    north = lat + dlat
    return (west, south, east, north)


def _parse_firms_csv(csv_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not csv_text or not csv_text.strip():
        return out
    # Skip FIRMS comment lines starting with #
    lines = csv_text.strip().splitlines()
    data_lines = [ln for ln in lines if not ln.strip().startswith("#")]
    if not data_lines:
        return out
    reader = csv.DictReader(StringIO("\n".join(data_lines)))
    for row in reader:
        if not row:
            continue
        lat_key = next((k for k in row if k and k.lower() in ("latitude", "lat")), None)
        lng_key = next((k for k in row if k and k.lower() in ("longitude", "lon", "lng")), None)
        if not lat_key or not lng_key:
            continue
        try:
            lat = float(row[lat_key].strip())
            lng = float(row[lng_key].strip())
        except (TypeError, ValueError, AttributeError):
            continue
        brightness = None
        for cand in ("bright_ti4", "brightness", "bright_t31", "frp"):
            if cand in row and row[cand] not in (None, ""):
                try:
                    brightness = float(str(row[cand]).strip())
                except ValueError:
                    brightness = row[cand]
                break
        conf = row.get("confidence") or row.get("conf") or ""
        acq_date = row.get("acq_date") or row.get("date") or ""
        acq_time = row.get("acq_time") or row.get("time") or ""
        if acq_date and acq_time:
            acq_display = f"{acq_date} {acq_time}".strip()
        else:
            acq_display = f"{acq_date}{acq_time}".strip() or acq_date or acq_time
        out.append(
            {
                "lat": lat,
                "lng": lng,
                "brightness": brightness,
                "confidence": str(conf).strip() if conf is not None else "",
                "acq_date": acq_display or acq_date,
            }
        )
    return out


def _overpass_flood_query(lat: float, lng: float, radius_m: int) -> str:
    r = int(radius_m)
    return (
        f'[out:json];'
        f'(node["natural"="water"](around:{r},{lat},{lng});'
        f'way["waterway"](around:{r},{lat},{lng}););out center;'
    )


def _parse_overpass_flood(data: dict[str, Any]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for el in data.get("elements") or []:
        t = el.get("type")
        if t == "node":
            lat, lon = el.get("lat"), el.get("lon")
            if lat is not None and lon is not None:
                zones.append({"type": "node", "lat": float(lat), "lng": float(lon), "tags": el.get("tags") or {}})
        elif t == "way":
            c = el.get("center") or {}
            clat, clon = c.get("lat"), c.get("lon")
            if clat is not None and clon is not None:
                zones.append({"type": "way", "lat": float(clat), "lng": float(clon), "tags": el.get("tags") or {}})
    return zones


def _simplify_nws_alert(feature: dict[str, Any]) -> dict[str, Any]:
    p = feature.get("properties") or {}
    return {
        "id": p.get("id"),
        "event": p.get("event"),
        "headline": p.get("headline"),
        "description": p.get("description"),
        "severity": p.get("severity"),
        "urgency": p.get("urgency"),
        "certainty": p.get("certainty"),
        "effective": p.get("effective"),
        "expires": p.get("expires"),
        "areaDesc": p.get("areaDesc"),
    }


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/reports", methods=["GET"])
def list_reports():
    try:
        status_filter = request.args.get("status")
        data = reports
        if status_filter:
            data = [r for r in reports if r.get("status") == status_filter]
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports", methods=["POST"])
def create_report():
    """Internal: called by bot.py, not end users."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"error": "JSON object required"}), 400
        rid = body.get("id") or _next_report_id()
        ts = body.get("timestamp") or _iso_now()
        status = body.get("status") if body.get("status") in ("new", "reviewed", "actioned") else "new"
        conf = (
            body.get("confidence")
            if body.get("confidence") in ("unverified", "corroborated", "flagged")
            else "unverified"
        )
        urg = body.get("urgency")
        if urg not in ("critical", "high", "normal"):
            urg = "normal"
        raw_tags = body.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(x).strip().lower() for x in raw_tags if str(x).strip()]
        else:
            tags = []
        report = {
            "id": rid,
            "phone": body.get("phone", ""),
            "lat": float(body["lat"]) if body.get("lat") is not None else None,
            "lng": float(body["lng"]) if body.get("lng") is not None else None,
            "description": body.get("description", ""),
            "image_url": body.get("image_url") or body.get("imageUrl") or "",
            "timestamp": ts,
            "status": status,
            "confidence": conf,
            "tags": tags,
            "urgency": urg,
        }
        # Replace if same id exists (idempotent upsert for int ids)
        existing = next((i for i, r in enumerate(reports) if r.get("id") == rid), None)
        if existing is not None:
            reports[existing] = report
        else:
            reports.append(report)
        return jsonify(report), 201
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports/<report_id>", methods=["GET"])
def get_report(report_id):
    try:
        for r in reports:
            if str(r.get("id")) == str(report_id):
                return jsonify(r)
        return jsonify({"error": "Not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports/<report_id>", methods=["PATCH"])
def patch_report(report_id):
    try:
        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"error": "JSON object required"}), 400
        for r in reports:
            if str(r.get("id")) == str(report_id):
                if "status" in body and body["status"] in ("new", "reviewed", "actioned"):
                    r["status"] = body["status"]
                if "confidence" in body and body["confidence"] in ("unverified", "corroborated", "flagged"):
                    r["confidence"] = body["confidence"]
                return jsonify(r)
        return jsonify({"error": "Not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/crisis-data", methods=["GET"])
def crisis_data():
    fires: list[dict[str, Any]] = []
    flood_zones: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lng required as numbers"}), 400

    try:
        radius_km = float(request.args.get("radius_km", "50"))
    except ValueError:
        radius_km = 50.0

    west, south, east, north = _bbox_from_center(lat, lng, radius_km)
    bbox = f"{west},{south},{east},{north}"

    # NASA FIRMS
    try:
        url = f"{NASA_FIRMS_BASE}/{FIRMS_MAP_KEY}/VIIRS_SNPP_NRT/{bbox}/1"
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        fires = _parse_firms_csv(r.text)
    except Exception as e:
        errors.append(f"firms: {e}")

    # Overpass flood features
    try:
        q = _overpass_flood_query(lat, lng, int(radius_km * 1000))
        r2 = requests.post(OVERPASS_URL, data=q, timeout=90)
        r2.raise_for_status()
        flood_zones = _parse_overpass_flood(r2.json())
    except Exception as e:
        errors.append(f"overpass: {e}")

    payload: dict[str, Any] = {"fires": fires, "flood_zones": flood_zones}
    if errors:
        payload["warnings"] = errors
    return jsonify(payload)


@app.route("/api/crisis-data/alerts", methods=["GET"])
def crisis_alerts():
    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
    except (TypeError, ValueError):
        return jsonify([])

    try:
        url = f"{NWS_ALERTS_URL}?point={lat},{lng}"
        headers = {"User-Agent": "(AYUDA crisis dashboard, contact@local)", "Accept": "application/geo+json"}
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        gj = r.json()
        features = gj.get("features") or []
        simplified = [_simplify_nws_alert(f) for f in features]
        return jsonify(simplified)
    except Exception:
        return jsonify([])


@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    try:
        buf = io.StringIO()
        fieldnames = [
            "id",
            "phone",
            "lat",
            "lng",
            "description",
            "image_url",
            "timestamp",
            "status",
            "confidence",
            "tags",
            "urgency",
        ]
        w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in reports:
            out = {k: row.get(k, "") for k in fieldnames}
            tg = row.get("tags")
            if isinstance(tg, list):
                out["tags"] = ";".join(str(x) for x in tg)
            w.writerow(out)
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=ayuda_reports.csv"},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _cap_xml_for_flagged() -> str:
    flagged = [r for r in reports if r.get("confidence") == "flagged"]
    sender = "AYUDA-Crisis-Platform"
    sent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    infos: list[str] = []
    for r in flagged:
        rid = escape(str(r.get("id", "")))
        desc = escape(str(r.get("description", "")))
        phone = escape(str(r.get("phone", "")))
        lat = r.get("lat")
        lng = r.get("lng")
        circle = ""
        if lat is not None and lng is not None:
            try:
                # CAP circle: "latitude,longitude radius_km"
                circle = f"<circle>{float(lat)},{float(lng)} 0.5</circle>"
            except (TypeError, ValueError):
                circle = ""
        identifier = f"urn:uuid:{uuid.uuid4()}"
        infos.append(
            f"""  <info>
    <category>Other</category>
    <event>Citizen crisis report (flagged)</event>
    <urgency>Immediate</urgency>
    <severity>Severe</severity>
    <certainty>Observed</certainty>
    <description>Report ID {rid}. Contact: {phone}. {desc}</description>
    <parameter>
      <valueName>report_id</valueName>
      <value>{rid}</value>
    </parameter>
    <area>
      {circle}
    </area>
  </info>"""
        )
    if not infos:
        infos.append(
            """  <info>
    <category>Other</category>
    <event>No flagged reports</event>
    <urgency>Past</urgency>
    <severity>Minor</severity>
    <certainty>Observed</certainty>
    <description>No flagged reports in the current store.</description>
  </info>"""
        )
    body = "\n".join(infos)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>{escape(str(uuid.uuid4()))}</identifier>
  <sender>{escape(sender)}</sender>
  <sent>{sent}</sent>
  <status>Actual</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
{body}
</alert>
"""
    return xml


@app.route("/api/export/cap", methods=["GET"])
def export_cap():
    try:
        xml = _cap_xml_for_flagged()
        return Response(
            xml,
            mimetype="application/xml",
            headers={"Content-Disposition": "attachment; filename=ayuda_flagged.cap.xml"},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
