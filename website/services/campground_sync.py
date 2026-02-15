"""
Campground data sync service.

Fetches detailed campground data from RIDB (Recreation.gov) and
ReserveCalifornia APIs, parses it, and upserts into the campgrounds table.
"""

import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser

import requests

logger = logging.getLogger(__name__)

RECREATION_API_KEY = os.environ.get('RECREATION_API_KEY', '')
RIDB_BASE = 'https://ridb.recreation.gov/api/v1'

# Rate limiting: max 2 req/sec to RIDB
_last_ridb_request = 0


def _ridb_rate_limit():
    global _last_ridb_request
    now = time.time()
    elapsed = now - _last_ridb_request
    if elapsed < 0.5:
        time.sleep(0.5 - elapsed)
    _last_ridb_request = time.time()


def _ridb_get(path, params=None):
    """Make a GET request to the RIDB API."""
    _ridb_rate_limit()
    params = params or {}
    params['apikey'] = RECREATION_API_KEY
    url = f'{RIDB_BASE}{path}'
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"RIDB {path} returned {resp.status_code}")
    except Exception as e:
        logger.error(f"RIDB request error for {path}: {e}")
    return None


# ── HTML description parser ──────────────────────────────────────────

class _DescriptionSectionParser(HTMLParser):
    """Split RIDB FacilityDescription HTML into sections by <h2> tags."""

    SECTION_MAP = {
        'overview': 'overview',
        'recreation': 'recreation',
        'facilities': 'facilities',
        'natural features': 'natural_features',
        'nearby attractions': 'nearby',
        'contact info': 'overview',  # merge into overview
        'charges & cancellations': 'rules',
        'charges and cancellations': 'rules',
        'rules': 'rules',
    }

    def __init__(self):
        super().__init__()
        self.sections = {}
        self._current_section = 'overview'
        self._current_content = []
        self._in_h2 = False
        self._h2_text = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'h2':
            # Save previous section
            self._save_current()
            self._in_h2 = True
            self._h2_text = ''
        elif not self._in_h2:
            # Reconstruct HTML
            attr_str = ''.join(f' {k}="{v}"' for k, v in attrs)
            self._current_content.append(f'<{tag}{attr_str}>')

    def handle_endtag(self, tag):
        if tag == 'h2':
            self._in_h2 = False
            heading = self._h2_text.strip().lower()
            self._current_section = self.SECTION_MAP.get(heading, 'overview')
            self._current_content = []
        elif not self._in_h2:
            self._current_content.append(f'</{tag}>')

    def handle_data(self, data):
        if self._in_h2:
            self._h2_text += data
        else:
            self._current_content.append(data)

    def _save_current(self):
        content = ''.join(self._current_content).strip()
        if content:
            if self._current_section in self.sections:
                self.sections[self._current_section] += '\n' + content
            else:
                self.sections[self._current_section] = content
        self._current_content = []

    def get_sections(self):
        self._save_current()
        return self.sections


def parse_facility_description(html):
    """Parse RIDB FacilityDescription HTML into named sections."""
    if not html:
        return {}
    parser = _DescriptionSectionParser()
    parser.feed(html)
    return parser.get_sections()


# ── Campsite attribute aggregation ───────────────────────────────────

def _aggregate_campsites(campsites):
    """Aggregate per-campsite data into campground-level summary."""
    if not campsites:
        return {}

    total = len(campsites)
    type_counter = Counter()
    loops = set()
    checkin_times = Counter()
    checkout_times = Counter()
    max_vehicle_len = 0
    pets_votes = Counter()
    fire_votes = Counter()
    shade_votes = Counter()
    max_people = 0
    max_vehicles = 0
    equipment_map = {}  # name -> max length
    driveway_surfaces = Counter()
    all_photos = []

    for site in campsites:
        # Type
        site_type = site.get('CampsiteType', 'Unknown')
        # Normalize type names
        if 'STANDARD' in site_type.upper():
            type_counter['Standard'] += 1
        elif 'RV' in site_type.upper():
            type_counter['RV'] += 1
        elif 'GROUP' in site_type.upper():
            type_counter['Group'] += 1
        elif 'TENT' in site_type.upper():
            type_counter['Tent'] += 1
        elif 'EQUESTRIAN' in site_type.upper():
            type_counter['Equestrian'] += 1
        elif 'MANAGEMENT' in site_type.upper():
            continue  # Skip management sites from count
        else:
            type_counter[site_type.title()] += 1

        # Loop
        loop = site.get('Loop')
        if loop:
            loops.add(loop)

        # Attributes
        for attr in site.get('ATTRIBUTES', []):
            name = attr.get('AttributeName', '')
            value = attr.get('AttributeValue', '')

            if name == 'Checkin Time' and value:
                checkin_times[value] += 1
            elif name == 'Checkout Time' and value:
                checkout_times[value] += 1
            elif name == 'Max Vehicle Length' and value:
                try:
                    max_vehicle_len = max(max_vehicle_len, int(value))
                except (ValueError, TypeError):
                    pass
            elif name == 'Pets Allowed' and value:
                pets_votes[value.lower().startswith('y')] += 1
            elif name == 'Campfire Allowed' and value:
                fire_votes[value.lower().startswith('y')] += 1
            elif name == 'Shade' and value:
                shade_votes[value.lower().startswith('y')] += 1
            elif name == 'Max Num of People' and value:
                try:
                    max_people = max(max_people, int(value))
                except (ValueError, TypeError):
                    pass
            elif name == 'Max Num of Vehicles' and value:
                try:
                    max_vehicles = max(max_vehicles, int(value))
                except (ValueError, TypeError):
                    pass
            elif name == 'Driveway Surface' and value:
                driveway_surfaces[value] += 1

        # Equipment
        for eq in site.get('PERMITTEDEQUIPMENT', []):
            eq_name = eq.get('EquipmentName', '')
            eq_len = eq.get('MaxLength', 0)
            if eq_name:
                try:
                    eq_len = int(eq_len)
                except (ValueError, TypeError):
                    eq_len = 0
                if eq_name not in equipment_map or eq_len > equipment_map[eq_name]:
                    equipment_map[eq_name] = eq_len

        # Per-site photos
        for media in site.get('ENTITYMEDIA', []):
            if media.get('MediaType') == 'Image' and media.get('URL'):
                all_photos.append({
                    'url': media['URL'],
                    'title': media.get('Title', ''),
                    'credits': media.get('Credits', ''),
                    'isPrimary': False,
                    'isGallery': True,
                })

    result = {
        'total_sites': total,
        'site_types': dict(type_counter),
        'loops': sorted(loops) if loops else None,
        'max_vehicle_length': max_vehicle_len or None,
        'max_people_per_site': max_people or None,
        'max_vehicles_per_site': max_vehicles or None,
        'site_photos': all_photos,
    }

    if checkin_times:
        result['checkin_time'] = checkin_times.most_common(1)[0][0]
    if checkout_times:
        result['checkout_time'] = checkout_times.most_common(1)[0][0]
    if pets_votes:
        result['pets_allowed'] = pets_votes.get(True, 0) > 0
    if fire_votes:
        result['campfires_allowed'] = fire_votes.get(True, 0) > 0
    if shade_votes:
        result['shade_available'] = shade_votes.get(True, 0) > shade_votes.get(False, 0)
    if driveway_surfaces:
        result['driveway_surface'] = driveway_surfaces.most_common(1)[0][0]

    if equipment_map:
        result['permitted_equipment'] = [
            {'name': k, 'maxLength': v} for k, v in sorted(equipment_map.items())
        ]

    return result


# ── Fetch all campsites for a facility ───────────────────────────────

def _fetch_all_campsites(facility_id):
    """Paginate through all campsites for an RIDB facility."""
    all_sites = []
    page_size = 50
    max_pages = 20  # safety cap (1000 sites)

    for page in range(max_pages):
        offset = page * page_size
        data = _ridb_get(
            f'/facilities/{facility_id}/campsites',
            params={'limit': page_size, 'offset': offset}
        )
        if not data:
            break
        batch = data.get('RECDATA', [])
        all_sites.extend(batch)
        if len(batch) < page_size:
            break

    return all_sites


# ── Sync a single Recreation.gov campground ──────────────────────────

def _sync_rg_campground(external_id, db, Campground):
    """Fetch and upsert a single Recreation.gov campground."""
    # Fetch facility details
    data = _ridb_get(f'/facilities/{external_id}', params={'full': 'true'})
    if not data:
        logger.error(f"Could not fetch RIDB facility {external_id}")
        return None

    # RIDB returns a list for full=true, or a single dict
    facility = data
    if isinstance(data, list):
        facility = data[0] if data else None
    elif isinstance(data, dict) and 'RECDATA' in data:
        recs = data['RECDATA']
        facility = recs[0] if recs else data

    if not facility or not facility.get('FacilityName'):
        logger.error(f"Invalid facility data for {external_id}")
        return None

    # Parse description sections
    desc_html = facility.get('FacilityDescription', '')
    sections = parse_facility_description(desc_html)

    # Collect facility-level photos
    photos = []
    for m in (facility.get('MEDIA') or []):
        if m.get('MediaType') == 'Image' and m.get('URL'):
            photos.append({
                'url': m['URL'],
                'title': m.get('Title', ''),
                'credits': m.get('Credits', ''),
                'isPrimary': bool(m.get('IsPrimary')),
                'isPreview': bool(m.get('IsPreview')),
                'isGallery': bool(m.get('IsGallery')),
                'width': m.get('Width'),
                'height': m.get('Height'),
            })

    # Fetch and aggregate campsites
    campsites = _fetch_all_campsites(external_id)
    agg = _aggregate_campsites(campsites)

    # Merge site photos (deduped by URL)
    seen_urls = {p['url'] for p in photos}
    for sp in agg.get('site_photos', []):
        if sp['url'] not in seen_urls:
            photos.append(sp)
            seen_urls.add(sp['url'])

    # Address fields
    addresses = facility.get('FACILITYADDRESS') or []
    addr_info = addresses[0] if addresses else {}

    # Upsert
    cg = Campground.query.filter_by(provider='rg', external_id=str(external_id)).first()
    if not cg:
        cg = Campground(provider='rg', external_id=str(external_id))
        db.session.add(cg)

    cg.name = facility.get('FacilityName', '')
    cg.slug = cg.generate_slug()
    cg.latitude = facility.get('FacilityLatitude')
    cg.longitude = facility.get('FacilityLongitude')
    cg.address = addr_info.get('FacilityStreetAddress1', '')
    cg.city = addr_info.get('City', '')
    cg.state = addr_info.get('AddressStateCode', '')
    cg.zip_code = addr_info.get('PostalCode', '')

    cg.description_overview = sections.get('overview', '')
    cg.description_recreation = sections.get('recreation', '')
    cg.description_facilities = sections.get('facilities', '')
    cg.description_natural_features = sections.get('natural_features', '')
    cg.description_nearby = sections.get('nearby', '')
    cg.description_rules = sections.get('rules', '')
    cg.directions = facility.get('FacilityDirections', '')

    cg.phone = facility.get('FacilityPhone', '')
    cg.email = facility.get('FacilityEmail', '')

    cg.total_sites = agg.get('total_sites')
    cg.site_types = agg.get('site_types')
    cg.loops = agg.get('loops')
    cg.checkin_time = agg.get('checkin_time')
    cg.checkout_time = agg.get('checkout_time')
    cg.max_vehicle_length = agg.get('max_vehicle_length')
    cg.pets_allowed = agg.get('pets_allowed')
    cg.campfires_allowed = agg.get('campfires_allowed')
    cg.ada_access = facility.get('FacilityAdaAccess', '').upper() == 'Y'
    cg.reservable = bool(facility.get('Reservable'))
    cg.stay_limit = facility.get('StayLimit', '')
    cg.max_people_per_site = agg.get('max_people_per_site')
    cg.max_vehicles_per_site = agg.get('max_vehicles_per_site')
    cg.shade_available = agg.get('shade_available')
    cg.permitted_equipment = agg.get('permitted_equipment')
    cg.driveway_surface = agg.get('driveway_surface')

    cg.photos = photos
    cg.map_image_url = facility.get('FacilityMapURL', '')
    cg.booking_url = f"https://www.recreation.gov/camping/campgrounds/{external_id}"
    cg.facility_type = facility.get('FacilityTypeDescription', 'Campground')
    cg.keywords = facility.get('Keywords', '')

    cg.last_synced = datetime.utcnow()
    cg.sync_status = 'synced'

    db.session.commit()
    logger.info(f"Synced RG campground: {cg.name} ({external_id}), {cg.total_sites} sites, {len(photos)} photos")
    return cg


# ── Sync a single ReserveCalifornia campground ───────────────────────

def _sync_rc_campground(external_id, db, Campground):
    """Fetch and upsert a single ReserveCalifornia campground."""
    from website.services.reserve_california import _fetch_rc_metadata
    from website.services.ca_parks_scraper import find_park_page_id, scrape_park_page

    places, facilities = _fetch_rc_metadata()

    # Find the facility
    fac = None
    for f in facilities:
        if str(f.get('FacilityId')) == str(external_id):
            fac = f
            break

    if not fac:
        logger.error(f"RC facility {external_id} not found in metadata")
        return None

    # Find the parent place
    place_id = fac.get('PlaceId')
    place = None
    for p in places:
        if p.get('PlaceId') == place_id:
            place = p
            break

    cg = Campground.query.filter_by(provider='rc', external_id=str(external_id)).first()
    if not cg:
        cg = Campground(provider='rc', external_id=str(external_id))
        db.session.add(cg)

    cg.name = fac.get('Name', 'Unknown')
    cg.slug = cg.generate_slug()

    place_name = place.get('Name', '') if place else ''

    if place:
        cg.latitude = place.get('Latitude')
        cg.longitude = place.get('Longitude')
        cg.address = place.get('StreetAddress', '')
        cg.city = place.get('City', '')
        cg.state = place.get('State', 'CA')
        cg.zip_code = place.get('Zip', '')
        cg.phone = place.get('Phone', '')
        cg.description_overview = place.get('Description', '')

    cg.booking_url = "https://www.reservecalifornia.com"
    cg.facility_type = fac.get('FacilityType', 'Campground')
    cg.reservable = True

    # Enrich from parks.ca.gov
    page_id = find_park_page_id(place_name)
    if page_id:
        try:
            park_data = scrape_park_page(page_id)
            if park_data:
                # Description — only overwrite if richer than existing
                if park_data.get('description') and (
                    not cg.description_overview or len(park_data['description']) > len(cg.description_overview or '')
                ):
                    cg.description_overview = park_data['description']
                if park_data.get('amenities'):
                    cg.description_facilities = ', '.join(park_data['amenities'])
                if park_data.get('directions'):
                    cg.directions = park_data['directions']
                if park_data.get('photos') and not cg.photos:
                    cg.photos = park_data['photos']
                if park_data.get('phone') and not cg.phone:
                    cg.phone = park_data['phone']
                if park_data.get('email') and not cg.email:
                    cg.email = park_data['email']
                if park_data.get('pets'):
                    cg.description_rules = park_data['pets']
                if park_data.get('fees'):
                    # Append fees info to rules if present
                    fees_text = park_data['fees']
                    if cg.description_rules:
                        cg.description_rules += '\n\n' + fees_text
                    else:
                        cg.description_rules = fees_text

                logger.info(f"Enriched RC campground '{cg.name}' from parks.ca.gov page_id={page_id}")
        except Exception as e:
            logger.warning(f"Failed to enrich RC campground '{cg.name}' from parks.ca.gov: {e}")

    cg.last_synced = datetime.utcnow()
    cg.sync_status = 'synced'

    db.session.commit()
    logger.info(f"Synced RC campground: {cg.name} ({external_id})")
    return cg


# ── Public API ───────────────────────────────────────────────────────

def sync_one(provider, external_id):
    """Sync a single campground on-demand. Must be called within app context."""
    from website.models import db, Campground

    try:
        if provider == 'rg':
            return _sync_rg_campground(external_id, db, Campground)
        elif provider == 'rc':
            return _sync_rc_campground(external_id, db, Campground)
        else:
            logger.error(f"Unknown provider: {provider}")
            return None
    except Exception as e:
        logger.error(f"Error syncing {provider}:{external_id}: {e}")
        db.session.rollback()
        return None


def sync_all():
    """Full sync of all known campgrounds.

    Discovers RG campgrounds via a lat/lng grid covering western US,
    and all RC campgrounds from metadata.  Should be run within app context.
    """
    from website.models import db, Campground
    from website.services.reserve_california import _fetch_rc_metadata

    synced_count = 0
    error_count = 0

    # ── ReserveCalifornia: all facilities ──
    logger.info("Starting RC full sync...")
    try:
        places, facilities = _fetch_rc_metadata(force=True)
        for fac in facilities:
            if not fac.get('AllowWebBooking', False):
                continue
            fid = str(fac.get('FacilityId', ''))
            if not fid:
                continue
            try:
                _sync_rc_campground(fid, db, Campground)
                synced_count += 1
            except Exception as e:
                logger.error(f"Error syncing RC {fid}: {e}")
                db.session.rollback()
                error_count += 1
    except Exception as e:
        logger.error(f"RC full sync failed: {e}")

    # ── Recreation.gov: discover via grid ──
    logger.info("Starting RG discovery sync...")
    # Grid of major western US locations
    grid_points = [
        (37.77, -122.42),   # San Francisco
        (34.05, -118.24),   # Los Angeles
        (32.72, -117.16),   # San Diego
        (38.58, -121.49),   # Sacramento
        (36.75, -119.77),   # Fresno
        (37.87, -119.54),   # Yosemite
        (36.57, -118.77),   # Sequoia
        (41.31, -122.31),   # Mt Shasta
        (39.09, -120.03),   # Lake Tahoe
        (45.52, -122.68),   # Portland
        (47.61, -122.33),   # Seattle
        (36.17, -115.14),   # Las Vegas
        (33.45, -112.07),   # Phoenix
        (39.53, -119.81),   # Reno
        (44.06, -121.31),   # Bend
    ]

    for lat, lng in grid_points:
        logger.info(f"RG grid search at ({lat}, {lng})...")
        try:
            params = {
                'latitude': lat,
                'longitude': lng,
                'radius': 100,
                'activity': 'CAMPING',
                'limit': 50,
                'offset': 0,
            }
            data = _ridb_get('/facilities', params=params)
            if not data:
                continue

            batch = data.get('RECDATA', [])
            for fac in batch:
                fac_type = fac.get('FacilityTypeDescription', '')
                if fac_type not in ('Campground', 'Cabin'):
                    continue
                fid = str(fac.get('FacilityID', ''))
                if not fid:
                    continue

                # Skip if recently synced
                existing = Campground.query.filter_by(provider='rg', external_id=fid).first()
                if existing and existing.last_synced:
                    age_days = (datetime.utcnow() - existing.last_synced).days
                    if age_days < 7:
                        continue

                try:
                    _sync_rg_campground(fid, db, Campground)
                    synced_count += 1
                except Exception as e:
                    logger.error(f"Error syncing RG {fid}: {e}")
                    db.session.rollback()
                    error_count += 1

        except Exception as e:
            logger.error(f"RG grid search error at ({lat}, {lng}): {e}")

    logger.info(f"Full sync complete: {synced_count} synced, {error_count} errors")
    return synced_count, error_count


def run_scheduled_sync():
    """APScheduler entry point — creates app context and runs sync_all.

    This function lives in a stable module path so APScheduler can always
    resolve the reference (website.services.campground_sync:run_scheduled_sync)
    regardless of how Flask is loaded.
    """
    from website.app import app
    with app.app_context():
        sync_all()
