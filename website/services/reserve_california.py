"""
Lightweight ReserveCalifornia client.

Talks directly to the UseDirect / Tyler Technologies API that powers
reservecalifornia.com.  Zero new dependencies — uses only ``requests``
(already installed) and stdlib modules.
"""

import logging
import math
import time
import json
import re
from datetime import datetime, timedelta
from threading import Lock

import requests

logger = logging.getLogger(__name__)

# ── API constants ──────────────────────────────────────────────────────
RC_BASE_URL = (
    "https://california-rdr.prod.cali.rd12.recreation-management.tylerapp.com"
)
RC_PLACES_URL = f"{RC_BASE_URL}/rdr/fd/places"
RC_FACILITIES_URL = f"{RC_BASE_URL}/rdr/fd/facilities"
RC_AVAILABILITY_URL = f"{RC_BASE_URL}/rdr/search/grid"
RC_DATE_FMT = "%m-%d-%Y"

RC_CAMPGROUND_URL = "https://www.reservecalifornia.com"
RC_BOOKING_PATH = "Web/Default.aspx#!park/{place_id}/{facility_id}"

# ── In-memory metadata cache (places + facilities) ────────────────────
_cache = {
    "places": None,       # list[dict]
    "facilities": None,   # list[dict]
    "ts": 0,              # epoch seconds when last fetched
}
_cache_lock = Lock()
_CACHE_TTL = 3600  # 1 hour

# ── Rate-limit: 1 req/s for availability calls ────────────────────────
_last_request_time = 0
_rate_lock = Lock()


def _rate_limit():
    """Sleep if less than 1 s since the last availability request."""
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_request_time = time.time()


def _headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ── Helpers ────────────────────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2):
    """Return distance in miles between two lat/lng points."""
    R = 3958.8  # Earth radius in miles
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_provider_id(prefixed_id):
    """Parse a provider-prefixed ID like ``rc:718`` or ``rg:232447``.

    Returns (provider, raw_id).  Unprefixed IDs default to ``rg``.
    """
    prefixed_id = str(prefixed_id).strip()
    if prefixed_id.startswith("rc:"):
        return ("rc", prefixed_id[3:])
    if prefixed_id.startswith("rg:"):
        return ("rg", prefixed_id[3:])
    return ("rg", prefixed_id)


def split_ids_by_provider(comma_separated):
    """Split ``'rg:232447,rc:718,rc:720'`` → ``{'rg': ['232447'], 'rc': ['718','720']}``."""
    result = {"rg": [], "rc": []}
    for raw in str(comma_separated).split(","):
        raw = raw.strip()
        if not raw:
            continue
        provider, fid = parse_provider_id(raw)
        result.setdefault(provider, []).append(fid)
    return result


# ── Metadata (places & facilities) ────────────────────────────────────

def _fetch_rc_metadata(force=False):
    """Fetch and cache all CA state-park places and facilities.

    Returns ``(places_list, facilities_list)`` where each item is the
    raw JSON list from the API.
    """
    with _cache_lock:
        if not force and _cache["places"] and (time.time() - _cache["ts"] < _CACHE_TTL):
            return _cache["places"], _cache["facilities"]

    logger.info("Refreshing ReserveCalifornia metadata …")
    try:
        r_places = requests.get(RC_PLACES_URL, headers=_headers(), timeout=15)
        r_places.raise_for_status()
        places = r_places.json()  # list of place dicts

        r_fac = requests.get(RC_FACILITIES_URL, headers=_headers(), timeout=15)
        r_fac.raise_for_status()
        facilities = r_fac.json()  # list of facility dicts
    except Exception:
        logger.exception("Failed to fetch RC metadata")
        return _cache["places"] or [], _cache["facilities"] or []

    with _cache_lock:
        _cache["places"] = places
        _cache["facilities"] = facilities
        _cache["ts"] = time.time()

    logger.info(f"RC metadata: {len(places)} places, {len(facilities)} facilities")
    return places, facilities


# ── Discovery ─────────────────────────────────────────────────────────

def discover_rc_campgrounds(lat, lng, radius_miles=100):
    """Return CA state-park campgrounds within *radius_miles* of (lat, lng).

    Each dict matches the ``/search_campsites`` response shape used by
    the frontend (keys: id, name, latitude, longitude, description,
    type, provider).
    """
    places, facilities = _fetch_rc_metadata()

    # Build place lookup  {PlaceId: place_dict}
    place_map = {}
    for p in places:
        pid = p.get("PlaceId")
        plat = p.get("Latitude")
        plng = p.get("Longitude")
        if pid is None or plat is None or plng is None:
            continue
        place_map[pid] = p

    results = []
    for fac in facilities:
        # Skip facilities that aren't bookable online
        if not fac.get("AllowWebBooking", False):
            continue

        place_id = fac.get("PlaceId")
        place = place_map.get(place_id)
        if not place:
            continue

        plat = place.get("Latitude", 0)
        plng = place.get("Longitude", 0)
        if plat == 0 and plng == 0:
            continue

        dist = _haversine(lat, lng, plat, plng)
        if dist > radius_miles:
            continue

        fac_id = fac["FacilityId"]
        place_id_val = place.get("PlaceId", "")
        results.append({
            "id": f"rc:{fac_id}",
            "name": fac.get("Name", "Unknown"),
            "latitude": plat,
            "longitude": plng,
            "description": place.get("Description", ""),
            "type": "Campground",
            "provider": "ReserveCalifornia",
            "image_url": "",
            "booking_url": f"https://www.reservecalifornia.com/Web/Default.aspx#!park/{place_id_val}/{fac_id}",
        })

    logger.info(f"RC discovery: {len(results)} campgrounds within {radius_miles} mi of ({lat},{lng})")
    return results


def search_rc_campgrounds_by_name(query):
    """Search RC campground/place names for *query*.

    Returns list of dicts in the same shape as ``discover_rc_campgrounds``.
    """
    places, facilities = _fetch_rc_metadata()
    q = query.lower()

    place_map = {p["PlaceId"]: p for p in places if "PlaceId" in p}

    results = []
    for fac in facilities:
        # Skip facilities that aren't bookable online
        if not fac.get("AllowWebBooking", False):
            continue

        name = (fac.get("Name") or "").lower()
        place = place_map.get(fac.get("PlaceId"))
        place_name = (place.get("Name") or "").lower() if place else ""

        if q in name or q in place_name:
            plat = place.get("Latitude", 0) if place else 0
            plng = place.get("Longitude", 0) if place else 0
            fac_id = fac["FacilityId"]
            place_id_val = (place.get("PlaceId", "") if place else "")
            results.append({
                "id": f"rc:{fac_id}",
                "name": fac.get("Name", "Unknown"),
                "latitude": plat,
                "longitude": plng,
                "description": (place.get("Description") or "") if place else "",
                "type": "Campground",
                "provider": "ReserveCalifornia",
                "image_url": "",
                "booking_url": f"https://www.reservecalifornia.com/Web/Default.aspx#!park/{place_id_val}/{fac_id}",
            })

    return results


# ── Availability ──────────────────────────────────────────────────────

def _fetch_availability(facility_id, start_date, end_date):
    """Hit the /rdr/search/grid endpoint for one facility.

    Returns the raw JSON dict or ``None`` on failure.
    """
    _rate_limit()
    body = {
        "FacilityId": int(facility_id),
        "StartDate": start_date.strftime(RC_DATE_FMT),
        "EndDate": end_date.strftime(RC_DATE_FMT),
        "InSeasonOnly": True,
        "WebOnly": True,
        "UnitSort": "orderby",
    }
    try:
        resp = requests.post(
            RC_AVAILABILITY_URL,
            headers=_headers(),
            data=json.dumps(body),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception(f"RC availability request failed for facility {facility_id}")
        return None


def _format_date_range(start_str, end_str):
    """``'2025-08-15'``, ``'2025-08-17'`` → ``'2025-08-15 (Fri) -> 2025-08-17 (Sun)'``."""
    s = datetime.strptime(start_str, "%Y-%m-%d")
    e = datetime.strptime(end_str, "%Y-%m-%d")
    return f"{start_str} ({s.strftime('%a')}) -> {end_str} ({e.strftime('%a')})"


def _classify_range(start_date, end_date, min_nights):
    """Port of ``camping_wrapper.filter_by_days`` logic.

    Returns ``'priority'``, ``'regular'``, or ``'ignored'``.
    """
    num_nights = (end_date - start_date).days
    if num_nights < min_nights:
        return None

    if min_nights == 1:
        if start_date.weekday() in (4, 5):
            return "priority"
        if start_date.weekday() in (3, 6):
            return "regular"
        return "ignored"

    if min_nights == 2:
        if start_date.weekday() == 4 and (start_date + timedelta(days=1)).weekday() == 5:
            return "priority"
        if start_date.weekday() in (3, 4, 5, 6) and end_date.weekday() in (5, 6, 0):
            return "regular"
        return "ignored"

    if min_nights == 3:
        if start_date.weekday() in (3, 4):
            return "priority"
        return "ignored"

    if min_nights == 4:
        if start_date.weekday() == 3:
            return "priority"
        return "ignored"

    # 5+ nights — everything is priority
    return "priority"


def search_rc_availability(facility_ids, start_date_str, end_date_str, nights):
    """Search availability for a list of RC facility IDs.

    Parameters match the ``camping_wrapper.py --json-output`` interface:
    - *facility_ids*: list of string facility IDs (without ``rc:`` prefix)
    - *start_date_str* / *end_date_str*: ``'YYYY-MM-DD'``
    - *nights*: int — minimum consecutive nights

    Returns a dict keyed by ``"FacilityName (rc:id)"`` containing
    ``{"priority": {…}, "regular": {…}, "ignored": {…}}`` in the same
    format as ``camping_wrapper.build_json_output()``.
    """
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    nights = int(nights)

    merged = {}

    for fid in facility_ids:
        data = _fetch_availability(fid, start_dt, end_dt)
        if not data or not data.get("Facility"):
            continue

        facility = data["Facility"]
        fac_name = facility.get("Name") or f"Facility {fid}"
        park_key = f"{fac_name} (rc:{fid})"

        # Gather per-unit, per-date free booleans
        units = facility.get("Units") or {}
        if not units:
            logger.info(f"RC facility {fid} ({fac_name}): 0 bookable units — skipping")
            continue
        # date_str → count of free units
        free_by_date = {}

        for _uid, unit in units.items():
            slices = unit.get("Slices") or {}
            for slice_key, sl in slices.items():
                if not sl.get("IsFree"):
                    continue
                # slice_key might be ISO datetime; normalise to YYYY-MM-DD
                day_str = str(slice_key)[:10]
                free_by_date[day_str] = free_by_date.get(day_str, 0) + 1

        # Find consecutive-night windows (same logic as camping.py)
        sorted_dates = sorted(free_by_date.keys())
        date_set = set(sorted_dates)
        seen_windows = set()

        park_priority = {}
        park_regular = {}
        park_ignored = {}

        for day_str in sorted_dates:
            day = datetime.strptime(day_str, "%Y-%m-%d")
            # Check if `nights` consecutive days starting from `day` are all free
            window_ok = True
            min_count = free_by_date.get(day_str, 0)
            for n in range(1, nights):
                next_day = (day + timedelta(days=n)).strftime("%Y-%m-%d")
                if next_day not in date_set:
                    window_ok = False
                    break
                min_count = min(min_count, free_by_date.get(next_day, 0))

            if not window_ok:
                continue

            checkout = day + timedelta(days=nights)
            checkout_str = checkout.strftime("%Y-%m-%d")
            window_key = (day_str, checkout_str)
            if window_key in seen_windows:
                continue
            seen_windows.add(window_key)

            category = _classify_range(day, checkout, nights)
            if category is None:
                continue

            range_key = _format_date_range(day_str, checkout_str)
            bucket = {"priority": park_priority, "regular": park_regular, "ignored": park_ignored}[category]
            bucket[range_key] = bucket.get(range_key, 0) + min_count

        # Only include if there's at least one available window
        if not park_priority and not park_regular and not park_ignored:
            logger.info(f"RC facility {fid} ({fac_name}): no available windows — omitting")
            continue

        merged[park_key] = {
            "priority": park_priority,
            "regular": park_regular,
            "ignored": park_ignored,
        }

    return merged
