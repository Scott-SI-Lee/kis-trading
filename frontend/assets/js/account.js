// ── 잔고 조회 ──
async function loadBalance() {
  if (!isAuthenticated) return;
  try {
    const [bal, pos] = await Promise.all([
      fetch(`${API}/balance`).then(r => r.json()),
      fetch(`${API}/positions`).then(r => r.json()),
    ]);
    // 메트릭 업데이트
    const pct = parseFloat(bal.profit_loss_pct);
    const profitCls = pct >= 0 ? 'up' : 'down';
    document.getElementById('m-total').textContent = fmtWon(bal.total_eval);
    document.getElementById('m-cash').textContent = fmtWon(bal.cash);
    document.getElementById('m-purchase').textContent = fmtWon(bal.purchase_amount);
    document.getElementById('m-profit').className = `metric-sub ${profitCls}`;
    document.getElementById('m-profit').textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% (${fmtWon(bal.profit_loss)})`;
    document.getElementById('b-total').textContent = fmtWon(bal.total_eval);
    document.getElementById('b-purchase').textContent = fmtWon(bal.purchase_amount);
    document.getElementById('b-profit').className = `metric-value ${profitCls}`;
    document.getElementById('b-profit').textContent = fmtWon(bal.profit_loss);
    document.getElementById('b-cash').textContent = fmtWon(bal.cash);
    // 포지션 테이블
    renderPositions(pos);
  } catch (err) {
    toast('잔고 조회 실패: ' + err.message, 'error');
  }
}

function renderPositions(positions) {
  const tbody = (id) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (positions.length === 0) {
      el.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--muted); padding:24px;">보유 종목 없음</td></tr>';
      return;
    }
    el.innerHTML = positions.map(p => {
      const pct = parseFloat(p.profit_loss_pct);
      const cls = pct >= 0 ? 'up' : 'down';
      return `<tr>
        <td>${p.symbol}</td>
        <td>${p.name}</td>
        <td>${fmt(p.qty)}</td>
        <td>${fmtWon(p.avg_price)}</td>
        <td>${fmtWon(p.current_price)}</td>
        <td class="${cls}">${fmtWon(p.profit_loss)}</td>
        <td class="${cls}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
      </tr>`;
    }).join('');
  };
  tbody('balance-positions-body');
  // 대시보드용 축약
  const el = document.getElementById('positions-body');
  if (!el) return;
  if (positions.length === 0) {
    el.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--muted); padding:24px;">보유 종목 없음</td></tr>';
    return;
  }
  el.innerHTML = positions.map(p => {
    const pct = parseFloat(p.profit_loss_pct);
    const cls = pct >= 0 ? 'up' : 'down';
    return `<tr>
      <td>${p.symbol} ${p.name}</td>
      <td>${fmt(p.qty)}주</td>
      <td class="${cls}">${fmtWon(p.profit_loss)}</td>
      <td class="${cls}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
    </tr>`;
  }).join('');
}

async function loadPositions() {
  if (!isAuthenticated) return;
  try {
    const pos = await fetch(`${API}/positions`).then(r => r.json());
    renderPositions(pos);
  } catch(e) {}
}

// ── 종목 검색 자동완성 ──
let _searchTimer = null;
let _searchResults = [];
let _searchIdx = -1;

async function onSearchInput(val) {
  const dd = document.getElementById('search-dropdown');
  clearTimeout(_searchTimer);
  if (!val.trim() || val.trim().length < 1) {
    dd.style.display = 'none';
    return;
  }
  _searchTimer = setTimeout(async () => {
    if (!isAuthenticated) return;
    try {
      const res = await fetch(`${API}/search?q=${encodeURIComponent(val.trim())}`);
      _searchResults = await res.json();
      renderSearchDropdown(_searchResults, val.trim());
    } catch(e) {}
  }, 250);
}

function renderSearchDropdown(items, query) {
  const dd = document.getElementById('search-dropdown');
  if (!items.length) { dd.style.display = 'none'; return; }
  _searchIdx = -1;
  dd.innerHTML = items.map((item, i) => {
    // 매칭 부분 하이라이트
    const hl = (str) => {
      const idx = str.toLowerCase().indexOf(query.toLowerCase());
      if (idx < 0) return str;
      return str.slice(0, idx) +
        `<strong style="color:var(--text);">${str.slice(idx, idx + query.length)}</strong>` +
        str.slice(idx + query.length);
    };
    return `<div class="search-item" data-idx="${i}"
      onmousedown="selectStock('${item.symbol}','${item.name.replace(/'/g,"\'")}')">
      <span class="search-code">${item.symbol}</span>
      <span class="search-name">${hl(item.name)}</span>
    </div>`;
  }).join('');
  dd.style.display = 'block';
}

function onSearchKeydown(e) {
  const dd = document.getElementById('search-dropdown');
  const items = dd.querySelectorAll('.search-item');
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    _searchIdx = Math.min(_searchIdx + 1, items.length - 1);
    items.forEach((el, i) => el.classList.toggle('active', i === _searchIdx));
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    _searchIdx = Math.max(_searchIdx - 1, 0);
    items.forEach((el, i) => el.classList.toggle('active', i === _searchIdx));
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (_searchIdx >= 0 && _searchResults[_searchIdx]) {
      const s = _searchResults[_searchIdx];
      selectStock(s.symbol, s.name);
    } else if (_searchResults.length > 0) {
      selectStock(_searchResults[0].symbol, _searchResults[0].name);
    } else {
      // 숫자 코드 직접 입력
      const val = document.getElementById('quote-search').value.trim();
      if (/^\d{6}$/.test(val)) selectStock(val, val);
    }
  } else if (e.key === 'Escape') {
    document.getElementById('search-dropdown').style.display = 'none';
  }
}

function selectStock(code, name) {
  document.getElementById('quote-symbol').value = code;
  document.getElementById('quote-search').value = name !== code ? `${name} (${code})` : code;
  document.getElementById('search-dropdown').style.display = 'none';
  document.getElementById('selected-stock').style.display = 'flex';
  document.getElementById('selected-code').textContent = code;
  document.getElementById('selected-name').textContent = name !== code ? name : '';
  loadQuote();
}

// 드롭다운 외부 클릭 시 닫기
document.addEventListener('click', (e) => {
  if (!e.target.closest('#search-dropdown') && !e.target.matches('#quote-search')) {
    document.getElementById('search-dropdown').style.display = 'none';
  }
});

// ── 시세 조회 + 차트 ──
async function loadQuote() {
  let sym = document.getElementById('quote-symbol').value.trim();
  // hidden input이 비어있으면 검색창에서 6자리 코드 추출 시도
  if (!sym) {
    const raw = document.getElementById('quote-search')?.value.trim() || '';
    const match = raw.match(/\d{6}/);
    if (match) sym = match[0];
  }
  if (!sym) { toast('종목명 또는 코드를 입력하세요', 'warn'); return; }
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  try {
    const [price, history] = await Promise.all([
      fetch(`${API}/price/${sym}`).then(r => r.json()),
      fetch(`${API}/price/${sym}/history?period=D&count=60`).then(r => r.json()),
    ]);
    const chgCls = price.change >= 0 ? 'up' : 'down';
    document.getElementById('q-price').textContent = fmtWon(price.price);
    document.getElementById('q-price').className = `metric-value ${chgCls}`;
    document.getElementById('q-change').textContent = (price.change >= 0 ? '+' : '') + fmtWon(price.change);
    document.getElementById('q-change').className = `metric-value ${chgCls}`;
    document.getElementById('q-pct').textContent = (price.change_pct >= 0 ? '+' : '') + parseFloat(price.change_pct).toFixed(2) + '%';
    document.getElementById('q-pct').className = `metric-value ${chgCls}`;
    document.getElementById('q-vol').textContent = fmt(price.volume);
    document.getElementById('q-hl').innerHTML = `<span style="font-size:14px">${fmtWon(price.high)}<br>${fmtWon(price.low)}</span>`;
    drawPriceChart(history);
  } catch(err) {
    toast('시세 조회 실패: ' + err.message, 'error');
  }
}

function ma(data, period) {
  return data.map((_, i) => {
    if (i < period - 1) return null;
    const slice = data.slice(i - period + 1, i + 1);
    return Math.round(slice.reduce((a, b) => a + b, 0) / period);
  });
}

function drawPriceChart(history) {
  const labels = history.map(h => h.date.substring(4));
  const closes = history.map(h => h.close);
  const short_period = parseInt(document.getElementById('strat-short').value) || 5;
  const long_period = parseInt(document.getElementById('strat-long').value) || 20;
  const shortMA = ma(closes, short_period);
  const longMA = ma(closes, long_period);

  if (priceChart) priceChart.destroy();
  priceChart = new Chart(document.getElementById('priceChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: '종가', data: closes, borderColor: '#ef4444', borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0.2 },
        { label: `단기MA(${short_period})`, data: shortMA, borderColor: '#3b82f6', borderWidth: 1.5, borderDash: [4,2], pointRadius: 0, fill: false },
        { label: `장기MA(${long_period})`, data: longMA, borderColor: '#f59e0b', borderWidth: 1.5, borderDash: [4,2], pointRadius: 0, fill: false },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6b7a99', maxTicksLimit: 10, font: { family: 'JetBrains Mono' } } },
        y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6b7a99', font: { family: 'JetBrains Mono' }, callback: v => fmt(v) } }
      }
    }
  });
}

// ── 수동 주문 ──
async function placeOrder(forceSide) {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  const symbol = document.getElementById('ord-symbol').value.trim();
  const side = forceSide || document.getElementById('ord-side').value;
  const qty = parseInt(document.getElementById('ord-qty').value);
  const price = parseInt(document.getElementById('ord-price').value) || 0;
  if (!symbol || !qty) { toast('종목코드와 수량을 입력하세요', 'warn'); return; }
  try {
    const res = await fetch(`${API}/order`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, side, qty, price })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast(`${side === 'BUY' ? '매수' : '매도'} 주문 완료 (주문번호: ${data.order_no})`, 'success');
    loadOrders();
  } catch(err) {
    toast('주문 실패: ' + err.message, 'error');
  }
}

async function loadOrders() {
  if (!isAuthenticated) return;
  try {
    const orders = await fetch(`${API}/orders`).then(r => r.json());
    const html = Array.isArray(orders) && orders.length > 0
      ? orders.map(o => `<tr><td>${o.ODNO||'-'}</td><td>${o.PDNO||'-'}</td><td>${o.SLL_BUY_DVSN_CD==='02'?'매도':'매수'}</td><td>${fmt(o.ORD_QTY||0)}</td><td>${fmtWon(o.ORD_UNPR||0)}</td><td>${o.ORD_TMD||'-'}</td></tr>`).join('')
      : '<tr><td colspan="6" style="text-align:center; color:var(--muted); padding:24px;">주문 내역 없음</td></tr>';
    document.querySelectorAll('#orders-body, #orders-hist-body').forEach(el => el.innerHTML = html);
  } catch(e) {}
}

