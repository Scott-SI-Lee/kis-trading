// ── 단타 자동매매 함수 ──
async function startIntradayTrader() {
  try {
    const res = await fetch(`${API}/intraday-trader/start`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast('단타 자동매매 시작', 'success');
    document.getElementById('it-start-btn').disabled = true;
    document.getElementById('it-stop-btn').disabled = false;
    document.getElementById('it-status-badge').className = 'badge badge-live';
    document.getElementById('it-status-badge').textContent = '실행중';
    loadIntradayTraderStatus();
    startIntradayTraderMonitor();
  } catch(e) {
    toast('시작 실패: ' + e.message, 'error');
  }
}

async function stopIntradayTrader() {
  try {
    const res = await fetch(`${API}/intraday-trader/stop`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast('단타 자동매매 중지', 'success');
    document.getElementById('it-start-btn').disabled = false;
    document.getElementById('it-stop-btn').disabled = true;
    document.getElementById('it-status-badge').className = 'badge badge-hold';
    document.getElementById('it-status-badge').textContent = '대기중';
    loadIntradayTraderStatus();
  } catch(e) {
    toast('중지 실패: ' + e.message, 'error');
  }
}

async function loadIntradayTraderStatus() {
  try {
    const res = await fetch(`${API}/intraday-trader/status`);
    const status = await res.json();

    // 상태 업데이트
    const running = status.running || false;
    document.getElementById('it-start-btn').disabled = running;
    document.getElementById('it-stop-btn').disabled = !running;

    const badge = document.getElementById('it-status-badge');
    badge.className = running ? 'badge badge-live' : 'badge badge-hold';
    badge.textContent = running ? '실행중' : '대기중';

    // 손익 & 거래 수
    const totalPnL = status.total_pnl || 0;
    const pnlEl = document.getElementById('it-total-pnl');
    pnlEl.textContent = (totalPnL >= 0 ? '₩' : '-₩') + Math.abs(totalPnL).toLocaleString('ko-KR');
    pnlEl.style.color = totalPnL >= 0 ? 'var(--green)' : 'var(--red)';

    document.getElementById('it-trades-closed').textContent = status.trades_closed || 0;

    // 포지션 상태
    if (status.position) {
      const pos = status.position;
      document.getElementById('it-position-status').style.display = 'block';
      document.getElementById('it-pos-symbol').textContent = pos.symbol;
      document.getElementById('it-pos-qty').textContent = pos.qty + '주';
      document.getElementById('it-pos-entry').textContent = '₩' + pos.entry_price.toLocaleString('ko-KR');
      document.getElementById('it-pos-current').textContent = '₩' + pos.current_price.toLocaleString('ko-KR');

      const pnl = pos.pnl;
      const pnlEl = document.getElementById('it-pos-pnl');
      pnlEl.textContent = (pnl >= 0 ? '+' : '') + '₩' + pnl.toLocaleString('ko-KR') + ' (' + pos.pnl_pct.toFixed(2) + '%)';
      pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    } else {
      document.getElementById('it-position-status').style.display = 'none';
    }

    // 로그 업데이트
    await loadIntradayTraderLog();
  } catch(e) {
    console.debug('상태 로드 오류:', e);
  }
}

async function loadIntradayTraderLog() {
  try {
    const res = await fetch(`${API}/intraday-trader/log`);
    const logs = await res.json();

    const tbody = document.getElementById('it-log-tbody');
    if (!logs || logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--muted); padding:40px 12px;">거래 없음</td></tr>';
      return;
    }

    tbody.innerHTML = logs.map(log => {
      const time = new Date(log.time).toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
      const type = log.type === 'entry' ? '📈 진입' : '📉 청산';
      const symbol = log.symbol || '-';
      const qty = log.qty || '-';

      let entryExit = '-';
      if (log.type === 'entry') {
        entryExit = '₩' + log.entry_price.toLocaleString('ko-KR');
      } else {
        entryExit = log.entry_price.toLocaleString('ko-KR') + ' → ' + log.exit_price.toLocaleString('ko-KR');
      }

      const pnl = log.pnl ? ((log.pnl >= 0 ? '+' : '') + '₩' + log.pnl.toLocaleString('ko-KR')) : '진입중';
      const pnlColor = log.pnl !== undefined ? (log.pnl >= 0 ? 'color:var(--green)' : 'color:var(--red)') : '';

      const reason = log.reason || '-';

      return `<tr>
        <td style="font-size:11px;">${time}</td>
        <td>${type}</td>
        <td style="font-weight:500; color:var(--accent);">${symbol}</td>
        <td>${qty}</td>
        <td style="font-size:11px;">${entryExit}</td>
        <td style="font-weight:500; ${pnlColor}">${pnl}</td>
        <td style="font-size:11px; color:var(--muted);">${reason}</td>
      </tr>`;
    }).join('');
  } catch(e) {
    console.debug('로그 로드 오류:', e);
  }
}

async function updateIntradayConfig() {
  try {
    const config = {
      entry_threshold: parseFloat(document.getElementById('it-entry-threshold').value) / 100,
      take_profit: parseFloat(document.getElementById('it-take-profit').value) / 100,
      stop_loss: parseFloat(document.getElementById('it-stop-loss').value) / 100,
      time_exit_minutes: parseInt(document.getElementById('it-time-exit').value),
      max_daily_loss: parseFloat(document.getElementById('it-max-daily-loss').value),
    };

    const res = await fetch(`${API}/intraday-trader/config`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(config),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast('설정 업데이트 완료', 'success');
  } catch(e) {
    toast('설정 변경 실패: ' + e.message, 'error');
  }
}

async function manualEntryIntraday() {
  try {
    const symbol = document.getElementById('it-manual-symbol').value.trim();
    const qty = parseInt(document.getElementById('it-manual-qty').value);
    if (!symbol || qty <= 0) {
      toast('종목과 수량을 올바르게 입력하세요', 'warn');
      return;
    }

    const res = await fetch(`${API}/intraday-trader/entry`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({symbol, qty}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast(`${symbol} ${qty}주 진입`, 'success');
    loadIntradayTraderStatus();
  } catch(e) {
    toast('진입 실패: ' + e.message, 'error');
  }
}

async function manualExitIntraday() {
  try {
    const res = await fetch(`${API}/intraday-trader/exit`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast('포지션 청산 완료', 'success');
    loadIntradayTraderStatus();
  } catch(e) {
    toast('청산 실패: ' + e.message, 'error');
  }
}

function clearIntradayLog() {
  if (confirm('거래 로그를 초기화하시겠습니까?')) {
    document.getElementById('it-log-tbody').innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--muted); padding:40px 12px;">거래 없음</td></tr>';
    toast('로그 초기화됨', 'info');
  }
}

let intradayTraderMonitorActive = false;
function startIntradayTraderMonitor() {
  if (intradayTraderMonitorActive) return;
  intradayTraderMonitorActive = true;

  const monitor = setInterval(async () => {
    const page = document.getElementById('page-intraday-trader');
    if (!page || !page.classList.contains('active')) {
      clearInterval(monitor);
      intradayTraderMonitorActive = false;
      return;
    }
    await loadIntradayTraderStatus();
  }, 2000);
}

function appendIntradaySignal(message, level = 'info') {
  const el = document.getElementById('it-signal-log');
  if (!el) return;

  const time = new Date().toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  const color = level === 'success' ? 'var(--green)' : level === 'error' ? 'var(--red)' : level === 'yellow' ? 'var(--yellow)' : 'var(--accent)';

  const line = document.createElement('div');
  line.style.cssText = `display:flex; gap:8px; color:${color};`;
  line.textContent = `[${time}] ${message}`;

  el.appendChild(line);
  el.scrollTop = el.scrollHeight;

  // 최대 50줄 유지
  while (el.children.length > 50) {
    el.removeChild(el.firstChild);
  }
}

// 주기적 상태 갱신
setInterval(() => {
  const page = document.getElementById('page-intraday-trader');
  if (page && page.classList.contains('active') && isAuthenticated) {
    loadIntradayTraderStatus();
  }
}, 3000);

// 모든 함수 정의 완료 후 실행
