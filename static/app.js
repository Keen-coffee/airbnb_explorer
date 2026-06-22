(function () {
  const form = document.getElementById('search-form');
  const btn = document.getElementById('search-btn');
  const statusEl = document.getElementById('status');
  const resultsWrap = document.getElementById('results-wrap');
  const resultsHeader = document.getElementById('results-header');
  const tbody = document.getElementById('results-body');
  const apiBtn = document.getElementById('api-btn');
  const apiPanel = document.getElementById('api-panel');
  const apiUrlEl = document.getElementById('api-url');
  const apiCopy = document.getElementById('api-copy');

  let allListings = [];
  let sortKey = 'price';
  let sortDir = 'asc';

  function setStatus(msg, isError = false) {
    statusEl.textContent = msg;
    statusEl.className = isError ? 'error' : '';
  }

  function esc(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function formatPrice(l) {
    if (l.total_price_display) {
      // Parse out just the dollar amount and qualifier separately
      const m = l.total_price_display.match(/^(\$[\d,]+(?:\.\d+)?)\s*(.*)$/);
      if (m) {
        return `<div class="price-total">${esc(m[1])}</div><div class="price-qualifier">${esc(m[2])}</div>`;
      }
      return `<div class="price-total">${esc(l.total_price_display)}</div>`;
    }
    if (l.price_per_night != null) {
      return `<div class="price-total">$${Number(l.price_per_night).toFixed(0)}</div><div class="price-qualifier">per night</div>`;
    }
    return '<span class="no-price">—</span>';
  }

  function formatRating(l) {
    if (!l.avg_rating) return '<span class="no-rating">—</span>';
    return `<span class="rating-pill"><span class="star">★</span>${Number(l.avg_rating).toFixed(2)}</span>`;
  }

  function formatReviews(l) {
    if (l.review_count == null) return '<span class="no-rating">—</span>';
    return `<span class="review-count">${Number(l.review_count).toLocaleString()}</span>`;
  }

  function formatCoord(l) {
    if (l.latitude == null || l.longitude == null) return '<span style="color:var(--muted)">—</span>';
    const lat = Number(l.latitude).toFixed(2);
    const lng = Number(l.longitude).toFixed(2);
    const url = `https://www.google.com/maps?q=${l.latitude},${l.longitude}`;
    return `<a class="coord-link" href="${esc(url)}" target="_blank" rel="noopener">${lat}, ${lng}</a>`;
  }

  function dash(val) {
    return val ? esc(val) : '<span style="color:var(--muted)">—</span>';
  }

  function renderRows(listings) {
    if (!listings.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:32px">No listings found.</td></tr>';
      return;
    }
    tbody.innerHTML = listings.map((l, i) => `
      <tr>
        <td class="col-rank">${i + 1}</td>
        <td class="col-name">
          <a class="listing-cell" href="${esc(l.url)}" target="_blank" rel="noopener">
            ${l.image_url ? `<img class="listing-thumb" src="${esc(l.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : '<div class="listing-thumb listing-thumb-placeholder"></div>'}
            <div class="listing-name">${esc(l.name)}</div>
          </a>
        </td>
        <td class="col-bedrooms">${dash(l.bedrooms)}</td>
        <td class="col-beds">${dash(l.beds)}</td>
        <td class="col-baths">${dash(l.bathrooms)}</td>
        <td class="col-rating">${formatRating(l)}</td>
        <td class="col-reviews">${formatReviews(l)}</td>
        <td class="col-price">${formatPrice(l)}</td>
        <td class="col-coord">${formatCoord(l)}</td>
      </tr>
    `).join('');
  }

  function sortAndRender() {
    let listings = allListings;
    if (sortKey) {
      listings = [...allListings].sort((a, b) => {
        let av, bv;
        if (sortKey === 'price') {
          av = a.total_price ?? a.price_per_night ?? Infinity;
          bv = b.total_price ?? b.price_per_night ?? Infinity;
        } else if (sortKey === 'review_count') {
          av = a.review_count ?? -Infinity;
          bv = b.review_count ?? -Infinity;
        } else {
          av = a.avg_rating ?? -Infinity;
          bv = b.avg_rating ?? -Infinity;
        }
        return sortDir === 'asc' ? av - bv : bv - av;
      });
    }
    renderRows(listings);
    document.querySelectorAll('thead th[data-sort]').forEach(th => {
      th.classList.remove('sorted-asc', 'sorted-desc');
      if (sortKey && th.dataset.sort === sortKey) {
        th.classList.add(sortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
      }
    });
  }

  // Column header click to sort
  document.querySelectorAll('thead th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      if (sortKey === th.dataset.sort) {
        sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        sortKey = th.dataset.sort;
        sortDir = sortKey === 'price' ? 'asc' : 'desc';
      }
      sortAndRender();
    });
  });

  function showSkeletons() {
    tbody.innerHTML = Array.from({ length: 12 }).map(() => `
      <tr class="skeleton-row">
        <td><div class="skel" style="width:24px"></div></td>
        <td><div style="display:flex;align-items:center;gap:10px"><div class="skel" style="width:56px;height:56px;border-radius:6px;flex-shrink:0"></div><div class="skel" style="width:60%"></div></div></td>
        <td><div class="skel" style="width:60px"></div></td>
        <td><div class="skel" style="width:40px"></div></td>
        <td><div class="skel" style="width:40px"></div></td>
        <td><div class="skel" style="width:36px"></div></td>
        <td><div class="skel" style="width:44px"></div></td>
        <td><div class="skel" style="width:70px"></div></td>
        <td><div class="skel" style="width:80px"></div></td>
      </tr>
    `).join('');
    resultsWrap.classList.remove('hidden');
    resultsHeader.textContent = '';
  }

  // Auto-advance checkout to the day after check-in
  const checkinEl = document.getElementById('checkin');
  const checkoutEl = document.getElementById('checkout');
  checkinEl.addEventListener('change', function () {
    if (!this.value) return;
    const next = new Date(this.value + 'T00:00:00');
    next.setDate(next.getDate() + 1);
    const nextStr = next.toISOString().split('T')[0];
    if (!checkoutEl.value || checkoutEl.value <= this.value) {
      checkoutEl.value = nextStr;
    }
    checkoutEl.min = nextStr;
  });

  function buildQS() {
    const fd = new FormData(form);
    const p = new URLSearchParams();
    for (const [k, v] of fd.entries()) {
      if (v !== '' && v != null) p.append(k, v);
    }
    return p.toString();
  }

  // API panel toggle
  apiBtn.addEventListener('click', () => {
    const open = apiPanel.classList.toggle('hidden');
    apiBtn.classList.toggle('active', !open);
  });

  apiCopy.addEventListener('click', () => {
    navigator.clipboard.writeText(apiUrlEl.textContent).then(() => {
      apiCopy.textContent = 'Copied!';
      apiCopy.classList.add('copied');
      setTimeout(() => {
        apiCopy.textContent = 'Copy URL';
        apiCopy.classList.remove('copied');
      }, 1800);
    });
  });

  function updateApiPanel(qs) {
    const url = `${location.origin}/api/search?${qs}`;
    apiUrlEl.textContent = url;
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const qs = buildQS();
    btn.disabled = true;
    btn.textContent = 'Searching…';
    setStatus('Fetching all pages — this may take a few seconds…');
    showSkeletons();
    allListings = [];
    // Collapse any open API panel while re-searching
    apiPanel.classList.add('hidden');
    apiBtn.classList.remove('active');

    try {
      const res = await fetch(`/api/search?${qs}`);
      const ct = res.headers.get('content-type') || '';
      if (!ct.includes('application/json')) {
        throw new Error(`Server error ${res.status}: unexpected non-JSON response (check server logs)`);
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);

      allListings = data.listings;
      resultsHeader.innerHTML = `<strong>${data.count}</strong> listings in <strong>${esc(data.location)}</strong>`;

      sortKey = null;
      sortDir = 'asc';
      document.querySelectorAll('thead th[data-sort]').forEach(th => th.classList.remove('sorted-asc', 'sorted-desc'));
      renderRows(allListings);
      updateApiPanel(qs);
      setStatus('');
    } catch (err) {
      setStatus(err.message || 'An unexpected error occurred.', true);
      tbody.innerHTML = '';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Search';
    }
  });
})();
