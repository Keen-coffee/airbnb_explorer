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
        <td class="col-name"><div class="listing-name">${esc(l.name)}</div></td>
        <td class="col-bedrooms">${dash(l.bedrooms)}</td>
        <td class="col-beds">${dash(l.beds)}</td>
        <td class="col-baths">${dash(l.bathrooms)}</td>
        <td class="col-rating">${formatRating(l)}</td>
        <td class="col-reviews">${formatReviews(l)}</td>
        <td class="col-price">${formatPrice(l)}</td>
        <td class="col-link"><a class="btn-view" href="${esc(l.url)}" target="_blank" rel="noopener">View →</a></td>
      </tr>
    `).join('');
  }

  function sortAndRender() {
    const sorted = [...allListings].sort((a, b) => {
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
    renderRows(sorted);
    // Update header sort indicators
    document.querySelectorAll('thead th[data-sort]').forEach(th => {
      th.classList.remove('sorted-asc', 'sorted-desc');
      if (th.dataset.sort === sortKey) {
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
        <td><div class="skel" style="width:75%"></div></td>
        <td><div class="skel" style="width:60px"></div></td>
        <td><div class="skel" style="width:40px"></div></td>
        <td><div class="skel" style="width:40px"></div></td>
        <td><div class="skel" style="width:36px"></div></td>
        <td><div class="skel" style="width:44px"></div></td>
        <td><div class="skel" style="width:70px"></div></td>
        <td><div class="skel" style="width:56px;margin-left:auto"></div></td>
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
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);

      allListings = data.listings;
      const hasDates = data.checkin && data.checkout;
      const priceLabel = hasDates ? 'total price' : 'nightly price';
      resultsHeader.innerHTML = `<strong>${data.count}</strong> listings in <strong>${esc(data.location)}</strong>, sorted by ${priceLabel}`;

      sortKey = 'price';
      sortDir = 'asc';
      sortAndRender();
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
