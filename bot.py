"""
AYUDA — Twilio WhatsApp webhook: intake flow and report POSTs to the API.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Blueprint, Response, request
from twilio.twiml.messaging_response import MessagingResponse

load_dotenv(Path(__file__).resolve().parent / ".env")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
REPORTS_URL = os.getenv("REPORTS_API_URL", "http://localhost:5001/api/reports")
SESSION_TTL_SEC = 30 * 60
RATE_WINDOW_SEC = 600
RATE_MAX_MSGS = 5

_BASE = Path(__file__).resolve().parent
UPLOAD_DIR = _BASE / "uploads"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "AYUDA-WhatsAppBot/1.0 (non-commercial crisis aid)"

bot_bp = Blueprint("bot", __name__)

_sessions: dict[str, dict[str, Any]] = {}
_msg_times: dict[str, list[float]] = {}

TAG_RULES: list[tuple[str, list[str]]] = [
    ("fire", ["fire", "flames", "smoke", "burning", "embers"]),
    ("flood", ["water", "flood", "rising", "overflow", "submerged"]),
    ("trapped", ["trapped", "stuck", "cannot move", "blocked", "help"]),
    ("medical", ["injured", "hurt", "bleeding", "breathing", "ambulance"]),
]

# crisis-word hints → reply language (demo)
_LANG_MARKERS: list[tuple[str, list[str]]] = [
    ("fr", ["au secours", "inondation"]),
    ("es", ["fuego", "ayuda"]),
    ("pt", ["socorro", "ajuda"]),
]

_COPY: dict[str, dict[str, str]] = {
    "en": {
        "loc_ok": "Thanks. We have your report and location. Coordinators can see it. Stay safe.",
        "ask_loc": "Thanks for reaching out. Tap + > Location > send your current location so we can reach you.",
        "ask_loc_smart": "It sounds like you're near {place}. Is that right? Or tap + > Location to share your exact spot.",
        "logged_no_coords": "We logged your report. Send your location anytime via + > Location.",
        "update": "Update received. Thank you.",
        "geocoded": "Got it. We pinned your report from the place you named. Help is coordinated.",
        "photo": "Photo received. This helps coordinators understand the situation.",
        "photo_loc": " Tap + > Location if you can share your exact spot.",
        "rate_limit": "We have your reports. Updates go to coordinators. We'll reach out if we need more info.",
    },
    "es": {
        "loc_ok": "Gracias. Tenemos su reporte y ubicación. Los coordinadores la ven. Cuídese.",
        "ask_loc": "Gracias. Toque + > Ubicación > envíe su ubicación actual para ayudarle.",
        "ask_loc_smart": "Parece que está cerca de {place}. ¿Correcto? Toque + > Ubicación para el punto exacto.",
        "logged_no_coords": "Registramos su reporte. Envíe su ubicación cuando pueda: + > Ubicación.",
        "update": "Actualización recibida. Gracias.",
        "geocoded": "Entendido. Ubicamos su reporte por el lugar indicado. Coordinamos ayuda.",
        "photo": "Foto recibida. Ayuda a los coordinadores a entender la situación.",
        "photo_loc": " Toque + > Ubicación si puede compartir el punto exacto.",
        "rate_limit": "Tenemos sus reportes. Los coordinadores están informados. Avisaremos si hace falta más.",
    },
    "pt": {
        "loc_ok": "Obrigado. Temos seu relato e localização. Os coordenadores veem. Cuide-se.",
        "ask_loc": "Obrigado. Toque + > Localização > envie sua localização atual para ajudarmos.",
        "ask_loc_smart": "Parece que você está perto de {place}. Certo? Toque + > Localização para o ponto exato.",
        "logged_no_coords": "Registramos seu relato. Envie sua localização quando puder: + > Localização.",
        "update": "Atualização recebida. Obrigado.",
        "geocoded": "Entendido. Fixamos seu relato pelo lugar indicado. Estamos coordenando ajuda.",
        "photo": "Foto recebida. Ajuda os coordenadores a entender a situação.",
        "photo_loc": " Toque + > Localização se puder compartilhar o ponto exato.",
        "rate_limit": "Temos seus relatos. Os coordenadores foram avisados. Contactamos se precisarmos de mais.",
    },
    "fr": {
        "loc_ok": "Merci. Nous avons votre signalement et la position. Les coordinateurs voient. Prenez soin de vous.",
        "ask_loc": "Merci. Appuyez sur + > Position > envoyez votre position actuelle pour nous aider.",
        "ask_loc_smart": "Vous semblez près de {place}. C'est bien ? Appuyez sur + > Position pour le lieu exact.",
        "logged_no_coords": "Signalement enregistré. Envoyez votre position quand vous pouvez : + > Position.",
        "update": "Mise à jour reçue. Merci.",
        "geocoded": "Bien noté. Nous avons placé votre signalement sur le lieu indiqué. Aide en coordination.",
        "photo": "Photo reçue. Aide les coordinateurs à comprendre la situation.",
        "photo_loc": " Appuyez sur + > Position si vous pouvez partager le lieu exact.",
        "rate_limit": "Nous avons vos signalements. Les coordinateurs sont informés. Nous revenons vers vous si besoin.",
    },
}


def _twilio_auth() -> tuple[str, str]:
    return (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def _normalize_from(from_val: str) -> str:
    return re.sub(r"\s+", "", (from_val or "").strip().lower())


def _anonymize_phone(from_val: str) -> str:
    digits = re.sub(r"\D", "", from_val or "")
    if len(digits) >= 4:
        return digits[-4:]
    return "****"


def _parse_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reset_if_stale(key: str) -> None:
    sess = _sessions.get(key)
    if not sess:
        return
    if time.time() - float(sess.get("last_activity", 0)) > SESSION_TTL_SEC:
        _sessions[key] = {"state": "NEW", "last_activity": time.time()}


def _get_session(key: str) -> dict[str, Any]:
    _reset_if_stale(key)
    if key not in _sessions:
        _sessions[key] = {"state": "NEW", "last_activity": time.time()}
    sess = _sessions[key]
    sess["last_activity"] = time.time()
    return sess


def _twiml(text: str) -> Response:
    resp = MessagingResponse()
    resp.message(text[:1600])
    return Response(str(resp), mimetype="text/xml; charset=utf-8")


def _download_media(url: str) -> str | None:
    if not url or not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return None
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(url, auth=_twilio_auth(), timeout=90)
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if "png" in ctype:
            ext = ".png"
        elif "gif" in ctype:
            ext = ".gif"
        elif "webp" in ctype:
            ext = ".webp"
        else:
            ext = ".jpg"
        name = f"{uuid.uuid4().hex}{ext}"
        path = UPLOAD_DIR / name
        path.write_bytes(r.content)
        return str(path.relative_to(_BASE))
    except (OSError, requests.RequestException):
        return None


def _extract_tags(text: str) -> list[str]:
    t = text.lower()
    tags: list[str] = []
    for tag, phrases in TAG_RULES:
        matched = False
        for p in phrases:
            if " " in p:
                if p in t:
                    matched = True
                    break
            elif re.search(rf"\b{re.escape(p)}\b", t):
                matched = True
                break
        if matched:
            tags.append(tag)
    return tags


def _compute_urgency(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(trapped|children|elderly|injured)\b", t):
        return "critical"
    if re.search(r"\bblocked\b", t) or "no services" in t or re.search(r"\brising\b", t):
        return "high"
    return "normal"


def _detect_lang(text: str) -> str:
    t = text.lower()
    for code, markers in _LANG_MARKERS:
        for m in markers:
            if " " in m:
                if m in t:
                    return code
            elif re.search(rf"\b{re.escape(m)}\b", t):
                return code
    return "en"


def _txt(lang: str, key: str, **fmt: Any) -> str:
    block = _COPY.get(lang) or _COPY["en"]
    s = block.get(key) or _COPY["en"][key]
    return s.format(**fmt) if fmt else s


def _clip160(s: str) -> str:
    s = s.strip()
    return s if len(s) <= 160 else s[:157] + "..."


def _geocode_nominatim(text: str) -> tuple[float | None, float | None, str | None]:
    q = text.strip()
    if not q or len(q) > 280:
        return None, None, None
    headers = {"User-Agent": NOMINATIM_UA}
    try:
        rr = requests.get(
            NOMINATIM_URL,
            params={"q": q, "format": "json", "limit": 1},
            headers=headers,
            timeout=20,
        )
        rr.raise_for_status()
        data = rr.json()
        if not isinstance(data, list) or not data:
            return None, None, None
        first = data[0]
        lat, lon = float(first["lat"]), float(first["lon"])
        disp = str(first.get("display_name") or "").strip()
        if len(disp) > 52:
            disp = disp[:49] + "..."
        return lat, lon, disp or None
    except (OSError, ValueError, TypeError, KeyError, requests.RequestException):
        return None, None, None


def _should_try_place_hint(body: str) -> bool:
    b = body.strip()
    if len(b) < 4:
        return False
    if re.search(r"\d", b):
        return True
    if re.search(r"\b(st|street|rd|road|ave|avenue|blvd|route|hwy|drive|ln|lane)\b", b, re.I):
        return True
    return len(b) >= 10


def _compose_reply(
    *,
    lang: str,
    main_key: str,
    main_fmt: dict[str, Any] | None = None,
    has_image: bool,
    location_missing: bool,
) -> str:
    code = lang if lang in _COPY else "en"
    main = _txt(code, main_key, **(main_fmt or {}))
    segs: list[str] = []
    if has_image:
        segs.append(_txt(code, "photo"))
    segs.append(main)
    asks_location = main_key in ("ask_loc", "ask_loc_smart", "logged_no_coords")
    if has_image and location_missing and not asks_location:
        segs.append(_txt(code, "photo_loc").strip())
    return _clip160(" ".join(s for s in segs if s).strip())


def _rate_record_and_over_limit(key: str) -> bool:
    now = time.time()
    lst = _msg_times.setdefault(key, [])
    lst[:] = [t for t in lst if now - t < RATE_WINDOW_SEC]
    lst.append(now)
    return len(lst) > RATE_MAX_MSGS


def _post_report(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        r = requests.post(REPORTS_URL, json=payload, timeout=20)
        if not r.ok:
            return None
        return r.json()
    except requests.RequestException:
        return None


def _get_report(rid: Any) -> dict[str, Any] | None:
    try:
        r = requests.get(f"{REPORTS_URL}/{rid}", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except requests.RequestException:
        return None


def _build_report_payload(
    *,
    phone_anon: str,
    lat: float | None,
    lng: float | None,
    description: str,
    image_path: str | None,
    confidence: str,
    report_id: Any | None = None,
    tags: list[str] | None = None,
    urgency: str | None = None,
) -> dict[str, Any]:
    desc = description.strip()
    tg = tags if tags is not None else _extract_tags(desc)
    urg = urgency if urgency is not None else _compute_urgency(desc)
    if urg not in ("critical", "high", "normal"):
        urg = "normal"
    payload: dict[str, Any] = {
        "phone": phone_anon,
        "lat": lat,
        "lng": lng,
        "description": desc,
        "image_url": image_path or "",
        "timestamp": _iso_now(),
        "confidence": confidence,
        "status": "new",
        "tags": tg,
        "urgency": urg,
    }
    if report_id is not None:
        payload["id"] = report_id
    return payload


def _merge_report_update(
    existing: dict[str, Any],
    *,
    new_description: str,
    new_image: str | None,
    new_lat: float | None,
    new_lng: float | None,
) -> dict[str, Any]:
    desc_parts: list[str] = []
    old = (existing.get("description") or "").strip()
    if old:
        desc_parts.append(old)
    if new_description.strip():
        desc_parts.append(new_description.strip())
    merged_desc = "\n".join(desc_parts)

    lat = existing.get("lat")
    lng = existing.get("lng")
    if new_lat is not None and new_lng is not None:
        lat, lng = new_lat, new_lng

    img = existing.get("image_url") or ""
    if new_image:
        img = new_image

    tags = _extract_tags(merged_desc)
    urgency = _compute_urgency(merged_desc)

    return {
        "id": existing.get("id"),
        "phone": existing.get("phone", ""),
        "lat": float(lat) if lat is not None else None,
        "lng": float(lng) if lng is not None else None,
        "description": merged_desc,
        "image_url": img or "",
        "timestamp": _iso_now(),
        "status": existing.get("status", "new"),
        "confidence": existing.get("confidence", "unverified"),
        "tags": tags,
        "urgency": urgency,
    }


def _handle_rate_limited(
    *,
    key: str,
    phone_anon: str,
    body: str,
    lat: float | None,
    lng: float | None,
    image_rel: str | None,
    lang: str,
) -> str:
    sess = _get_session(key)
    has_gps = lat is not None and lng is not None
    upd = body.strip()
    if not upd and image_rel:
        upd = "(photo attached)"

    rid = sess.get("report_id")
    if rid is not None:
        cur = _get_report(rid)
        if cur:
            merged = _merge_report_update(
                cur,
                new_description=upd,
                new_image=image_rel,
                new_lat=lat if has_gps else None,
                new_lng=lng if has_gps else None,
            )
            if _post_report(merged):
                return _clip160(_txt(lang, "rate_limit"))
    rep = _post_report(
        _build_report_payload(
            phone_anon=phone_anon,
            lat=lat if has_gps else None,
            lng=lng if has_gps else None,
            description=upd or "(follow-up)",
            image_path=image_rel,
            confidence="corroborated" if has_gps else "unverified",
        )
    )
    if rep:
        sess["report_id"] = rep.get("id")
        sess["state"] = "DONE"
    return _clip160(_txt(lang, "rate_limit"))


def _handle_inbound(
    *,
    key: str,
    phone_anon: str,
    body: str,
    lat: float | None,
    lng: float | None,
    image_rel: str | None,
    lang: str,
) -> str:
    sess = _get_session(key)
    state = sess["state"]
    has_gps = lat is not None and lng is not None

    desc_bits: list[str] = []
    if body:
        desc_bits.append(body)
    if image_rel and not body:
        desc_bits.append("(photo attached)")
    description = "\n".join(desc_bits).strip()

    if state == "DONE":
        rid = sess.get("report_id")
        if rid is None:
            sess["state"] = "NEW"
            return _handle_inbound(
                key=key,
                phone_anon=phone_anon,
                body=body,
                lat=lat,
                lng=lng,
                image_rel=image_rel,
                lang=lang,
            )
        cur = _get_report(rid)
        if not cur:
            sess["state"] = "NEW"
            return _handle_inbound(
                key=key,
                phone_anon=phone_anon,
                body=body,
                lat=lat,
                lng=lng,
                image_rel=image_rel,
                lang=lang,
            )
        upd_text = body
        if not upd_text.strip() and image_rel:
            upd_text = "(photo attached)"
        merged = _merge_report_update(
            cur,
            new_description=upd_text,
            new_image=image_rel,
            new_lat=lat if has_gps else None,
            new_lng=lng if has_gps else None,
        )
        out = _post_report(merged)
        if out is None:
            return _clip160("We could not save that just now. Please try again in a moment.")
        if image_rel:
            return _compose_reply(lang=lang, main_key="update", has_image=True, location_missing=False)
        return _clip160(_txt(lang, "update"))

    if state == "NEW":
        if has_gps:
            rep = _post_report(
                _build_report_payload(
                    phone_anon=phone_anon,
                    lat=lat,
                    lng=lng,
                    description=description,
                    image_path=image_rel,
                    confidence="corroborated",
                )
            )
            if rep is None:
                return _clip160("We could not save your report. Please try again shortly.")
            sess["state"] = "DONE"
            sess["report_id"] = rep.get("id")
            return _compose_reply(
                lang=lang,
                main_key="loc_ok",
                has_image=bool(image_rel),
                location_missing=False,
            )
        sess["state"] = "AWAITING_LOCATION"
        if _should_try_place_hint(body):
            _lat, _lng, label = _geocode_nominatim(body)
            if label:
                return _compose_reply(
                    lang=lang,
                    main_key="ask_loc_smart",
                    main_fmt={"place": label},
                    has_image=bool(image_rel),
                    location_missing=True,
                )
        return _compose_reply(
            lang=lang,
            main_key="ask_loc",
            has_image=bool(image_rel),
            location_missing=True,
        )

    # AWAITING_LOCATION
    if has_gps:
        rep = _post_report(
            _build_report_payload(
                phone_anon=phone_anon,
                lat=lat,
                lng=lng,
                description=description,
                image_path=image_rel,
                confidence="corroborated",
            )
        )
        if rep is None:
            return _clip160("We could not save your report. Please try again shortly.")
        sess["state"] = "DONE"
        sess["report_id"] = rep.get("id")
        return _compose_reply(
            lang=lang,
            main_key="loc_ok",
            has_image=bool(image_rel),
            location_missing=False,
        )

    glat, glng, _glabel = _geocode_nominatim(body)
    if glat is not None and glng is not None:
        rep = _post_report(
            _build_report_payload(
                phone_anon=phone_anon,
                lat=glat,
                lng=glng,
                description=description,
                image_path=image_rel,
                confidence="unverified",
            )
        )
        if rep is None:
            return _clip160("We could not save your report. Please try again shortly.")
        sess["state"] = "DONE"
        sess["report_id"] = rep.get("id")
        return _compose_reply(
            lang=lang,
            main_key="geocoded",
            has_image=bool(image_rel),
            location_missing=False,
        )

    rep = _post_report(
        _build_report_payload(
            phone_anon=phone_anon,
            lat=None,
            lng=None,
            description=description,
            image_path=image_rel,
            confidence="unverified",
        )
    )
    if rep is None:
        return _clip160("We could not save your report. Please try again shortly.")
    sess["state"] = "DONE"
    sess["report_id"] = rep.get("id")
    return _compose_reply(
        lang=lang,
        main_key="logged_no_coords",
        has_image=bool(image_rel),
        location_missing=True,
    )


@bot_bp.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    from_val = request.form.get("From") or ""
    body_raw = request.form.get("Body") or ""
    body = body_raw.strip()
    try:
        num_media = int(request.form.get("NumMedia") or "0")
    except ValueError:
        num_media = 0
    media_url = request.form.get("MediaUrl0") if num_media > 0 else None
    lat = _parse_float(request.form.get("Latitude"))
    lng = _parse_float(request.form.get("Longitude"))

    key = _normalize_from(from_val)
    phone_anon = _anonymize_phone(from_val)
    lang = _detect_lang(body)

    image_rel = _download_media(media_url) if media_url else None

    if _rate_record_and_over_limit(key):
        reply = _handle_rate_limited(
            key=key,
            phone_anon=phone_anon,
            body=body,
            lat=lat,
            lng=lng,
            image_rel=image_rel,
            lang=lang,
        )
    else:
        reply = _handle_inbound(
            key=key,
            phone_anon=phone_anon,
            body=body,
            lat=lat,
            lng=lng,
            image_rel=image_rel,
            lang=lang,
        )
    return _twiml(reply)

