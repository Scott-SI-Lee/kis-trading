// ── 자동매매 전략 ──
function onStrategyChange() {
  const type = document.getElementById('strat-type').value;
  document.querySelectorAll('.strat-params').forEach(el => el.style.display = 'none');
  const panel = document.getElementById('params-' + type);
  if (panel) panel.style.display = 'grid';
  if (type === 'lgbm_ai') loadStrategyAIModels();
}

async function loadStrategyAIModels() {
  const select = document.getElementById('strat-ai-model');
  if (!select) return;
  select.innerHTML = '<option value="">저장된 모델 불러오는 중...</option>';
  try {
    const res = await fetch(`${API}/lgbm/models`);
    const models = await res.json();
    if (!res.ok) throw new Error(models.detail || '모델 목록 조회 실패');
    if (!models.length) {
      select.innerHTML = '<option value="">저장된 모델 없음</option>';
      return;
    }
    select.innerHTML = models.filter(m => m.has_model).map(m => {
      const name = m.name ? `${m.name} ` : '';
      const trainedAt = m.trained_at ? new Date(m.trained_at).toLocaleDateString('ko-KR') : '';
      return `<option value="${escapeHtml(m.symbol)}">${escapeHtml(name)}${escapeHtml(m.symbol)}${trainedAt ? ` · ${escapeHtml(trainedAt)}` : ''}</option>`;
    }).join('');
    selectStrategyAIModel(select.value);
  } catch(e) {
    select.innerHTML = `<option value="">모델 목록 조회 실패</option>`;
    toast('AI 모델 목록 조회 실패: ' + e.message, 'error');
  }
}

function selectStrategyAIModel(symbol) {
  if (!symbol) return;
  document.getElementById('strat-symbol').value = symbol;
}

function openStrategyHelp() {
  const modal = document.getElementById('strategy-help-modal');
  if (modal) modal.style.display = 'flex';
}

function closeStrategyHelp() {
  const modal = document.getElementById('strategy-help-modal');
  if (modal) modal.style.display = 'none';
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeStrategyHelp();
});

let _stratSymbolTimer = null;
let _stratSymbolResults = [];
let _stratSymbolIdx = -1;

function getStrategySymbolSearchToken(value) {
  const parts = (value || '').split(/[,\s]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
}

async function onStrategySymbolInput(value) {
  clearTimeout(_stratSymbolTimer);
  const dd = document.getElementById('strat-symbol-dropdown');
  const token = getStrategySymbolSearchToken(value);
  if (!token) {
    if (dd) dd.style.display = 'none';
    return;
  }

  _stratSymbolTimer = setTimeout(async () => {
    if (!isAuthenticated) return;
    try {
      const res = await fetch(`${API}/search?q=${encodeURIComponent(token)}`);
      _stratSymbolResults = await res.json();
      _stratSymbolIdx = -1;
      renderLgbmDropdown('strat-symbol-dropdown', _stratSymbolResults, token, selectStrategyStock);
    } catch(e) {}
  }, 250);
}

function onStrategySymbolKeydown(e) {
  handleDropdownKeydown(e, 'strat-symbol-dropdown', _stratSymbolResults, _stratSymbolIdx,
    (idx) => { _stratSymbolIdx = idx; },
    selectStrategyStock,
    document.getElementById('strat-symbol')
  );
}

function selectStrategyStock(code, name) {
  const input = document.getElementById('strat-symbol');
  const raw = input.value || '';
  const parts = raw.split(/[,\s]+/).filter(Boolean);
  if (!parts.length || /[,\s]$/.test(raw)) {
    parts.push(code);
  } else {
    parts[parts.length - 1] = code;
  }
  input.value = [...new Set(parts)].join(', ');
  const dd = document.getElementById('strat-symbol-dropdown');
  if (dd) dd.style.display = 'none';
}

function formatStrategyIndicators(indicators = {}) {
  const entries = Object.entries(indicators || {});
  if (!entries.length) return '';
  return entries.map(([k, v]) => `${k}:${v}`).join(' ');
}

function formatStrategyLogEntry(entry) {
  const price = entry.price !== undefined && entry.price !== null ? `가격:${fmt(entry.price)}` : '';
  const reason = entry.reason ? ` ${entry.reason}` : '';
  const indicators = formatStrategyIndicators(entry.indicators);
  return [price + reason, indicators].filter(Boolean).join(' | ');
}

function updateStrategySummary(status = {}) {
  const traders = Array.isArray(status.traders) ? status.traders : [];
  const running = traders.filter(t => t.running);
  const activeCount = status.active_count ?? running.length;
  const first = running[0] || traders[0] || null;

  if (activeCount > 0) {
    document.getElementById('strat-name').textContent = `${activeCount}개 종목 자동매매 실행중`;
    document.getElementById('strat-detail').textContent = first
      ? `${first.symbol} ${first.strategy || ''} 외 ${Math.max(activeCount - 1, 0)}개`
      : '여러 종목 전략이 실행 중입니다';
    document.getElementById('strat-badge').className = 'badge badge-live';
    document.getElementById('strat-badge').textContent = 'RUNNING';
    document.getElementById('m-bot').textContent = `${activeCount}개 실행`;
    document.getElementById('m-bot-symbol').textContent = running.map(t => t.symbol).slice(0, 3).join(', ') + (activeCount > 3 ? '...' : '');
  } else {
    document.getElementById('strat-name').textContent = '대기중';
    document.getElementById('strat-detail').textContent = '전략이 실행되지 않았습니다';
    document.getElementById('strat-badge').className = 'badge badge-hold';
    document.getElementById('strat-badge').textContent = 'STOPPED';
    document.getElementById('m-bot').textContent = '대기';
    document.getElementById('m-bot-symbol').textContent = '-';
  }
}

function renderRunningStrategies(status = {}) {
  const tbody = document.getElementById('strategy-running-body');
  if (!tbody) return;
  const traders = Array.isArray(status.traders) ? status.traders : [];
  if (!traders.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--muted); padding:24px;">실행 중인 자동매매가 없습니다</td></tr>';
    return;
  }

  tbody.innerHTML = traders.map(t => {
    const running = !!t.running;
    const statusBadge = running
      ? '<span class="badge badge-live">RUNNING</span>'
      : '<span class="badge badge-hold">STOPPED</span>';
    return `<tr>
      <td><strong>${escapeHtml(t.symbol || '-')}</strong></td>
      <td>${escapeHtml(t.strategy || '-')}</td>
      <td style="font-family:var(--mono);">${t.qty ?? '-'}</td>
      <td style="font-family:var(--mono);">${t.position ?? 0}</td>
      <td style="font-family:var(--mono);">${t.interval ?? '-'}초</td>
      <td>${statusBadge}</td>
      <td>
        <button class="btn btn-danger" style="padding:3px 8px; font-size:11px;"
          onclick="stopStrategySymbol('${escapeHtml(t.symbol || '')}')">
          중지
        </button>
      </td>
    </tr>`;
  }).join('');
}

async function loadStrategyStatus() {
  if (!isAuthenticated) return;
  try {
    const status = await fetch(`${API}/strategy/status`).then(r => r.json());
    updateStrategySummary(status);
    renderRunningStrategies(status);
  } catch(e) {}
}

function parseStrategySymbols(raw) {
  return [...new Set((raw || '')
    .split(/[,\s]+/)
    .map(s => s.trim())
    .filter(Boolean))];
}

async function startStrategy() {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  const strategy = document.getElementById('strat-type').value;
  const symbols = parseStrategySymbols(document.getElementById('strat-symbol').value);
  const qty = parseInt(document.getElementById('strat-qty').value);
  const check_interval = parseInt(document.getElementById('strat-interval').value);
  if (!symbols.length || !qty) { toast('종목코드와 수량을 입력하세요', 'warn'); return; }

  const payload = {
    strategy, qty, check_interval,
    short_period:  parseInt(document.getElementById('strat-short')?.value || 5),
    long_period:   parseInt(document.getElementById('strat-long')?.value || 20),
    rsi_period:    parseInt(document.getElementById('strat-rsi-period')?.value || 14),
    oversold:      parseFloat(document.getElementById('strat-oversold')?.value || 30),
    overbought:    parseFloat(document.getElementById('strat-overbought')?.value || 70),
    bb_period:     parseInt(document.getElementById('strat-bb-period')?.value || 20),
    bb_k:          parseFloat(document.getElementById('strat-bb-k')?.value || 2),
    macd_fast:     parseInt(document.getElementById('strat-macd-fast')?.value || 12),
    macd_slow:     parseInt(document.getElementById('strat-macd-slow')?.value || 26),
    macd_signal:   parseInt(document.getElementById('strat-macd-signal')?.value || 9),
    ai_confidence:  parseFloat(document.getElementById('strat-ai-confidence')?.value || 45) / 100,
  };

  const stratNames = { golden_cross:'골든크로스', rsi:'RSI', bollinger:'볼린저밴드', macd:'MACD', lgbm_ai:'AI 파라미터' };
  try {
    const failed = [];
    for (const symbol of symbols) {
      const res = await fetch(`${API}/strategy/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({...payload, symbol})
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        failed.push(`${symbol}: ${err.detail || '시작 실패'}`);
      }
    }
    const name = stratNames[strategy] || strategy;
    await loadStrategyStatus();
    if (failed.length) {
      toast(`일부 시작 실패 · ${failed.join(', ')}`, 'error');
    } else {
      toast(`${symbols.length}개 종목 ${name} 자동매매 시작`, 'success');
    }
  } catch(err) {
    toast('전략 시작 실패: ' + err.message, 'error');
  }
}

async function stopStrategy() {
  try {
    await fetch(`${API}/strategy/stop`, { method: 'POST' });
    await loadStrategyStatus();
    toast('자동매매 전체 중지', 'warn');
  } catch(err) {
    toast('중지 실패: ' + err.message, 'error');
  }
}

async function stopStrategySymbol(symbol) {
  if (!symbol) return;
  try {
    const res = await fetch(`${API}/strategy/stop/${symbol}`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || '중지 실패');
    await loadStrategyStatus();
    toast(`${symbol} 자동매매 중지`, 'warn');
  } catch(err) {
    toast('종목 중지 실패: ' + err.message, 'error');
  }
}

async function loadStrategyLog() {
  if (!isAuthenticated) return;
  try {
    const logs = await fetch(`${API}/strategy/log`).then(r => r.json());
    const container = document.getElementById('strategy-log-full');
    container.innerHTML = '';
    logs.forEach(l => {
      const cls = l.signal === 'BUY' ? 'log-buy' : l.signal === 'SELL' ? 'log-sell' : 'log-hold';
      const text = l.action || `[${l.signal||'HOLD'}] ${formatStrategyLogEntry(l)}`;
      appendLog('strategy-log-full', l.time, text, cls);
    });
  } catch(e) {}
}

