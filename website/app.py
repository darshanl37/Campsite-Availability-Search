from flask import Flask, render_template, request, jsonify, flash, redirect, url_for, session, g
import sys
import os
from datetime import datetime, timedelta
import subprocess
import logging
from werkzeug.exceptions import HTTPException
import traceback
import re
import requests
from math import radians, sin, cos, sqrt, atan2
from logging.handlers import RotatingFileHandler
import json
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import inspect
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from flask_wtf.csrf import CSRFProtect

# Load environment variables from .env file
load_dotenv()

# Import our models and services
from website.models import db, User, Subscription, Notification, Payment, VerificationCode, SearchHistory, Campground
from website.routes import register_routes
from website.services import auth_service

app = Flask(__name__)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Initialize cache
cache = Cache(app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 300  # 5 minutes
})

# Load configuration from environment variables
MAPS_API_KEY = os.environ.get('MAPS_API_KEY')
RECREATION_API_KEY = os.environ.get('RECREATION_API_KEY')
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
DATABASE_URI = os.environ.get('DATABASE_URI', 'sqlite:///camping.db')

# Configure app
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Session security
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# CSRF protection
csrf = CSRFProtect(app)

# Initialize database
db.init_app(app)

# Create all database tables if they don't exist
with app.app_context():
    # Use inspector to check if tables exist first
    inspector = inspect(db.engine)

    if not inspector.get_table_names():
        db.create_all()
    else:
        # Create any missing tables
        db.create_all()

# Initialize APScheduler and restore active watches
from website.scheduler import init_scheduler
init_scheduler(DATABASE_URI)

with app.app_context():
    from website.services.subscription_service import SubscriptionService
    SubscriptionService.restore_active_watches()

# Schedule weekly campground sync
# Reference the function via its stable module path so APScheduler can always
# resolve the stored reference, regardless of how Flask is loaded.
from website.scheduler import get_scheduler
_sched = get_scheduler()
if _sched:
    try:
        from website.services.campground_sync import run_scheduled_sync
        _sched.add_job(
            run_scheduled_sync,
            'interval',
            weeks=1,
            id='campground_weekly_sync',
            name='Weekly campground data sync',
            replace_existing=True,
        )
        app.logger.info("Scheduled weekly campground sync job")
    except Exception as _e:
        app.logger.warning(f"Could not schedule campground sync: {_e}")

# Configure logging with timestamp
LOG_DIR = 'logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# Create timestamp for log file
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = os.path.join(LOG_DIR, f'camping_search_{timestamp}.log')

# Configure file handler
file_handler = RotatingFileHandler(
    log_file,
    maxBytes=1024 * 1024,  # 1MB
    backupCount=10
)

# Configure formatter
formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(formatter)

# Set up logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

# Required environment variables — no fallback to dev paths
SCRIPT_DIR = os.environ.get('CAMPING_SCRIPT_DIR')
VENV_PYTHON = os.environ.get('VENV_PYTHON')

if not SCRIPT_DIR or not VENV_PYTHON:
    raise RuntimeError(
        "CAMPING_SCRIPT_DIR and VENV_PYTHON environment variables must be set. "
        "Check your .env file."
    )

# Global counter for script executions
script_executions = {
    'count': 0,
    'last_executed': None,
    'sessions': set()
}

# Register all routes
register_routes(app)

# Exempt Stripe webhook from CSRF (it uses its own signature verification)
from website.routes.payment_routes import payment_bp
csrf.exempt(payment_bp)

# Add custom filter to parse JSON strings
@app.template_filter('from_json')
def from_json(value):
    return json.loads(value)

@app.route('/')
def index():
    user = auth_service.get_current_user()
    return render_template('index.html', MAPS_API_KEY=MAPS_API_KEY, user=user)

@app.route('/results')
def results():
    # Instead of rendering a results page, redirect to home with the parameters
    query_string = request.query_string.decode('utf-8')
    return redirect(f'/?{query_string}#results')

@app.route('/about')
def about():
    user = auth_service.get_current_user()
    return render_template('about.html', MAPS_API_KEY=MAPS_API_KEY, user=user)

@app.route('/campground/<provider>/<external_id>')
def campground_profile(provider, external_id):
    """Campground profile page with on-demand sync."""
    if provider not in ('rg', 'rc'):
        from flask import abort
        abort(404)

    campground = Campground.query.filter_by(provider=provider, external_id=external_id).first()

    # On-demand sync if not in DB, stale (>7 days), or missing enrichment data
    needs_sync = (
        not campground
        or not campground.last_synced
        or (datetime.utcnow() - campground.last_synced).days > 7
        or (campground.provider == 'rc' and not campground.photos)
    )
    if needs_sync:
        from website.services.campground_sync import sync_one
        sync_one(provider, external_id)
        campground = Campground.query.filter_by(provider=provider, external_id=external_id).first()

    if not campground:
        from flask import abort
        abort(404)

    user = auth_service.get_current_user()
    return render_template('campground/profile.html', cg=campground, user=user)


@app.route('/campground/<slug>')
def campground_profile_by_slug(slug):
    """SEO-friendly campground profile URL."""
    campground = Campground.query.filter_by(slug=slug).first()
    if not campground:
        from flask import abort
        abort(404)
    return redirect(url_for('campground_profile', provider=campground.provider, external_id=campground.external_id))


@app.route('/history')
def history():
    user = auth_service.get_current_user()
    history_items = []
    watches = []

    if user:
        # Get active watches/subscriptions for this user
        watches = Subscription.query.filter_by(user_id=user.id).order_by(
            Subscription.active.desc(), Subscription.created_at.desc()
        ).all()

        # Get history for authenticated user
        history_records = SearchHistory.query.filter_by(user_id=user.id).order_by(
            SearchHistory.created_at.desc()
        ).limit(30).all()
        history_items = [record.to_dict() for record in history_records]
    else:
        # Get history for anonymous user from device_id cookie
        device_id = request.cookies.get('device_id')
        if device_id:
            history_records = SearchHistory.query.filter_by(device_id=device_id).order_by(
                SearchHistory.created_at.desc()
            ).limit(10).all()
            history_items = [record.to_dict() for record in history_records]

    return render_template('history.html', history_items=history_items, watches=watches, user=user)

def build_calendar_data(json_results, search_start, search_end, nights):
    """Transform date-range results into per-day calendar availability data.

    Input json_results shape (from camping_wrapper.py --json-output):
    {
        "Kirby Cove (232447)": {
            "priority": {"2025-08-15 (Fri) -> 2025-08-17 (Sun)": 3},
            "regular": {"2025-08-14 (Thu) -> 2025-08-16 (Sat)": 2},
            "ignored": {}
        }
    }

    Output shape:
    {
        "Kirby Cove": {
            "park_id": "232447",
            "dates": {
                "2025-08-15": {"count": 3, "type": "priority", "checkout": "2025-08-17"},
                ...
            }
        }
    }
    """
    calendar_data = {}

    for park_key, categories in json_results.items():
        # Extract park_id from name like "Kirby Cove (232447)" or "Big Basin (rc:718)"
        park_id_match = re.search(r'\(((?:rc:|rg:)?\d+)\)', park_key)
        park_id = park_id_match.group(1) if park_id_match else ""
        park_name = re.sub(r'\s*\((?:rc:|rg:)?\d+\)\s*$', '', park_key).strip()

        # Determine provider from the park_id prefix
        provider = 'ReserveCalifornia' if park_id.startswith('rc:') else 'RecreationGov'

        dates = {}

        # Process each category in priority order (priority > regular > ignored)
        for category in ['priority', 'regular', 'ignored']:
            ranges = categories.get(category, {})
            for date_range_str, count in ranges.items():
                # Parse "2025-08-15 (Fri) -> 2025-08-17 (Sun)"
                match = re.match(
                    r'(\d{4}-\d{2}-\d{2})\s*\([A-Za-z]+\)\s*->\s*(\d{4}-\d{2}-\d{2})\s*\([A-Za-z]+\)',
                    date_range_str
                )
                if not match:
                    continue

                start_str, end_str = match.group(1), match.group(2)
                start_dt = datetime.strptime(start_str, '%Y-%m-%d')
                end_dt = datetime.strptime(end_str, '%Y-%m-%d')

                # Each night of the stay is a check-in day except the last (checkout)
                current = start_dt
                while current < end_dt:
                    day_key = current.strftime('%Y-%m-%d')
                    # Only overwrite if this category has higher priority
                    if day_key not in dates:
                        dates[day_key] = {
                            'count': count,
                            'type': category,
                            'checkout': end_str,
                        }
                    current += timedelta(days=1)

        calendar_data[park_name] = {
            'park_id': park_id,
            'provider': provider,
            'dates': dates,
        }

    return calendar_data

@app.route('/search', methods=['POST'])
@limiter.limit("30 per minute")
def search():
    try:
        data = request.get_json()
        
        # Create cache key from search parameters (v2 = multi-campground support)
        cache_key = f"search_v2_{data.get('parkId')}_{data.get('startDate')}_{data.get('endDate')}_{data.get('nights')}_{data.get('searchPreference')}"
        
        # Try to get cached results
        cached_result = cache.get(cache_key)
        if cached_result:
            logger.info(f"Returning cached results for {cache_key}")
            return jsonify(cached_result)
        
        # Update execution stats
        script_executions['count'] += 1
        script_executions['last_executed'] = datetime.now()
        script_executions['sessions'].add(request.remote_addr)
        
        # Log search parameters and stats
        logger.info("\n=== Search Parameters ===")
        logger.info(f"Park ID: {data.get('parkId')}")
        logger.info(f"Start Date: {data.get('startDate')}")
        logger.info(f"End Date: {data.get('endDate')}")
        logger.info(f"Nights: {data.get('nights')}")
        logger.info(f"Search Preference: {data.get('searchPreference')}")
        logger.info("----------------------------------------")
        logger.info("Session Stats:")
        logger.info(f"Total Searches: {script_executions['count']}")
        logger.info(f"Last Search: {script_executions['last_executed'].strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Unique Users: {len(script_executions['sessions'])}")
        logger.info(f"Current User IP: {request.remote_addr}")
        logger.info("----------------------------------------")
        
        # Extract data from request
        park_id = data.get('parkId')
        start_date = data.get('startDate')
        end_date = data.get('endDate')
        nights = data.get('nights')
        search_preference = data.get('searchPreference')
        
        # Split IDs by provider
        from website.services.reserve_california import split_ids_by_provider, search_rc_availability

        all_ids = [p.strip() for p in park_id.split(',') if p.strip()]
        by_provider = split_ids_by_provider(park_id)
        rg_ids = by_provider.get('rg', [])
        rc_ids = by_provider.get('rc', [])

        total_count = len(rg_ids) + len(rc_ids)

        # Restrict "all dates" search for large batches
        BATCH_SIZE = 5  # Process 5 campgrounds at a time
        if total_count > 8 and search_preference == 'all':
            logger.warning(f"Too many campgrounds ({total_count}) for 'all dates' search")
            return jsonify({
                'success': False,
                'error': f'Searching all dates for {total_count} campgrounds would take too long. Please select "Weekends Only" or "Flexible (Weekends + Weekdays)" instead, or search fewer campgrounds.'
            }), 400

        logger.info(f"Searching {total_count} campground(s): RG={rg_ids}, RC={rc_ids}")

        # Save to search history
        for pid in all_ids:
            save_search_history(
                park_id=pid,
                start_date=start_date,
                end_date=end_date,
                nights=nights,
                search_preference=search_preference,
                campground_name=data.get('campgroundName', '')
            )

        # Use camping_wrapper.py directly with --json-output
        WRAPPER_NAME = 'camping_wrapper.py'

        # Helper function to run search for a batch of Recreation.gov campgrounds
        def search_batch(batch_park_ids):
            cmd = [
                VENV_PYTHON,
                os.path.join(SCRIPT_DIR, WRAPPER_NAME),
                '--start-date', start_date,
                '--end-date', end_date,
                '--parks',
            ]
            cmd.extend(batch_park_ids)
            cmd.extend([
                '--nights', str(nights),
                '--json-output',
            ])

            logger.info(f"Executing batch command: {' '.join(cmd)}")

            timeout_seconds = max(60, len(batch_park_ids) * 30)

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=SCRIPT_DIR,
                    timeout=timeout_seconds,
                )

                logger.info(f"Batch completed. Return code: {result.returncode}")
                logger.info(f"Output length: {len(result.stdout)} chars")
                if result.stderr:
                    logger.warning(f"Batch stderr: {result.stderr[:500]}")

                if result.returncode != 0:
                    logger.error(f"Batch command failed with return code {result.returncode}")
                    return None

                if not result.stdout or len(result.stdout) < 2:
                    logger.error(f"Batch returned empty output: {result.stdout}")
                    return None

                return result.stdout
            except subprocess.TimeoutExpired:
                logger.error(f"Batch search timed out after {timeout_seconds} seconds")
                return None
            except Exception as e:
                logger.error(f"Batch search error: {str(e)}")
                return None

        # ── Recreation.gov search (existing subprocess path) ──
        merged_json = {}
        if rg_ids:
            if len(rg_ids) <= BATCH_SIZE:
                raw = search_batch(rg_ids)
                if raw:
                    try:
                        merged_json = json.loads(raw)
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON parse error: {e}")
            else:
                logger.info(f"Running batch search for {len(rg_ids)} RG campgrounds in batches of {BATCH_SIZE}")
                for i in range(0, len(rg_ids), BATCH_SIZE):
                    batch = rg_ids[i:i + BATCH_SIZE]
                    logger.info(f"Processing batch {i//BATCH_SIZE + 1}: {len(batch)} campgrounds")
                    raw = search_batch(batch)
                    if raw:
                        try:
                            batch_json = json.loads(raw)
                            merged_json.update(batch_json)
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON parse error in batch {i//BATCH_SIZE + 1}: {e}")
                    else:
                        logger.warning(f"Batch {i//BATCH_SIZE + 1} timed out or failed")

        # ── ReserveCalifornia search (in-process) ──
        if rc_ids:
            logger.info(f"Searching {len(rc_ids)} ReserveCalifornia campground(s)")
            try:
                rc_results = search_rc_availability(rc_ids, start_date, end_date, nights)
                merged_json.update(rc_results)
                logger.info(f"RC search returned {len(rc_results)} results")
            except Exception as e:
                logger.error(f"ReserveCalifornia search error: {e}")
                logger.error(traceback.format_exc())

        if merged_json is None:
            logger.error("Search failed: all batches returned None")
            return jsonify({
                'success': False,
                'error': 'Search failed or timed out. Try selecting fewer campgrounds or a shorter date range.',
            }), 504

        if 'error' in merged_json:
            error_msg = merged_json.get('error', 'Search failed.')
            logger.error(f"Search returned error: {error_msg}")
            return jsonify({
                'success': False,
                'error': error_msg,
            }), 504

        # Build calendar data from structured JSON
        calendar_data = build_calendar_data(merged_json, start_date, end_date, int(nights))

        logger.info(f"Built calendar data for {len(calendar_data)} campground(s)")
        for cg_name, cg_info in calendar_data.items():
            logger.info(f"  - {cg_name} ({cg_info['park_id']}): {len(cg_info['dates'])} available days")

        response_data = {
            'success': True,
            'calendar_data': calendar_data,
            'search_params': {
                'start_date': start_date,
                'end_date': end_date,
                'nights': int(nights),
            },
        }
        cache.set(cache_key, response_data, timeout=180)

        logger.info(f"Returning response with {len(calendar_data)} campgrounds")
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Search error: {str(e)}\n{traceback.format_exc()}")
        
        # Send error notification (basic error monitoring)
        try:
            error_details = {
                'error': str(e),
                'traceback': traceback.format_exc(),
                'url': request.url,
                'method': request.method,
                'data': data if 'data' in locals() else None,
                'timestamp': datetime.now().isoformat()
            }
            logger.critical(f"CRITICAL ERROR: {json.dumps(error_details, indent=2)}")
        except:
            pass
        
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def _fetch_ridb_facilities(lat, lng, radius=100, max_results=500):
    """Fetch facilities from RIDB with pagination.

    RIDB caps `limit` at 50 per request. This helper pages through
    results using `offset` until fewer than 50 are returned or
    `max_results` is reached (safety cap: 10 iterations).
    """
    url = 'https://ridb.recreation.gov/api/v1/facilities'
    all_facilities = []
    page_size = 50  # RIDB max per page
    max_pages = 10  # safety cap

    for page in range(max_pages):
        offset = page * page_size
        params = {
            'latitude': lat,
            'longitude': lng,
            'radius': radius,
            'activity': 'CAMPING',
            'limit': page_size,
            'offset': offset,
            'apikey': RECREATION_API_KEY,
        }

        logger.info(f"RIDB page {page + 1}: offset={offset}")
        resp = requests.get(url, params=params, timeout=10)

        if resp.status_code != 200:
            logger.error(f"RIDB API error on page {page + 1}: {resp.status_code}")
            break

        batch = resp.json().get('RECDATA', [])
        all_facilities.extend(batch)
        logger.info(f"RIDB page {page + 1}: received {len(batch)} facilities (total {len(all_facilities)})")

        if len(batch) < page_size or len(all_facilities) >= max_results:
            break

    return all_facilities[:max_results]


@app.route('/search_campsites', methods=['POST'])
@csrf.exempt
@limiter.limit("20 per minute")
def search_campsites():
    try:
        data = request.get_json()
        lat = data.get('latitude')
        lng = data.get('longitude')

        logger.info(f"Searching campsites near lat={lat}, lng={lng}")

        # Check if API key is set
        if not RECREATION_API_KEY:
            logger.error("Recreation.gov API key not set!")
            return jsonify({
                'success': False,
                'error': 'Recreation.gov API key not configured'
            }), 500

        # Fetch Recreation.gov and ReserveCalifornia in parallel
        from website.services.reserve_california import discover_rc_campgrounds
        rg_results = []
        rc_results = []

        with ThreadPoolExecutor(max_workers=2) as pool:
            rg_future = pool.submit(_fetch_ridb_facilities, lat, lng, 100, 500)
            rc_future = pool.submit(discover_rc_campgrounds, float(lat), float(lng), 100)

            try:
                all_facilities = rg_future.result(timeout=20)
            except Exception as e:
                logger.error(f"RIDB fetch error: {e}")
                all_facilities = []

            try:
                rc_results = rc_future.result(timeout=20)
            except Exception as e:
                logger.error(f"RC discovery error: {e}")
                rc_results = []

        logger.info(f"Fetched {len(all_facilities)} RIDB facilities + {len(rc_results)} RC campgrounds")

        # Include both Campgrounds and Cabins from Recreation.gov
        ALLOWED_TYPES = {'Campground', 'Cabin'}
        rg_campsites = []
        for site in all_facilities:
            if site.get('FacilityTypeDescription') not in ALLOWED_TYPES:
                continue
            # Pick the best image: prefer IsPrimary, then IsPreview, then first
            image_url = ''
            media = site.get('MEDIA') or []
            for m in media:
                if m.get('MediaType') == 'Image' and m.get('IsPrimary'):
                    image_url = m.get('URL', '')
                    break
            if not image_url:
                for m in media:
                    if m.get('MediaType') == 'Image' and m.get('IsPreview'):
                        image_url = m.get('URL', '')
                        break
            if not image_url:
                for m in media:
                    if m.get('MediaType') == 'Image' and m.get('URL'):
                        image_url = m.get('URL', '')
                        break
            fac_id = site.get('FacilityID')
            rg_campsites.append({
                'name': site.get('FacilityName'),
                'id': f"rg:{fac_id}",
                'description': site.get('FacilityDescription'),
                'latitude': site.get('FacilityLatitude'),
                'longitude': site.get('FacilityLongitude'),
                'type': site.get('FacilityTypeDescription'),
                'provider': 'RecreationGov',
                'image_url': image_url,
                'booking_url': f"https://www.recreation.gov/camping/campgrounds/{fac_id}",
                'phone': site.get('FacilityPhone', ''),
            })

        # Enrich RC results with photos/descriptions from DB
        rc_ext_ids = [r['id'].replace('rc:', '') for r in rc_results]
        rc_db_map = {}
        if rc_ext_ids:
            rc_records = Campground.query.filter(
                Campground.provider == 'rc',
                Campground.external_id.in_(rc_ext_ids)
            ).all()
            rc_db_map = {cg.external_id: cg for cg in rc_records}

        for site in rc_results:
            ext_id = site['id'].replace('rc:', '')
            cg = rc_db_map.get(ext_id)
            if cg:
                if not site.get('image_url') and cg.photos:
                    site['image_url'] = cg.primary_photo or ''
                if (not site.get('description') or len(site.get('description', '')) < 50) and cg.description_overview:
                    site['description'] = cg.description_overview

        # RC results already have the right shape (with rc: prefix and provider field)
        all_campsites = rg_campsites + rc_results

        logger.info(f"Returning {len(all_campsites)} total campsites ({len(rg_campsites)} RG + {len(rc_results)} RC)")

        return jsonify({
            'success': True,
            'campsites': all_campsites,
        })

    except requests.exceptions.Timeout:
        logger.error("Recreation.gov API timeout")
        return jsonify({
            'success': False,
            'error': 'Recreation.gov API timeout - please try again'
        }), 500
    except requests.exceptions.RequestException as e:
        logger.error(f"Recreation.gov API request error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Network error: {str(e)}'
        }), 500
    except Exception as e:
        logger.error(f"Error searching campsites: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/search_campsites_by_name', methods=['POST'])
@csrf.exempt
@limiter.limit("20 per minute")
def search_campsites_by_name():
    """Search campgrounds by name across RIDB and ReserveCalifornia."""
    try:
        data = request.get_json()
        query = (data.get('query') or '').strip()
        if not query or len(query) < 2:
            return jsonify({'success': True, 'campsites': []})

        from website.services.reserve_california import search_rc_campgrounds_by_name

        rg_results = []
        rc_results = []

        # RIDB name search
        if RECREATION_API_KEY:
            try:
                resp = requests.get(
                    'https://ridb.recreation.gov/api/v1/facilities',
                    params={
                        'query': query,
                        'activity': 'CAMPING',
                        'limit': 50,
                        'apikey': RECREATION_API_KEY,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    for site in resp.json().get('RECDATA', []):
                        ft = site.get('FacilityTypeDescription', '')
                        if ft in ('Campground', 'Cabin'):
                            # Pick the best image
                            image_url = ''
                            media = site.get('MEDIA') or []
                            for m in media:
                                if m.get('MediaType') == 'Image' and m.get('IsPrimary'):
                                    image_url = m.get('URL', '')
                                    break
                            if not image_url:
                                for m in media:
                                    if m.get('MediaType') == 'Image' and m.get('IsPreview'):
                                        image_url = m.get('URL', '')
                                        break
                            if not image_url:
                                for m in media:
                                    if m.get('MediaType') == 'Image' and m.get('URL'):
                                        image_url = m.get('URL', '')
                                        break
                            fac_id = site.get('FacilityID')
                            rg_results.append({
                                'name': site.get('FacilityName'),
                                'id': f"rg:{fac_id}",
                                'description': site.get('FacilityDescription'),
                                'latitude': site.get('FacilityLatitude'),
                                'longitude': site.get('FacilityLongitude'),
                                'type': ft,
                                'provider': 'RecreationGov',
                                'image_url': image_url,
                                'booking_url': f"https://www.recreation.gov/camping/campgrounds/{fac_id}",
                                'phone': site.get('FacilityPhone', ''),
                            })
            except Exception as e:
                logger.error(f"RIDB name search error: {e}")

        # RC name search
        try:
            rc_results = search_rc_campgrounds_by_name(query)
        except Exception as e:
            logger.error(f"RC name search error: {e}")

        # Enrich RC results with photos/descriptions from DB
        rc_ext_ids = [r['id'].replace('rc:', '') for r in rc_results]
        if rc_ext_ids:
            rc_records = Campground.query.filter(
                Campground.provider == 'rc',
                Campground.external_id.in_(rc_ext_ids)
            ).all()
            rc_db_map = {cg.external_id: cg for cg in rc_records}
            for site in rc_results:
                ext_id = site['id'].replace('rc:', '')
                cg = rc_db_map.get(ext_id)
                if cg:
                    if not site.get('image_url') and cg.photos:
                        site['image_url'] = cg.primary_photo or ''
                    if (not site.get('description') or len(site.get('description', '')) < 50) and cg.description_overview:
                        site['description'] = cg.description_overview

        return jsonify({
            'success': True,
            'campsites': rg_results + rc_results,
        })

    except Exception as e:
        logger.error(f"Name search error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors
    if isinstance(e, HTTPException):
        return e

    # Now you're handling non-HTTP exceptions only
    app.logger.error(f"An error occurred: {str(e)}")
    app.logger.error(traceback.format_exc())
    return jsonify({
        "error": "An unexpected error occurred",
        "details": str(e)
    }), 500

# Context processor to pass data to all templates
@app.context_processor
def inject_user():
    """Make the current user available to all templates."""
    user = auth_service.get_current_user()
    # If we have a user, ensure it has an is_authenticated property
    if user:
        user.is_authenticated = True
    return {
        'current_user': user
    }

def save_search_history(park_id, start_date, end_date, nights, search_preference, campground_name=""):
    """Save the search to history for both logged in and anonymous users."""
    try:
        # Get user info
        user = auth_service.get_current_user()
        user_id = user.id if user else None
        
        logger.debug(f"Current user: {user_id}, Cookie device_id: {request.cookies.get('device_id')}")
        
        # Get or create device_id for anonymous users
        device_id = request.cookies.get('device_id')
        if not user and not device_id:
            device_id = str(uuid.uuid4())
            logger.debug(f"Generated new device_id: {device_id}")
            # Note: We'll set this cookie in the response
        
        # Parse dates
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            # Handle invalid date format
            logger.error(f"Invalid date format in search history: {start_date}, {end_date}")
            return
        
        # Extract city/state from campground name if possible
        city = None
        state = None
        if ',' in campground_name:
            parts = campground_name.split(',')
            if len(parts) >= 2:
                # Last part might contain state
                state_part = parts[-1].strip()
                if len(state_part) <= 3:  # Likely a state abbreviation
                    state = state_part
                    city = parts[-2].strip()
                else:
                    city = state_part
        
        # Create history record
        history_entry = SearchHistory(
            user_id=user_id,
            device_id=device_id if not user else None,  # Only set to None for logged-in users
            park_id=park_id,
            park_name=campground_name,
            city=city,
            state=state,
            start_date=start_date_obj,
            end_date=end_date_obj,
            nights=int(nights),
            search_preference=search_preference,
            ip_address=request.remote_addr
        )
        
        logger.debug(f"Saving search history: user_id={user_id}, device_id={device_id if not user else None}, park_id={park_id}")
        
        db.session.add(history_entry)
        db.session.commit()
        
        # Store the device_id in the app context for the response
        if not user and device_id:
            logger.debug(f"Setting g.device_id: {device_id}")
            g.device_id = device_id
            
        return history_entry
    except Exception as e:
        logger.error(f"Error saving search history: {str(e)}")
        db.session.rollback()
        return None

# After request handler to set device_id cookie
@app.after_request
def set_device_id_cookie(response):
    if hasattr(g, 'device_id'):
        logger.debug(f"Setting device_id cookie: {g.device_id}")
        response.set_cookie('device_id', g.device_id, max_age=60*60*24*365)  # 1 year
    return response

if __name__ == '__main__':
    # Set both Flask and logging to DEBUG level
    app.logger.setLevel(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    app.run(debug=True, host='0.0.0.0', port=5000) 