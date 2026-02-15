// Helper function to reformat a date range string
function formatDateRange(dateRange) {
    let parts = dateRange.split('->');
    if (parts.length !== 2) return dateRange;
    let start = parts[0].trim();
    let end = parts[1].trim();

    function formatPart(part) {
        let datePart = part.substring(0, 10);
        let dayOfWeekMatch = part.match(/\((.*?)\)/);
        let dayOfWeek = dayOfWeekMatch ? ` (${dayOfWeekMatch[1]})` : "";
        let [year, month, day] = datePart.split('-');
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        let formattedDay = parseInt(day, 10);
        let formattedMonth = months[parseInt(month, 10)-1];
        let formattedYear = year.substring(2);
        return `${formattedDay} ${formattedMonth} ${formattedYear}${dayOfWeek}`;
    }

    return `${formatPart(start)} - ${formatPart(end)}`;
}

// ========================================
// Shared map state
// ========================================
let homepageMap = null;
let homepageMarkers = [];
let lastLoadedCenter = null;
let lastLoadedBounds = null;  // track the map bounds from the last search
let currentInfoWindow = null;
let currentCampsites = [];
let selectedMarkerIds = new Set();  // track selected campground IDs for marker styling

// ========================================
// Haversine distance (miles)
// ========================================
function haversineMiles(lat1, lng1, lat2, lng2) {
    const R = 3958.8;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLng = (lng2 - lng1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2 +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLng / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ========================================
// Strip HTML tags / truncate
// ========================================
function stripHtml(html) {
    const tmp = document.createElement('div');
    tmp.innerHTML = html || '';
    return tmp.textContent || tmp.innerText || '';
}

function truncate(text, len) {
    if (!text) return '';
    const clean = stripHtml(text);
    return clean.length > len ? clean.substring(0, len) + '...' : clean;
}

// ========================================
// Get CSRF token helper
// ========================================
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

// ========================================
// SessionStorage caching
// ========================================
const CACHE_KEY_PREFIX = 'campfinder_campsites_';
const CACHE_TTL_MS = 5 * 60 * 1000;

function getCachedCampsites(lat, lng) {
    const key = CACHE_KEY_PREFIX + lat.toFixed(2) + '_' + lng.toFixed(2);
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    try {
        const cached = JSON.parse(raw);
        if (Date.now() - cached.timestamp < CACHE_TTL_MS) return cached.data;
        sessionStorage.removeItem(key);
    } catch (e) { /* ignore */ }
    return null;
}

function setCachedCampsites(lat, lng, data) {
    const key = CACHE_KEY_PREFIX + lat.toFixed(2) + '_' + lng.toFixed(2);
    try {
        sessionStorage.setItem(key, JSON.stringify({ timestamp: Date.now(), data }));
    } catch (e) { /* quota exceeded */ }
}

// ========================================
// Fetch campsites (with caching)
// ========================================
function fetchCampsites(lat, lng) {
    const cached = getCachedCampsites(lat, lng);
    if (cached) return Promise.resolve(cached);

    return fetch('/search_campsites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: JSON.stringify({ latitude: lat, longitude: lng })
    })
    .then(r => {
        if (!r.ok) throw new Error(`Search failed (${r.status})`);
        return r.json();
    })
    .then(data => {
        if (data.success) setCachedCampsites(lat, lng, data);
        return data;
    });
}

// ========================================
// Fetch campsites by name
// ========================================
function fetchCampsitesByName(query) {
    return fetch('/search_campsites_by_name', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: JSON.stringify({ query })
    })
    .then(r => r.json());
}

// ========================================
// Marker styling helpers
// ========================================
function getMarkerIcon(site, isSelected) {
    const isRC = (site.provider || '').includes('California');
    const isCabin = (site.type || '').toLowerCase() === 'cabin';

    let fillColor;
    if (isSelected) {
        fillColor = '#E8590C'; // orange for selected
    } else if (isRC) {
        fillColor = '#2563EB'; // blue for CA State Parks
    } else if (isCabin) {
        fillColor = '#D4870E'; // amber for cabins
    } else {
        fillColor = '#1A5C4C'; // green for RG campgrounds
    }

    return {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor,
        fillOpacity: 1,
        strokeColor: isSelected ? '#FFFFFF' : '#FFFFFF',
        strokeWeight: isSelected ? 3 : 2,
        scale: isSelected ? 16 : 14,
    };
}

// ========================================
// Create a labeled circular marker
// ========================================
function createLabeledMarker(map, site, index) {
    const isSelected = selectedMarkerIds.has(site.id);
    const marker = new google.maps.Marker({
        position: { lat: parseFloat(site.latitude), lng: parseFloat(site.longitude) },
        map,
        title: site.name,
        label: { text: String(index + 1), color: '#FFFFFF', fontWeight: 'bold', fontSize: '11px' },
        icon: getMarkerIcon(site, isSelected),
        zIndex: isSelected ? 999 : index,
    });
    marker._siteId = site.id;
    marker._site = site;

    marker.addListener('click', () => {
        if (currentInfoWindow) currentInfoWindow.close();

        // Find all sites at the same (or very close) position
        const posKey = `${parseFloat(site.latitude).toFixed(3)},${parseFloat(site.longitude).toFixed(3)}`;
        const colocated = homepageMarkers.filter(m => {
            const mKey = `${parseFloat(m._site.latitude).toFixed(3)},${parseFloat(m._site.longitude).toFixed(3)}`;
            return mKey === posKey;
        }).map(m => m._site);

        const infoContent = document.createElement('div');
        infoContent.className = 'iw-content';

        if (colocated.length > 1) {
            // Multi-campground popup
            infoContent.innerHTML = buildMultiSiteInfoWindow(colocated);
        } else {
            // Single campground popup
            infoContent.innerHTML = buildSingleSiteInfoWindow(site);
        }

        const infoWindow = new google.maps.InfoWindow({ content: infoContent });
        infoWindow.open(map, marker);
        currentInfoWindow = infoWindow;

        google.maps.event.addListener(infoWindow, 'domready', () => {
            // Bind select buttons
            infoContent.querySelectorAll('.infowindow-select-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const siteId = btn.getAttribute('data-site-id');
                    toggleCampsiteSelection(siteId);
                    // Update this button text
                    btn.textContent = selectedMarkerIds.has(siteId) ? 'Deselect' : 'Select';
                    // Update marker icon
                    const m = homepageMarkers.find(mk => mk._siteId === siteId);
                    if (m) {
                        m.setIcon(getMarkerIcon(m._site, selectedMarkerIds.has(siteId)));
                        m.setZIndex(selectedMarkerIds.has(siteId) ? 999 : index);
                    }
                });
            });
            // Bind "Select All" button
            const selectAllBtn = infoContent.querySelector('.iw-select-all-btn');
            if (selectAllBtn) {
                selectAllBtn.addEventListener('click', () => {
                    const siteIds = selectAllBtn.getAttribute('data-site-ids').split(',');
                    const allSelected = siteIds.every(id => selectedMarkerIds.has(id));
                    siteIds.forEach(id => {
                        if (allSelected) {
                            selectedMarkerIds.delete(id);
                        } else {
                            selectedMarkerIds.add(id);
                        }
                        const m = homepageMarkers.find(mk => mk._siteId === id);
                        if (m) {
                            m.setIcon(getMarkerIcon(m._site, selectedMarkerIds.has(id)));
                            m.setZIndex(selectedMarkerIds.has(id) ? 999 : 0);
                        }
                    });
                    updateSelectedCampgrounds();
                    updateMapSelectionBadge();
                    // Update individual buttons
                    infoContent.querySelectorAll('.infowindow-select-btn').forEach(btn => {
                        const sid = btn.getAttribute('data-site-id');
                        btn.textContent = selectedMarkerIds.has(sid) ? 'Deselect' : 'Select';
                    });
                    selectAllBtn.textContent = siteIds.every(id => selectedMarkerIds.has(id))
                        ? `Deselect All ${siteIds.length}` : `Select All ${siteIds.length}`;
                });
            }
        });
    });

    return marker;
}

// Build InfoWindow HTML for a single campground
function buildSingleSiteInfoWindow(site) {
    const isRC = (site.provider || '').includes('California');
    const isCabin = (site.type || '').toLowerCase() === 'cabin';
    const bookingUrl = site.booking_url || (isRC
        ? 'https://www.reservecalifornia.com'
        : `https://www.recreation.gov/camping/campgrounds/${(site.id || '').replace('rg:', '')}`);
    let typeBadge;
    if (isRC) {
        typeBadge = `<a class="facility-type-badge rc" href="${bookingUrl}" target="_blank" rel="noopener">CA State Parks</a>`;
    } else if (isCabin) {
        typeBadge = `<a class="facility-type-badge cabin" href="${bookingUrl}" target="_blank" rel="noopener">Cabin</a>`;
    } else {
        typeBadge = `<a class="facility-type-badge campground" href="${bookingUrl}" target="_blank" rel="noopener">Recreation.gov</a>`;
    }
    const desc = truncate(site.description, 150);
    const imageUrl = site.image_url || '';
    const isSelected = selectedMarkerIds.has(site.id);

    const profileUrl = `/campground/${isRC ? 'rc' : 'rg'}/${(site.id || '').replace(/^(rg:|rc:)/, '')}`;

    return `
        ${imageUrl ? `<div class="iw-image"><img src="${imageUrl}" alt="${site.name}" /></div>` : ''}
        <div class="iw-body">
            <div class="iw-header">
                <strong class="iw-name">${site.name}</strong>
                ${typeBadge}
            </div>
            ${desc ? `<p class="iw-desc">${desc}</p>` : ''}
            <div class="iw-actions">
                <button class="infowindow-select-btn" data-site-id="${site.id}">${isSelected ? 'Deselect' : 'Select'}</button>
                <a class="iw-details-link" href="${profileUrl}">
                    View details <i class="fas fa-arrow-right"></i>
                </a>
            </div>
        </div>`;
}

// Build InfoWindow HTML for multiple co-located campgrounds
function buildMultiSiteInfoWindow(sites) {
    // Use first site's image if available
    const firstWithImage = sites.find(s => s.image_url);
    const imageUrl = firstWithImage ? firstWithImage.image_url : '';
    const allIds = sites.map(s => s.id);
    const allSelected = allIds.every(id => selectedMarkerIds.has(id));

    let sitesHtml = sites.map(s => {
        const isRC = (s.provider || '').includes('California');
        const isCabin = (s.type || '').toLowerCase() === 'cabin';
        const bookingUrl = s.booking_url || (isRC
            ? 'https://www.reservecalifornia.com'
            : `https://www.recreation.gov/camping/campgrounds/${(s.id || '').replace('rg:', '')}`);
        let badge;
        if (isRC) badge = `<a class="facility-type-badge rc" href="${bookingUrl}" target="_blank" rel="noopener" style="font-size:0.6rem">CA Parks</a>`;
        else if (isCabin) badge = `<a class="facility-type-badge cabin" href="${bookingUrl}" target="_blank" rel="noopener" style="font-size:0.6rem">Cabin</a>`;
        else badge = `<a class="facility-type-badge campground" href="${bookingUrl}" target="_blank" rel="noopener" style="font-size:0.6rem">Rec.gov</a>`;
        const profileUrl = `/campground/${isRC ? 'rc' : 'rg'}/${(s.id || '').replace(/^(rg:|rc:)/, '')}`;
        const isSelected = selectedMarkerIds.has(s.id);

        return `
            <div class="iw-multi-site">
                <div class="iw-multi-row">
                    <div class="iw-multi-info">
                        <strong class="iw-name">${s.name}</strong>
                        ${badge}
                    </div>
                    <button class="infowindow-select-btn" data-site-id="${s.id}">${isSelected ? 'Deselect' : 'Select'}</button>
                </div>
                <div class="iw-multi-links">
                    <a class="iw-details-link" href="${profileUrl}">Details <i class="fas fa-arrow-right"></i></a>
                </div>
            </div>`;
    }).join('');

    return `
        ${imageUrl ? `<div class="iw-image"><img src="${imageUrl}" alt="Campgrounds" /></div>` : ''}
        <div class="iw-body">
            <div class="iw-multi-header">
                <strong>${sites.length} campgrounds at this location</strong>
                <button class="iw-select-all-btn" data-site-ids="${allIds.join(',')}">${allSelected ? `Deselect All ${sites.length}` : `Select All ${sites.length}`}</button>
            </div>
            ${sitesHtml}
        </div>`;
}

// ========================================
// Toggle campsite selection
// ========================================
function toggleCampsiteSelection(siteId) {
    // Always update the canonical Set first
    if (selectedMarkerIds.has(siteId)) {
        selectedMarkerIds.delete(siteId);
    } else {
        selectedMarkerIds.add(siteId);
    }

    // Sync the checkbox if it exists in the list panel
    const cb = document.querySelector(`.campsite-checkbox[value="${siteId}"]`);
    if (cb) {
        cb.checked = selectedMarkerIds.has(siteId);
        const item = cb.closest('.campsite-item');
        if (item) {
            if (cb.checked) {
                item.classList.add('selected');
            } else {
                item.classList.remove('selected');
            }
            if (window.innerWidth > 768) {
                item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
            item.classList.add('flash-highlight');
            setTimeout(() => item.classList.remove('flash-highlight'), 1200);
        }
    }

    updateSelectedCampgrounds();
    updateMapSelectionBadge();
}

// ========================================
// Update mobile floating selection badge
// ========================================
function updateMapSelectionBadge() {
    const badge = document.getElementById('mapSelectionBadge');
    const countEl = document.getElementById('mapSelectionCount');
    if (!badge || !countEl) return;

    const count = selectedMarkerIds.size;
    const isMobile = window.innerWidth <= 768;
    const hasCampgrounds = currentCampsites && currentCampsites.length > 0;

    if (isMobile && hasCampgrounds) {
        // On mobile: always show badge when campgrounds are loaded
        badge.style.display = 'flex';
        if (count > 0) {
            countEl.parentElement.querySelector('#mapSelectionCount').textContent = count;
            // Show "N selected · View List"
            badge.innerHTML = `<span>${count} selected</span><button type="button" id="mapViewListBtn" class="map-view-list-btn">View List</button>`;
        } else {
            // Show just "View List"
            badge.innerHTML = `<span>${currentCampsites.length} campgrounds</span><button type="button" id="mapViewListBtn" class="map-view-list-btn">View List</button>`;
        }
        // Re-bind View List button
        const vlBtn = document.getElementById('mapViewListBtn');
        if (vlBtn) {
            vlBtn.addEventListener('click', () => {
                const panel = document.getElementById('campsiteListPanel');
                if (!panel) return;
                if (panel.classList.contains('mobile-expanded')) {
                    hideListPanel();
                    vlBtn.textContent = 'View List';
                } else {
                    if (!panel.hasChildNodes() || panel.children.length === 0) {
                        if (currentCampsites.length > 0) {
                            populateCampsiteList(currentCampsites, homepageMap, 'Campgrounds in this area');
                        }
                    }
                    showListPanel();
                    vlBtn.textContent = 'Hide List';
                }
            });
        }
    } else if (count > 0) {
        countEl.textContent = count;
        badge.style.display = 'flex';
    } else {
        badge.style.display = 'none';
    }

    // Update bottom sheet peek label
    const peekLabel = document.querySelector('.peek-label');
    if (peekLabel) {
        peekLabel.textContent = count > 0
            ? `${count} campground${count > 1 ? 's' : ''} selected`
            : 'Search options';
    }
}

// ========================================
// Sync list panel checkboxes to selectedMarkerIds
// ========================================
function syncListToSelections() {
    document.querySelectorAll('.campsite-checkbox').forEach(cb => {
        const shouldBeChecked = selectedMarkerIds.has(cb.value);
        if (cb.checked !== shouldBeChecked) {
            cb.checked = shouldBeChecked;
            const item = cb.closest('.campsite-item');
            if (item) {
                if (shouldBeChecked) {
                    item.classList.add('selected');
                } else {
                    item.classList.remove('selected');
                }
            }
        }
    });
}

// ========================================
// Populate campsite list into #campsiteListPanel
// ========================================
function populateCampsiteList(campsites, map, locationLabel) {
    currentCampsites = campsites;

    // Clear old markers
    homepageMarkers.forEach(m => m.setMap(null));
    homepageMarkers = [];

    // Prune stale selections: remove IDs that aren't in the new campsite list
    const newIds = new Set(campsites.map(s => s.id));
    selectedMarkerIds.forEach(id => {
        if (!newIds.has(id)) selectedMarkerIds.delete(id);
    });

    // Get the list panel
    const panel = document.getElementById('campsiteListPanel');
    if (!panel) return;

    panel.innerHTML = '';

    const isMobile = window.innerWidth <= 768;
    if (isMobile) {
        // On mobile: keep list hidden, user opens via "View List" button
        panel.style.display = 'none';
        panel.classList.remove('mobile-expanded');
    } else {
        panel.style.display = 'block';
    }

    // Sync "View List" button text
    const vlBtn = document.getElementById('mapViewListBtn');
    if (vlBtn) vlBtn.textContent = isMobile ? 'View List' : 'Hide List';

    // Header
    const header = document.createElement('div');
    header.className = 'campsite-header';
    header.innerHTML = `
        <button type="button" class="list-close-btn" onclick="hideListPanel()" title="Close list"><i class="fas fa-times"></i></button>
        <h3>${locationLabel}</h3>
        <span class="campsite-count">${campsites.length} campground${campsites.length !== 1 ? 's' : ''}</span>
        <span class="click-note">Select campgrounds to search availability</span>
        <button type="button" class="select-all-btn" onclick="selectAllCampgrounds()">Select All</button>
    `;
    panel.appendChild(header);

    // Create markers + list items
    campsites.forEach((site, idx) => {
        const marker = createLabeledMarker(map, site, idx);
        homepageMarkers.push(marker);

        const siteElement = document.createElement('div');
        siteElement.className = 'campsite-item';
        siteElement.setAttribute('data-park-id', site.id);
        siteElement.setAttribute('data-park-name', site.name);

        const isRC = (site.provider || '').includes('California');
        const isCabin = (site.type || '').toLowerCase() === 'cabin';

        let badgeHtml;
        if (isRC) {
            badgeHtml = '<span class="facility-type-badge rc">CA Parks</span>';
        } else if (isCabin) {
            badgeHtml = '<span class="facility-type-badge cabin">Cabin</span>';
        } else {
            badgeHtml = '<span class="facility-type-badge campground">Rec.gov</span>';
        }

        const numColor = isRC ? '#2563EB' : (isCabin ? '#D4870E' : '#1A5C4C');

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'campsite-checkbox';
        checkbox.value = site.id;
        checkbox.setAttribute('data-name', site.name);
        // Restore selection state
        if (selectedMarkerIds.has(site.id)) checkbox.checked = true;

        const siteInfo = document.createElement('div');
        siteInfo.className = 'campsite-info';
        siteInfo.innerHTML = `
            <span class="campsite-number" style="background:${numColor};">${idx + 1}</span>
            <span class="campsite-name">${site.name}</span>
            ${badgeHtml}
        `;

        siteElement.appendChild(checkbox);
        siteElement.appendChild(siteInfo);

        if (checkbox.checked) siteElement.classList.add('selected');

        siteElement.addEventListener('click', function(e) {
            if (e.target === checkbox) return;
            checkbox.checked = !checkbox.checked;
            checkbox.dispatchEvent(new Event('change'));
        });

        checkbox.addEventListener('change', function() {
            // Update the canonical set
            if (this.checked) {
                selectedMarkerIds.add(site.id);
                siteElement.classList.add('selected');
            } else {
                selectedMarkerIds.delete(site.id);
                siteElement.classList.remove('selected');
            }
            // Update marker appearance
            const m = homepageMarkers.find(mk => mk._siteId === site.id);
            if (m) {
                m.setIcon(getMarkerIcon(site, this.checked));
                m.setZIndex(this.checked ? 999 : idx);
            }
            updateSelectedCampgrounds();
            updateMapSelectionBadge();
        });

        panel.appendChild(siteElement);
    });

    // Update badge (shows "View List" on mobile even with 0 selected)
    updateMapSelectionBadge();
}

// ========================================
// Load campgrounds at a given center
// ========================================
function loadCampgroundsAtCenter(lat, lng, locationLabel, panMap, filterBounds) {
    lastLoadedCenter = { lat, lng };
    // Save the bounds at search time so we can detect viewport changes later
    if (homepageMap) {
        lastLoadedBounds = homepageMap.getBounds();
    }

    const searchAreaBtn = document.getElementById('searchAreaBtn');
    if (searchAreaBtn) searchAreaBtn.style.display = 'none';

    if (panMap && homepageMap) {
        homepageMap.panTo({ lat, lng });
        homepageMap.setZoom(9);
    }

    fetchCampsites(lat, lng).then(data => {
        if (data.success && homepageMap) {
            let campsites = data.campsites;

            // If filterBounds provided, only show campgrounds within the visible map area
            if (filterBounds) {
                campsites = campsites.filter(s => {
                    const sLat = parseFloat(s.latitude);
                    const sLng = parseFloat(s.longitude);
                    return filterBounds.contains({ lat: sLat, lng: sLng });
                });
            }

            populateCampsiteList(campsites, homepageMap, locationLabel);

            if (campsites.length > 0 && panMap) {
                const bounds = new google.maps.LatLngBounds();
                campsites.forEach(s => {
                    bounds.extend({ lat: parseFloat(s.latitude), lng: parseFloat(s.longitude) });
                });
                homepageMap.fitBounds(bounds);
            }
        }
    }).catch(err => {
        console.error('Error fetching campsites:', err);
        const searchAreaBtn = document.getElementById('searchAreaBtn');
        if (searchAreaBtn) searchAreaBtn.style.display = 'flex';
    });
}

// ========================================
// Load campgrounds by name search
// ========================================
function loadCampgroundsByName(query) {
    fetchCampsitesByName(query).then(data => {
        if (data.success && data.campsites.length > 0 && homepageMap) {
            populateCampsiteList(data.campsites, homepageMap, `Results for "${query}"`);

            const bounds = new google.maps.LatLngBounds();
            data.campsites.forEach(s => {
                if (s.latitude && s.longitude) {
                    bounds.extend({ lat: parseFloat(s.latitude), lng: parseFloat(s.longitude) });
                }
            });
            if (!bounds.isEmpty()) homepageMap.fitBounds(bounds);
        } else if (data.success && data.campsites.length === 0) {
            const panel = document.getElementById('campsiteListPanel');
            if (panel) {
                panel.innerHTML = '<div class="no-results-msg">No campgrounds found matching "' + query + '"</div>';
                panel.style.display = 'block';
            }
        }
    }).catch(err => console.error('Name search error:', err));
}

// ========================================
// Initialize homepage map
// ========================================
function initHomepageMap() {
    const mapEl = document.getElementById('homepageMap');
    if (!mapEl) return;

    const defaultLat = 37.77;
    const defaultLng = -122.42;

    homepageMap = new google.maps.Map(mapEl, {
        center: { lat: defaultLat, lng: defaultLng },
        zoom: 10,
        mapTypeId: 'terrain',
        mapTypeControl: true,
        zoomControl: true,
        scrollwheel: true,
        gestureHandling: 'greedy'
    });

    // Load initial campgrounds (pins only, no list panel)
    lastLoadedCenter = { lat: defaultLat, lng: defaultLng };

    fetchCampsites(defaultLat, defaultLng).then(data => {
        if (data.success && homepageMap) {
            let campsites = data.campsites;
            const bounds = homepageMap.getBounds();
            if (bounds) {
                campsites = campsites.filter(s =>
                    bounds.contains({ lat: parseFloat(s.latitude), lng: parseFloat(s.longitude) })
                );
            }
            lastLoadedBounds = homepageMap.getBounds();
            populateCampsiteList(campsites, homepageMap, 'Nearby campgrounds');
        }
    });

    // Show "Search this area" when user pans or zooms away from last searched viewport
    homepageMap.addListener('idle', () => {
        if (!lastLoadedCenter) return;
        const searchAreaBtn = document.getElementById('searchAreaBtn');
        if (!searchAreaBtn) return;

        const currentBounds = homepageMap.getBounds();
        if (!currentBounds) return;

        // If we have saved bounds, check if the viewport has meaningfully changed
        if (lastLoadedBounds) {
            const oldNE = lastLoadedBounds.getNorthEast();
            const oldSW = lastLoadedBounds.getSouthWest();
            const newNE = currentBounds.getNorthEast();
            const newSW = currentBounds.getSouthWest();

            // Check if the center has moved by more than 20% of the viewport span,
            // or if the zoom level has changed significantly
            const latSpan = Math.abs(oldNE.lat() - oldSW.lat());
            const lngSpan = Math.abs(oldNE.lng() - oldSW.lng());
            const centerShiftLat = Math.abs(newNE.lat() + newSW.lat() - oldNE.lat() - oldSW.lat()) / 2;
            const centerShiftLng = Math.abs(newNE.lng() + newSW.lng() - oldNE.lng() - oldSW.lng()) / 2;
            const zoomChanged = Math.abs((newNE.lat() - newSW.lat()) - latSpan) > latSpan * 0.3;

            const panThreshold = 0.2; // 20% of viewport
            const hasPanned = centerShiftLat > latSpan * panThreshold || centerShiftLng > lngSpan * panThreshold;

            searchAreaBtn.style.display = (hasPanned || zoomChanged) ? 'flex' : 'none';
        } else {
            // No saved bounds — fall back to distance check
            const center = homepageMap.getCenter();
            const dist = haversineMiles(lastLoadedCenter.lat, lastLoadedCenter.lng, center.lat(), center.lng());
            searchAreaBtn.style.display = dist > 5 ? 'flex' : 'none';
        }
    });

    const searchAreaBtn = document.getElementById('searchAreaBtn');
    if (searchAreaBtn) {
        searchAreaBtn.addEventListener('click', () => {
            const center = homepageMap.getCenter();
            loadCampgroundsAtCenter(center.lat(), center.lng(), 'Campgrounds in this area', false, homepageMap.getBounds());
        });
    }

    // "View List" / "Hide List" button (works on both desktop and mobile)
    const viewListBtn = document.getElementById('mapViewListBtn');
    if (viewListBtn) {
        viewListBtn.addEventListener('click', () => {
            const panel = document.getElementById('campsiteListPanel');
            if (!panel) return;

            // Check if currently visible
            const isVisible = panel.style.display === 'block';
            if (isVisible) {
                hideListPanel();
            } else {
                // If the panel has no content, rebuild from current campsites
                if (!panel.hasChildNodes() || panel.children.length === 0) {
                    if (currentCampsites.length > 0) {
                        populateCampsiteList(currentCampsites, homepageMap, 'Campgrounds in this area');
                        return; // populateCampsiteList already shows the panel
                    }
                }
                showListPanel();
            }
        });
    }
}

// ========================================
// Preference pills
// ========================================
function initPreferencePills() {
    const pills = document.querySelectorAll('.preference-pill');
    const radios = document.querySelectorAll('input[name="searchPreference"]');

    // Use change event on radios for reliable single-select
    radios.forEach(radio => {
        radio.addEventListener('change', () => {
            pills.forEach(p => p.classList.remove('active'));
            const parentPill = radio.closest('.preference-pill');
            if (parentPill) parentPill.classList.add('active');
        });
    });

    // Sync initial state
    pills.forEach(pill => {
        const radio = pill.querySelector('input[type="radio"]');
        if (radio && radio.checked) {
            pills.forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
        }
    });
}

// ========================================
// Hide campsite list panel (close button)
// ========================================
window.hideListPanel = function() {
    const panel = document.getElementById('campsiteListPanel');
    if (panel) {
        // Use class toggle only — avoid fighting with CSS !important rules
        panel.classList.remove('mobile-expanded');
        panel.style.display = 'none';
    }
    const vlBtn = document.getElementById('mapViewListBtn');
    if (vlBtn) vlBtn.textContent = 'View List';
};

window.showListPanel = function() {
    const panel = document.getElementById('campsiteListPanel');
    if (panel) {
        panel.style.display = 'block';
        panel.classList.add('mobile-expanded');
        syncListToSelections();
    }
    const vlBtn = document.getElementById('mapViewListBtn');
    if (vlBtn) vlBtn.textContent = 'Hide List';
};

// ========================================
// Bottom Sheet for mobile
// ========================================
function initBottomSheet() {
    const sheet = document.getElementById('bottomSheet');
    const handle = document.getElementById('bottomSheetHandle');
    const content = document.getElementById('bottomSheetContent');
    const peek = document.getElementById('bottomSheetPeek');
    if (!sheet || !handle) return;

    // Only activate touch drag on mobile
    const isMobile = () => window.innerWidth <= 768;

    let startY = 0;
    let startTranslate = 0;
    let currentTranslate = 0;
    let dragging = false;

    // Sheet states: 'peek' (just handle), 'half' (form visible), 'full' (results)
    let sheetState = 'peek';

    function getSheetHeight() {
        return sheet.offsetHeight;
    }

    function setSheetState(state) {
        if (!isMobile()) return;
        sheetState = state;
        sheet.style.transition = 'transform 0.3s cubic-bezier(0.2, 0, 0, 1)';

        const vh = window.innerHeight;
        if (state === 'peek') {
            // Show just the handle bar (~56px)
            sheet.style.transform = `translateY(calc(100% - 56px))`;
            content.style.overflowY = 'hidden';
            peek.querySelector('i').className = 'fas fa-chevron-up';
        } else if (state === 'half') {
            // Show ~45% of viewport
            sheet.style.transform = `translateY(55%)`;
            content.style.overflowY = 'hidden';
            peek.querySelector('i').className = 'fas fa-chevron-up';
        } else if (state === 'full') {
            // Full height
            sheet.style.transform = `translateY(0)`;
            content.style.overflowY = 'auto';
            peek.querySelector('i').className = 'fas fa-chevron-down';
        }
    }

    // Tap on handle toggles states
    handle.addEventListener('click', (e) => {
        if (!isMobile()) return;
        if (dragging) return;
        if (sheetState === 'peek') {
            setSheetState('half');
        } else if (sheetState === 'half') {
            setSheetState('full');
        } else {
            setSheetState('peek');
        }
    });

    // Touch drag
    handle.addEventListener('touchstart', (e) => {
        if (!isMobile()) return;
        dragging = false;
        startY = e.touches[0].clientY;
        const transform = window.getComputedStyle(sheet).transform;
        const matrix = new DOMMatrix(transform);
        startTranslate = matrix.m42; // translateY value
        sheet.style.transition = 'none';
    }, { passive: true });

    handle.addEventListener('touchmove', (e) => {
        if (!isMobile()) return;
        dragging = true;
        const deltaY = e.touches[0].clientY - startY;
        currentTranslate = Math.max(0, startTranslate + deltaY);
        sheet.style.transform = `translateY(${currentTranslate}px)`;
    }, { passive: true });

    handle.addEventListener('touchend', (e) => {
        if (!isMobile()) return;
        if (!dragging) return; // was a tap, handled by click
        sheet.style.transition = 'transform 0.3s cubic-bezier(0.2, 0, 0, 1)';

        const vh = window.innerHeight;
        const sheetH = getSheetHeight();
        const visiblePct = 1 - (currentTranslate / sheetH);

        // Snap to nearest state
        if (visiblePct > 0.7) {
            setSheetState('full');
        } else if (visiblePct > 0.25) {
            setSheetState('half');
        } else {
            setSheetState('peek');
        }
        dragging = false;
    });

    // Initialize on mobile
    if (isMobile()) {
        setSheetState('peek');
    }

    // Re-check on resize
    window.addEventListener('resize', () => {
        if (isMobile()) {
            if (!sheet.classList.contains('mobile-active')) {
                sheet.classList.add('mobile-active');
                setSheetState('peek');
            }
        } else {
            sheet.classList.remove('mobile-active');
            sheet.style.transform = '';
            sheet.style.transition = '';
            content.style.overflowY = '';
        }
    });

    if (isMobile()) {
        sheet.classList.add('mobile-active');
    }

    // Expose for other code to open sheet
    window.openBottomSheet = function(state) {
        if (isMobile()) setSheetState(state || 'half');
    };
}

// ========================================
// DOMContentLoaded
// ========================================
document.addEventListener('DOMContentLoaded', function() {
    const fillParkId = sessionStorage.getItem('fillParkId');
    if (fillParkId) {
        document.getElementById('parkId').value = fillParkId;
        sessionStorage.removeItem('fillParkId');
    }

    const searchForm = document.getElementById('searchForm');

    // URL parameter handling for history "Search Again"
    if (searchForm) {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.has('parkId')) {
            document.getElementById('parkId').value = urlParams.get('parkId');
            if (urlParams.has('startDate')) document.getElementById('startDate').value = urlParams.get('startDate');
            if (urlParams.has('endDate')) document.getElementById('endDate').value = urlParams.get('endDate');
            if (urlParams.has('nights')) document.getElementById('nights').value = urlParams.get('nights');

            if (urlParams.has('searchPreference')) {
                const preference = urlParams.get('searchPreference');
                const radioButton = document.querySelector(`input[name="searchPreference"][value="${preference}"]`);
                if (radioButton) {
                    radioButton.checked = true;
                    const pill = radioButton.closest('.preference-pill');
                    if (pill) {
                        document.querySelectorAll('.preference-pill').forEach(p => p.classList.remove('active'));
                        pill.classList.add('active');
                    }
                }
            }

            if (window.handleCitySearch) window.handleCitySearch();

            if (urlParams.has('campgroundName')) {
                const parkIdField = document.getElementById('parkId');
                if (parkIdField) {
                    parkIdField.setAttribute('data-campground-name', urlParams.get('campgroundName'));
                    if (urlParams.has('city')) parkIdField.setAttribute('data-city', urlParams.get('city'));
                }
            }

            if (window.location.hash === '#results') {
                setTimeout(() => {
                    if (searchForm) searchForm.dispatchEvent(new Event('submit'));
                }, 500);
            }
        }
    }

    // Google Places Autocomplete + campground name search
    if (document.getElementById('citySearch')) {
        let selectedPlace = null;
        const autocomplete = new google.maps.places.Autocomplete(
            document.getElementById('citySearch'),
            {
                types: ['(cities)'],
                componentRestrictions: { country: 'us' },
                fields: ['place_id', 'geometry', 'formatted_address', 'name']
            }
        );

        autocomplete.addListener('place_changed', function() {
            const place = autocomplete.getPlace();
            if (place.geometry) selectedPlace = place;
        });

        // Search button: city OR campground name search
        document.querySelector('.search-city-button').addEventListener('click', function() {
            const inputVal = document.getElementById('citySearch').value.trim();

            if (selectedPlace && selectedPlace.geometry) {
                // City was selected from autocomplete
                const lat = selectedPlace.geometry.location.lat();
                const lng = selectedPlace.geometry.location.lng();
                loadCampgroundsAtCenter(lat, lng, `Campsites near ${selectedPlace.formatted_address}`, true);
                selectedPlace = null; // reset for next search

                if (window.handleCitySearch) window.handleCitySearch();
            } else if (inputVal.length >= 2) {
                // No autocomplete place selected — treat as campground name search
                loadCampgroundsByName(inputVal);

                if (window.handleCitySearch) window.handleCitySearch();
            } else {
                alert('Please enter a city name or campground name to search');
            }
        });

        // Also trigger search on Enter key
        document.getElementById('citySearch').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                document.querySelector('.search-city-button').click();
            }
        });
    }

    // ========================================
    // Update selected campgrounds display
    // ========================================
    window.updateSelectedCampgrounds = function() {
        const selectedCampgroundDiv = document.getElementById('selectedCampground');
        const campgroundNameDisplay = document.getElementById('campgroundNameDisplay');
        const allDatesPill = document.querySelector('.preference-pill[data-value="all"]');
        const allDatesOption = document.querySelector('input[name="searchPreference"][value="all"]');

        // selectedMarkerIds is the single source of truth
        const totalSelected = selectedMarkerIds.size;

        if (totalSelected === 0) {
            if (selectedCampgroundDiv) selectedCampgroundDiv.style.display = 'none';
            document.getElementById('parkId').value = '';
            if (allDatesPill) allDatesPill.classList.remove('disabled');
            if (allDatesOption) allDatesOption.disabled = false;
            return;
        }

        // Build IDs and names from the canonical set
        const selectedIds = Array.from(selectedMarkerIds);
        const selectedNames = selectedIds.map(id => {
            const cb = document.querySelector(`.campsite-checkbox[value="${id}"]`);
            if (cb) return cb.getAttribute('data-name') || id;
            // Fallback: look up from marker data
            const marker = homepageMarkers.find(m => m._siteId === id);
            return marker ? marker._site.name : id;
        });

        document.getElementById('parkId').value = selectedIds.join(',');

        // Disable "all dates" if >8
        if (selectedIds.length > 8) {
            if (allDatesPill) allDatesPill.classList.add('disabled');
            if (allDatesOption) {
                allDatesOption.disabled = true;
                if (allDatesOption.checked) {
                    const flexibleOption = document.querySelector('input[name="searchPreference"][value="flexible"]');
                    if (flexibleOption) {
                        flexibleOption.checked = true;
                        document.querySelectorAll('.preference-pill').forEach(p => p.classList.remove('active'));
                        const flexPill = document.querySelector('.preference-pill[data-value="flexible"]');
                        if (flexPill) flexPill.classList.add('active');
                    }
                }
            }
        } else {
            if (allDatesPill) allDatesPill.classList.remove('disabled');
            if (allDatesOption) allDatesOption.disabled = false;
        }

        if (selectedCampgroundDiv && campgroundNameDisplay) {
            const displayText = selectedIds.length === 1
                ? (selectedNames[0] || 'Selected campground')
                : `${selectedIds.length} campgrounds selected`;
            campgroundNameDisplay.textContent = displayText;
            selectedCampgroundDiv.style.display = 'block';
        }

        // Show form fields
        const formGroups = document.querySelectorAll('.form-group:not(.city-search-group)');
        const searchButton = document.querySelector('.search-button');
        formGroups.forEach(group => group.classList.add('show'));
        if (searchButton) searchButton.classList.add('show');

        // Open bottom sheet to half on mobile so user sees the form
        if (window.openBottomSheet) window.openBottomSheet('half');

        if (window.innerWidth > 768) {
            setTimeout(() => {
                const datesRow = document.querySelector('.form-row');
                if (datesRow) datesRow.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }, 200);
        }
    };

    // ========================================
    // Select all campgrounds
    // ========================================
    window.selectAllCampgrounds = function() {
        const checkboxes = document.querySelectorAll('.campsite-checkbox');
        const allChecked = Array.from(checkboxes).every(cb => cb.checked);

        if (!allChecked && checkboxes.length > 10) {
            const confirmed = confirm(
                `You're about to select ${checkboxes.length} campgrounds.\n\n` +
                `"All dates" option will be disabled.\n\nContinue?`
            );
            if (!confirmed) return;
        }

        checkboxes.forEach(cb => {
            cb.checked = !allChecked;
            const item = cb.closest('.campsite-item');
            if (cb.checked) {
                item.classList.add('selected');
                selectedMarkerIds.add(cb.value);
            } else {
                item.classList.remove('selected');
                selectedMarkerIds.delete(cb.value);
            }
        });

        // Update all markers
        homepageMarkers.forEach(m => {
            const isNowSelected = selectedMarkerIds.has(m._siteId);
            m.setIcon(getMarkerIcon(m._site, isNowSelected));
            m.setZIndex(isNowSelected ? 999 : 0);
        });

        updateSelectedCampgrounds();
        updateMapSelectionBadge();

        const btn = document.querySelector('.select-all-btn');
        if (btn) btn.textContent = allChecked ? 'Select All' : 'Deselect All';
    };

    // ========================================
    // Init on index page
    // ========================================
    if (searchForm) {
        setDefaultDates();
        initHomepageMap();
        initPreferencePills();
    }

    // ========================================
    // Bottom Sheet (mobile pull-up panel)
    // ========================================
    initBottomSheet();

    // Set default dates
    function setDefaultDates() {
        const today = new Date();
        const dayOfWeek = today.getDay();
        let daysUntilFriday = (5 - dayOfWeek + 7) % 7;
        if (daysUntilFriday === 0) daysUntilFriday = 7;

        const nextFriday = new Date();
        nextFriday.setDate(today.getDate() + daysUntilFriday);

        const threeMonthsLater = new Date(nextFriday);
        threeMonthsLater.setMonth(threeMonthsLater.getMonth() + 3);

        const formatDate = (date) => {
            const year = date.getFullYear();
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            return `${year}-${month}-${day}`;
        };

        const minDate = formatDate(today);
        document.getElementById('startDate').min = minDate;
        document.getElementById('endDate').min = minDate;
        document.getElementById('startDate').value = formatDate(nextFriday);
        document.getElementById('endDate').value = formatDate(threeMonthsLater);
    }
});
