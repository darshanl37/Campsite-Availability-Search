/**
 * CalendarGrid — renders a monthly calendar showing campsite availability.
 *
 * Usage:
 *   new CalendarGrid(container, {
 *     parkName: "Kirby Cove",
 *     parkId:   "232447",
 *     provider: "RecreationGov",  // or "ReserveCalifornia"
 *     dates:    { "2025-08-15": { count: 3, type: "priority", checkout: "2025-08-17" }, ... },
 *     searchStart: "2025-08-01",
 *     searchEnd:   "2025-09-30",
 *     nights: 2,
 *   });
 */
class CalendarGrid {
  constructor(container, opts) {
    this.container = container;
    this.parkName = opts.parkName;
    this.parkId = opts.parkId;
    this.provider = opts.provider || 'RecreationGov';
    this.dates = opts.dates || {};
    this.searchStart = new Date(opts.searchStart + 'T00:00:00');
    this.searchEnd = new Date(opts.searchEnd + 'T00:00:00');
    this.nights = opts.nights || 1;

    const dateKeys = Object.keys(this.dates).sort();
    if (dateKeys.length > 0) {
      const first = new Date(dateKeys[0] + 'T00:00:00');
      this.viewYear = first.getFullYear();
      this.viewMonth = first.getMonth();
    } else {
      this.viewYear = this.searchStart.getFullYear();
      this.viewMonth = this.searchStart.getMonth();
    }

    this.render();
  }

  _fmt(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  _monthName(m) {
    return [
      'January','February','March','April','May','June',
      'July','August','September','October','November','December'
    ][m];
  }

  _canPrev() {
    const first = new Date(this.searchStart);
    return (this.viewYear > first.getFullYear()) ||
           (this.viewYear === first.getFullYear() && this.viewMonth > first.getMonth());
  }

  _canNext() {
    const last = new Date(this.searchEnd);
    return (this.viewYear < last.getFullYear()) ||
           (this.viewYear === last.getFullYear() && this.viewMonth < last.getMonth());
  }

  _bookingUrl(checkin, checkout) {
    const rawId = this.parkId.replace(/^(rc:|rg:)/, '');
    if (this.provider === 'ReserveCalifornia' || this.parkId.startsWith('rc:')) {
      return 'https://www.reservecalifornia.com';
    }
    return `https://www.recreation.gov/camping/campgrounds/${rawId}?start=${checkin}&end=${checkout}`;
  }

  _headerUrl() {
    const rawId = this.parkId.replace(/^(rc:|rg:)/, '');
    if (this.provider === 'ReserveCalifornia' || this.parkId.startsWith('rc:')) {
      return 'https://www.reservecalifornia.com';
    }
    return `https://www.recreation.gov/camping/campgrounds/${rawId}`;
  }

  render() {
    this.container.innerHTML = '';

    const card = document.createElement('div');
    card.className = 'calendar-grid';

    // Header
    const header = document.createElement('div');
    header.className = 'cal-header';
    const link = document.createElement('a');
    link.href = this._headerUrl();
    link.target = '_blank';
    link.textContent = this.parkName;
    header.appendChild(link);

    // Provider badge
    if (this.provider === 'ReserveCalifornia' || this.parkId.startsWith('rc:')) {
      const badge = document.createElement('span');
      badge.className = 'facility-type-badge rc';
      badge.textContent = 'CA Parks';
      badge.style.marginLeft = '8px';
      header.appendChild(badge);
    }

    card.appendChild(header);

    // Nav
    const nav = document.createElement('div');
    nav.className = 'cal-nav';

    const prevBtn = document.createElement('button');
    prevBtn.className = 'cal-nav-btn';
    prevBtn.textContent = '\u2039';
    prevBtn.disabled = !this._canPrev();
    prevBtn.addEventListener('click', () => {
      if (!this._canPrev()) return;
      this.viewMonth--;
      if (this.viewMonth < 0) { this.viewMonth = 11; this.viewYear--; }
      this.render();
    });

    const nextBtn = document.createElement('button');
    nextBtn.className = 'cal-nav-btn';
    nextBtn.textContent = '\u203A';
    nextBtn.disabled = !this._canNext();
    nextBtn.addEventListener('click', () => {
      if (!this._canNext()) return;
      this.viewMonth++;
      if (this.viewMonth > 11) { this.viewMonth = 0; this.viewYear++; }
      this.render();
    });

    const title = document.createElement('span');
    title.className = 'cal-month-title';
    title.textContent = `${this._monthName(this.viewMonth)} ${this.viewYear}`;

    nav.appendChild(prevBtn);
    nav.appendChild(title);
    nav.appendChild(nextBtn);
    card.appendChild(nav);

    // Day-of-week headers
    const dowRow = document.createElement('div');
    dowRow.className = 'cal-days cal-dow-row';
    ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].forEach(d => {
      const cell = document.createElement('div');
      cell.className = 'cal-dow';
      cell.textContent = d;
      dowRow.appendChild(cell);
    });
    card.appendChild(dowRow);

    // Day cells
    const grid = document.createElement('div');
    grid.className = 'cal-days';

    const firstDay = new Date(this.viewYear, this.viewMonth, 1);
    const startDow = firstDay.getDay();
    const daysInMonth = new Date(this.viewYear, this.viewMonth + 1, 0).getDate();

    for (let i = 0; i < startDow; i++) {
      const blank = document.createElement('div');
      blank.className = 'cal-day blank';
      grid.appendChild(blank);
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const dt = new Date(this.viewYear, this.viewMonth, d);
      const key = this._fmt(dt);
      const info = this.dates[key];
      const cell = document.createElement('div');
      const inRange = dt >= this.searchStart && dt <= this.searchEnd;

      if (info && inRange) {
        cell.className = `cal-day available ${info.type}`;
        cell.innerHTML = `<span class="cal-date">${d}</span><span class="cal-count">${info.count}</span>`;
        cell.title = `${info.count} site(s) — check in ${key}, check out ${info.checkout}`;
        cell.addEventListener('click', () => {
          window.open(this._bookingUrl(key, info.checkout), '_blank');
        });
      } else {
        cell.className = `cal-day${inRange ? ' unavailable' : ' out-of-range'}`;
        cell.innerHTML = `<span class="cal-date">${d}</span>`;
      }

      grid.appendChild(cell);
    }

    card.appendChild(grid);
    this.container.appendChild(card);
  }
}

/**
 * Format a YYYY-MM-DD date string as "Fri, Aug 15".
 */
function _fmtReadable(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${days[d.getDay()]}, ${months[d.getMonth()]} ${d.getDate()}`;
}

/**
 * Map availability type to display labels.
 */
const TYPE_LABELS = {
  priority: 'Weekend',
  regular:  'Near Weekend',
  ignored:  'Midweek',
};

/**
 * Determine which types to show based on search preference.
 */
function getAllowedTypes(searchPreference) {
  switch (searchPreference) {
    case 'weekends': return ['priority'];
    case 'flexible': return ['priority', 'regular'];
    default:         return ['priority', 'regular', 'ignored'];
  }
}

/**
 * Get booking URL based on provider.
 */
function getBookingUrl(parkId, provider, checkin, checkout) {
  const rawId = parkId.replace(/^(rc:|rg:)/, '');
  if (provider === 'ReserveCalifornia' || parkId.startsWith('rc:')) {
    return 'https://www.reservecalifornia.com/CaliforniaWebHome/Facilities/SearchViewUnitAvailabity.aspx';
  }
  if (checkin && checkout) {
    return `https://www.recreation.gov/camping/campgrounds/${rawId}?start=${checkin}&end=${checkout}`;
  }
  return `https://www.recreation.gov/camping/campgrounds/${rawId}`;
}

/**
 * Render calendar grids for all campgrounds returned by the search API.
 *
 * @param {HTMLElement} container        The DOM element to render into.
 * @param {Object}      data             The API response.
 * @param {string}      searchPreference 'weekends', 'flexible', or 'all'.
 */
function renderCalendarResults(container, data, searchPreference) {
  container.innerHTML = '';

  const calendarData = data.calendar_data || {};
  const params = data.search_params || {};
  searchPreference = searchPreference || 'all';
  const allowedTypes = getAllowedTypes(searchPreference);

  if (Object.keys(calendarData).length === 0) {
    container.innerHTML = '<div class="no-results"><p>No availability found for the selected dates and criteria.</p></div>';
    return;
  }

  // Legend — always show all categories so calendar colors are explained
  const legend = document.createElement('div');
  legend.className = 'calendar-legend';
  legend.innerHTML =
    '<span class="legend-item"><span class="legend-swatch priority"></span> Weekend</span>' +
    '<span class="legend-item"><span class="legend-swatch regular"></span> Near Weekend</span>' +
    '<span class="legend-item"><span class="legend-swatch ignored"></span> Midweek</span>' +
    '<span class="legend-item"><span class="legend-swatch unavailable"></span> Unavailable</span>';
  container.appendChild(legend);

  // --- Build rows, filtered by allowed types ---
  const rows = [];
  let campgroundCount = 0;
  const typeCounts = { priority: 0, regular: 0, ignored: 0 };

  Object.entries(calendarData).forEach(([name, info]) => {
    const dates = info.dates || {};
    const provider = info.provider || 'RecreationGov';
    let hasAny = false;

    Object.keys(dates).sort().forEach(key => {
      const d = dates[key];
      if (!allowedTypes.includes(d.type)) return;

      typeCounts[d.type] = (typeCounts[d.type] || 0) + 1;
      hasAny = true;
      const checkin = new Date(key + 'T00:00:00');
      const dow = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][checkin.getDay()];
      rows.push({
        campground: name,
        parkId: info.park_id,
        provider,
        checkin: key,
        checkout: d.checkout,
        dow,
        count: d.count,
        type: d.type,
      });
    });

    if (hasAny) campgroundCount++;
  });

  // --- Summary bar: per-campground rows, each expandable ---
  const summaryBar = document.createElement('div');
  summaryBar.className = 'results-summary-bar';

  // Build per-campground type counts + grouped rows
  const perCampground = {};
  const grouped = {};
  rows.forEach(r => {
    if (!perCampground[r.campground]) {
      perCampground[r.campground] = { priority: 0, regular: 0, ignored: 0, provider: r.provider, parkId: r.parkId };
      grouped[r.campground] = [];
    }
    perCampground[r.campground][r.type]++;
    grouped[r.campground].push(r);
  });

  // Render per-campground expandable rows
  Object.entries(perCampground).forEach(([name, counts]) => {
    let chips = [];
    if (allowedTypes.includes('priority') && counts.priority > 0)
      chips.push(`<span class="summary-chip priority">${counts.priority} Weekend</span>`);
    if (allowedTypes.includes('regular') && counts.regular > 0)
      chips.push(`<span class="summary-chip regular">${counts.regular} Near Weekend</span>`);
    if (allowedTypes.includes('ignored') && counts.ignored > 0)
      chips.push(`<span class="summary-chip ignored">${counts.ignored} Midweek</span>`);
    const total = counts.priority + counts.regular + counts.ignored;
    const provider = counts.provider;
    const parkId = counts.parkId;
    const isRC = provider === 'ReserveCalifornia' || (parkId && parkId.startsWith('rc:'));
    const rawId = (parkId || '').replace(/^(rg:|rc:)/, '');
    const profileUrl = `/campground/${isRC ? 'rc' : 'rg'}/${rawId}`;
    const providerBadge = isRC
      ? ' <span class="facility-type-badge rc" style="font-size:0.6rem;">CA Parks</span>' : '';

    // Campground section wrapper
    const section = document.createElement('div');
    section.className = 'summary-campground-section';

    // Header row: name (linked to profile), chips, expand button
    const headerRow = document.createElement('div');
    headerRow.className = 'summary-campground-row';
    headerRow.innerHTML = `
      <div class="summary-campground-left">
        <a class="summary-campground-name" href="${profileUrl}" target="_blank" rel="noopener">${name}</a>${providerBadge}
        <div class="summary-counts">${chips.join('')}<span class="summary-row-total">${total} date${total !== 1 ? 's' : ''}</span></div>
      </div>
      <button class="summary-expand-btn" aria-expanded="false" title="Show dates"><i class="fas fa-plus"></i></button>
    `;
    section.appendChild(headerRow);

    // Detail rows (hidden by default)
    const detailBody = document.createElement('div');
    detailBody.className = 'summary-detail-body';
    detailBody.style.display = 'none';

    const cgRows = grouped[name] || [];

    cgRows.forEach(r => {
      const row = document.createElement('a');
      row.className = 'avail-row';
      row.href = getBookingUrl(r.parkId, r.provider, r.checkin, r.checkout);
      row.target = '_blank';
      row.rel = 'noopener';
      const typeLabel = TYPE_LABELS[r.type] || r.type;
      row.innerHTML = `
        <span class="avail-checkin">${_fmtReadable(r.checkin)}</span>
        <span class="avail-arrow">&rarr;</span>
        <span class="avail-checkout">${_fmtReadable(r.checkout)}</span>
        <span class="avail-sites">${r.count} site${r.count !== 1 ? 's' : ''}</span>
        <span class="avail-badge ${r.type}">${typeLabel}</span>
      `;
      detailBody.appendChild(row);
    });

    // Action links at bottom of detail
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'avail-detail-actions';

    // View details → profile page (new window)
    const detailsLink = document.createElement('a');
    detailsLink.className = 'avail-details-link';
    detailsLink.href = profileUrl;
    detailsLink.target = '_blank';
    detailsLink.rel = 'noopener';
    detailsLink.innerHTML = `View details <i class="fas fa-arrow-right"></i>`;
    actionsDiv.appendChild(detailsLink);

    // Book on external site (new tab)
    const bookingLink = document.createElement('a');
    bookingLink.className = 'avail-booking-link';
    bookingLink.href = getBookingUrl(parkId, provider);
    bookingLink.target = '_blank';
    bookingLink.rel = 'noopener';
    bookingLink.innerHTML = `Book on ${isRC ? 'reservecalifornia.com' : 'recreation.gov'} <i class="fas fa-external-link-alt"></i>`;
    actionsDiv.appendChild(bookingLink);

    detailBody.appendChild(actionsDiv);

    section.appendChild(detailBody);

    // Toggle expand
    const expandBtn = headerRow.querySelector('.summary-expand-btn');
    expandBtn.addEventListener('click', () => {
      const expanded = detailBody.style.display !== 'none';
      detailBody.style.display = expanded ? 'none' : '';
      expandBtn.innerHTML = expanded ? '<i class="fas fa-plus"></i>' : '<i class="fas fa-minus"></i>';
      expandBtn.setAttribute('aria-expanded', String(!expanded));
    });

    summaryBar.appendChild(section);
  });

  // Overall total footer
  const totalFiltered = rows.length;
  const overallDiv = document.createElement('div');
  overallDiv.className = 'summary-overall';
  overallDiv.innerHTML = `<span class="summary-total">${totalFiltered} date${totalFiltered !== 1 ? 's' : ''} across ${campgroundCount} campground${campgroundCount !== 1 ? 's' : ''}</span>`;
  summaryBar.appendChild(overallDiv);

  container.appendChild(summaryBar);

  // --- Calendar grids ---
  const wrapper = document.createElement('div');
  wrapper.className = 'calendar-comparison';

  Object.entries(calendarData).forEach(([name, info]) => {
    const allDates = info.dates || {};

    // Skip campground only if it has zero dates at all
    if (Object.keys(allDates).length === 0) return;

    const slot = document.createElement('div');
    slot.className = 'calendar-slot';
    wrapper.appendChild(slot);

    // Calendar always shows ALL dates (all types colored)
    new CalendarGrid(slot, {
      parkName: name,
      parkId: info.park_id,
      provider: info.provider || 'RecreationGov',
      dates: allDates,
      searchStart: params.start_date,
      searchEnd: params.end_date,
      nights: params.nights,
    });
  });

  container.appendChild(wrapper);

  // --- "Watch This Search" button (Coming Soon) ---
  const watchSection = document.createElement('div');
  watchSection.className = 'watch-search-section';

  const watchMsg = document.createElement('p');
  watchMsg.className = 'watch-search-msg';
  watchMsg.textContent = 'Want to know when new sites open up?';
  watchSection.appendChild(watchMsg);

  const watchBtn = document.createElement('button');
  watchBtn.className = 'watch-search-btn coming-soon';
  watchBtn.innerHTML = '<i class="fas fa-bell"></i> Watch This Search — Coming Soon';
  watchBtn.disabled = true;

  watchSection.appendChild(watchBtn);
  container.appendChild(watchSection);
}
