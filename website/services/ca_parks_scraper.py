"""
Scraper for parks.ca.gov — enriches ReserveCalifornia campground profiles
with descriptions, photos, amenities, directions, fees, and rules from
official California State Parks pages.

Uses BeautifulSoup (already installed) for HTML parsing.
Rate-limited to 1 req/sec to be respectful.
"""

import logging
import re
import time
from difflib import SequenceMatcher
from threading import Lock

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Rate limiting ─────────────────────────────────────────────────────
_last_request_time = 0
_rate_lock = Lock()


def _rate_limit():
    """Sleep to ensure at least 1 second between requests."""
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_request_time = time.time()


def _fetch_page(url):
    """Fetch a page from parks.ca.gov with rate limiting."""
    _rate_limit()
    try:
        resp = requests.get(url, timeout=15, headers={
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
        })
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


# ── Park index: name → page_id mapping ────────────────────────────────
# Built-in mapping of common CA state park names to their parks.ca.gov
# page_id values.  This avoids scraping the index page on every sync.
# Source: https://www.parks.ca.gov/?page_id=21805

_PARK_NAME_TO_PAGE_ID = {
    "Admiral William Standley SRA": 424,
    "Ahjumawi Lava Springs State Park": 464,
    "Anderson Marsh State Historic Park": 483,
    "Andrew Molera State Park": 582,
    "Angel Island State Park": 468,
    "Año Nuevo State Park": 523,
    "Ano Nuevo State Park": 523,
    "Antelope Valley California Poppy Reserve": 627,
    "Anza-Borrego Desert State Park": 638,
    "Armstrong Redwoods SNR": 450,
    "Asilomar State Beach": 566,
    "Auburn State Recreation Area": 502,
    "Austin Creek SRA": 452,
    "Benbow State Recreation Area": 426,
    "Benicia State Recreation Area": 476,
    "Bethany Reservoir SRA": 562,
    "Big Basin Redwoods State Park": 540,
    "Bodie State Historic Park": 509,
    "Bolsa Chica State Beach": 642,
    "Border Field State Park": 664,
    "Bothe-Napa Valley State Park": 477,
    "Brannan Island SRA": 487,
    "Burton Creek State Park": 512,
    "Butano State Park": 536,
    "Calaveras Big Trees State Park": 551,
    "Candlestick Point SRA": 519,
    "Cardiff State Beach": 656,
    "Carlsbad State Beach": 653,
    "Carnegie State Vehicular Recreation Area": 1172,
    "Carpinteria State Beach": 599,
    "Castaic Lake SRA": 628,
    "Castle Crags State Park": 454,
    "Castle Rock State Park": 538,
    "Caswell Memorial State Park": 557,
    "China Camp State Park": 466,
    "Chino Hills State Park": 648,
    "Clear Lake State Park": 473,
    "Colonel Allensworth State Historic Park": 583,
    "Columbia State Historic Park": 552,
    "Colusa-Sacramento River SRA": 461,
    "Crystal Cove State Park": 644,
    "Cuyamaca Rancho State Park": 667,
    "D. L. Bliss State Park": 505,
    "DL Bliss State Park": 505,
    "Del Norte Coast Redwoods State Park": 414,
    "Doheny State Beach": 645,
    "Donner Memorial State Park": 503,
    "Ed Z'berg Sugar Pine Point State Park": 510,
    "Sugar Pine Point State Park": 510,
    "El Capitán State Beach": 601,
    "El Capitan State Beach": 601,
    "Emerald Bay State Park": 506,
    "Emma Wood State Beach": 604,
    "Empire Mine State Historic Park": 499,
    "Folsom Lake SRA": 500,
    "Fort Humboldt State Historic Park": 665,
    "Fort Ord Dunes State Park": 580,
    "Fort Ross State Historic Park": 449,
    "Fremont Peak State Park": 564,
    "Garrapata State Park": 579,
    "Gaviota State Park": 606,
    "Grizzly Creek Redwoods State Park": 421,
    "Grover Hot Springs State Park": 508,
    "Half Moon Bay State Beach": 531,
    "Hearst San Simeon State Park": 590,
    "Hendy Woods State Park": 438,
    "Henry Cowell Redwoods State Park": 546,
    "Henry W. Coe State Park": 561,
    "Hollister Hills State Vehicular Recreation Area": 1179,
    "Humboldt Lagoons State Park": 416,
    "Humboldt Redwoods State Park": 425,
    "Hungry Valley State Vehicular Recreation Area": 1192,
    "Huntington State Beach": 643,
    "Indian Grinding Rock State Historic Park": 553,
    "Jack London State Historic Park": 478,
    "Jedediah Smith Redwoods State Park": 413,
    "Julia Pfeiffer Burns State Park": 578,
    "Kenneth Hahn State Recreation Area": 612,
    "Kings Beach SRA": 511,
    "Lake Del Valle SRA": 537,
    "Lake Oroville SRA": 462,
    "Lake Perris SRA": 651,
    "Leo Carrillo State Park": 616,
    "Limekiln State Park": 577,
    "MacKerricher State Park": 436,
    "Malakoff Diggins State Historic Park": 494,
    "Malibu Creek State Park": 614,
    "Manchester State Park": 437,
    "Manresa State Beach": 545,
    "McArthur-Burney Falls Memorial State Park": 455,
    "Burney Falls": 455,
    "McConnell SRA": 554,
    "McGrath State Beach": 607,
    "Mendocino Woodlands State Park": 443,
    "Millerton Lake SRA": 587,
    "Montaña de Oro State Park": 592,
    "Montana de Oro State Park": 592,
    "Morro Bay State Park": 594,
    "Morro Strand State Beach": 593,
    "Mount Diablo State Park": 517,
    "Mount San Jacinto State Park": 636,
    "Mount Tamalpais State Park": 471,
    "New Brighton State Beach": 542,
    "Oceano Dunes State Vehicular Recreation Area": 1207,
    "Ocotillo Wells State Vehicular Recreation Area": 1217,
    "Pacheco State Park": 560,
    "Palomar Mountain State Park": 637,
    "Pfeiffer Big Sur State Park": 570,
    "Big Sur": 570,
    "Picacho SRA": 641,
    "Pismo State Beach": 595,
    "Placerita Canyon State Park": 622,
    "Plumas-Eureka State Park": 507,
    "Point Mugu State Park": 630,
    "Portola Redwoods State Park": 539,
    "Prairie Creek Redwoods State Park": 415,
    "Providence Mountains SRA": 615,
    "Red Rock Canyon State Park": 631,
    "Refugio State Beach": 603,
    "Reynolds Wayside Campground": 428,
    "Richardson Grove State Park": 422,
    "Russian Gulch State Park": 432,
    "Saddleback Butte State Park": 618,
    "Salt Point State Park": 453,
    "Salton Sea SRA": 639,
    "Samuel P. Taylor State Park": 469,
    "San Clemente State Beach": 646,
    "San Elijo State Beach": 662,
    "San Luis Reservoir SRA": 558,
    "San Onofre State Beach": 647,
    "San Simeon State Park": 590,
    "Seacliff State Beach": 543,
    "Silverwood Lake SRA": 650,
    "Sinkyone Wilderness State Park": 429,
    "Sonoma Coast State Park": 451,
    "South Carlsbad State Beach": 660,
    "Standish-Hickey SRA": 423,
    "Sue-meg State Park": 417,
    "Patrick's Point State Park": 417,
    "Sugarloaf Ridge State Park": 481,
    "Sunset State Beach": 544,
    "Tahoe SRA": 504,
    "Topanga State Park": 629,
    "Turlock Lake SRA": 555,
    "Van Damme State Park": 433,
    "Washoe Meadows State Park": 516,
    "Westport-Union Landing State Beach": 440,
    "Wilder Ranch State Park": 549,
    "William B. Ide Adobe State Historic Park": 458,
    "Woodson Bridge SRA": 459,
}


def _normalize_park_name(name):
    """Normalize a park name for fuzzy matching."""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [
        'state park', 'state beach', 'state historic park',
        'state recreation area', 'state natural reserve',
        'state vehicular recreation area', 'sra', 'snr', 'sp', 'sb',
        'campground', 'camp', 'camping',
    ]:
        name = re.sub(rf'\s*{re.escape(suffix)}\s*$', '', name)
    return name.strip()


def find_park_page_id(place_name):
    """Find the parks.ca.gov page_id for a given RC place name.

    Uses exact match first, then fuzzy matching against the built-in mapping.
    Returns page_id (int) or None.
    """
    if not place_name:
        return None

    # Exact match
    if place_name in _PARK_NAME_TO_PAGE_ID:
        return _PARK_NAME_TO_PAGE_ID[place_name]

    # Case-insensitive exact match
    name_lower = place_name.lower()
    for key, pid in _PARK_NAME_TO_PAGE_ID.items():
        if key.lower() == name_lower:
            return pid

    # Fuzzy match: compare normalized names
    normalized_input = _normalize_park_name(place_name)
    best_match = None
    best_score = 0.0

    for key, pid in _PARK_NAME_TO_PAGE_ID.items():
        normalized_key = _normalize_park_name(key)
        score = SequenceMatcher(None, normalized_input, normalized_key).ratio()
        if score > best_score:
            best_score = score
            best_match = pid

    # Require a reasonably good match (>= 0.6)
    if best_score >= 0.6:
        logger.debug(
            f"Fuzzy matched '{place_name}' → page_id={best_match} (score={best_score:.2f})"
        )
        return best_match

    logger.debug(f"No parks.ca.gov match for '{place_name}' (best score={best_score:.2f})")
    return None


# ── Scrape individual park page ───────────────────────────────────────

def scrape_park_page(page_id):
    """Scrape a parks.ca.gov park page and return structured data.

    Returns a dict with keys:
        description, recreation, facilities, directions, phone, email,
        fees, rules, pets, hours, photos, amenities
    """
    url = f'https://www.parks.ca.gov/?page_id={page_id}'
    html = _fetch_page(url)
    if not html:
        return {}

    soup = BeautifulSoup(html, 'lxml')
    result = {}

    # ── Description ──
    # Find the main content: div#main-content > div.col-md-9 > first div.container
    main_content = None
    main_row = soup.find('div', id='main-content')
    if main_row:
        col = main_row.find('div', class_='col-md-9')
        if col:
            main_content = col.find('div', class_='container')

    # Fallback chain for edge cases
    if not main_content:
        main_content = (
            soup.find('div', class_='entry-content')
            or soup.find('article')
            or soup.body
        )

    if main_content:
        paragraphs = main_content.find_all('p', recursive=True)
        desc_parts = []
        for p in paragraphs[:10]:  # Limit to first 10 paragraphs
            text = p.get_text(strip=True)
            if text and len(text) > 20:
                # Skip boilerplate / navigation / non-description text
                if any(skip in text.lower() for skip in [
                    'copyright', 'privacy policy', 'all rights reserved',
                    'subscribe', 'newsletter', 'follow us',
                    'check current weather', 'google map',
                ]):
                    continue
                # Stop collecting if we hit weather/location boilerplate
                if text.lower().startswith('weather') or text.lower().startswith('location'):
                    break
                desc_parts.append(text)

        if desc_parts:
            result['description'] = '\n\n'.join(desc_parts[:5])

    # ── Photos ──
    # Search entire page for park-specific images (gallery may be outside main_content)
    photos = []
    seen_urls = set()
    for img in soup.find_all('img'):
            src = img.get('src', '')
            if not src or src in seen_urls:
                continue
            # Only include park-specific images (not icons, logos, etc.)
            if f'/pages/{page_id}/images/' in src or '/parkimages/' in src.lower():
                # Make absolute URL, resolving relative paths like ../../pages/570/images/foo.jpg
                if src.startswith('http'):
                    pass  # already absolute
                else:
                    # Strip leading ../../ or ./ and build clean URL
                    clean = re.sub(r'^(\.\./)+', '', src)
                    clean = re.sub(r'^\./', '', clean)
                    clean = clean.lstrip('/')
                    src = f'https://www.parks.ca.gov/{clean}'
                photos.append({
                    'url': src,
                    'title': img.get('alt', '').strip(),
                    'credits': 'California State Parks',
                    'isPrimary': len(photos) == 0,
                    'isGallery': True,
                })
                seen_urls.add(src)
    if photos:
        result['photos'] = photos

    # ── Amenities / Activities ──
    amenities = []
    # Primary: look inside the accordion panel for "Activities and Facilities"
    accordion = soup.find('div', id='accordionparkinfo')
    if accordion:
        for card in accordion.find_all('div', class_='card'):
            heading = card.find(['h2', 'h3', 'h4', 'button'])
            if heading and 'activit' in heading.get_text(strip=True).lower():
                card_body = card.find('div', class_='card-body') or card
                for li in card_body.find_all('li'):
                    text = li.get_text(strip=True)
                    if text and 5 < len(text) < 100:
                        amenities.append(text)
    # Fallback: scrape <ul> items from main content
    if not amenities and main_content:
        for ul in main_content.find_all('ul'):
            for li in ul.find_all('li'):
                text = li.get_text(strip=True)
                if text and 5 < len(text) < 100:
                    # Filter out navigation items
                    if not any(skip in text.lower() for skip in [
                        'home', 'contact us', 'about us', 'faq',
                        'facebook', 'twitter', 'instagram',
                    ]):
                        amenities.append(text)

    if amenities:
        result['amenities'] = list(dict.fromkeys(amenities))  # dedupe preserving order

    # ── Phone ──
    # Search entire page since phone is often in the header/contact bar
    phone_pattern = re.compile(r'\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}')
    page_text = soup.get_text()
    phone_match = phone_pattern.search(page_text)
    if phone_match:
        result['phone'] = phone_match.group(0)

    # ── Email ──
    # Search entire page for mailto links
    email_links = soup.find_all('a', href=re.compile(r'^mailto:'))
    for link in email_links:
        email = link.get('href', '').replace('mailto:', '').strip()
        if '@' in email:
            result['email'] = email
            break

    # ── Directions ──
    # Look for directions-related text near headings or keywords
    if main_content:
        for heading in main_content.find_all(['h2', 'h3', 'h4', 'strong']):
            heading_text = heading.get_text(strip=True).lower()
            if 'direction' in heading_text or 'getting here' in heading_text or 'location' in heading_text:
                # Collect sibling paragraphs
                directions_parts = []
                for sibling in heading.find_next_siblings(['p', 'div']):
                    text = sibling.get_text(strip=True)
                    if text and len(text) > 10:
                        directions_parts.append(text)
                    if len(directions_parts) >= 3:
                        break
                if directions_parts:
                    result['directions'] = '\n'.join(directions_parts)
                break

    # ── Fees ──
    if main_content:
        text = main_content.get_text()
        fee_pattern = re.compile(r'\$\d+\.?\d*')
        # Look for fee-related sections
        for heading in main_content.find_all(['h2', 'h3', 'h4', 'strong']):
            heading_text = heading.get_text(strip=True).lower()
            if 'fee' in heading_text or 'charge' in heading_text or 'price' in heading_text:
                fees_parts = []
                for sibling in heading.find_next_siblings(['p', 'div', 'ul']):
                    text = sibling.get_text(strip=True)
                    if text and fee_pattern.search(text):
                        fees_parts.append(text)
                    if len(fees_parts) >= 5:
                        break
                if fees_parts:
                    result['fees'] = '\n'.join(fees_parts)
                break

    # ── Pet Policy ──
    if main_content:
        text = main_content.get_text()
        pet_patterns = [
            re.compile(r'(?:dogs?|pets?)\s+(?:are\s+)?(?:allowed|not allowed|prohibited|welcome|permitted)[^.]*\.', re.IGNORECASE),
            re.compile(r'(?:no\s+)?(?:dogs?|pets?)\s+(?:on|in)\s+[^.]*\.', re.IGNORECASE),
        ]
        for pattern in pet_patterns:
            match = pattern.search(text)
            if match:
                result['pets'] = match.group(0).strip()
                break

    # ── Hours ──
    if main_content:
        text = main_content.get_text()
        hours_patterns = [
            re.compile(r'(\d{1,2}\s*(?:am|pm)\s*(?:to|[-–])\s*(?:sunset|\d{1,2}\s*(?:am|pm)))', re.IGNORECASE),
            re.compile(r'(sunrise\s*(?:to|[-–])\s*sunset)', re.IGNORECASE),
        ]
        for pattern in hours_patterns:
            match = pattern.search(text)
            if match:
                result['hours'] = match.group(0).strip()
                break

    return result
