// ── 장중 급등주 AI ────────────────────────────────────────
let _iaSearchTimer = null;
let _iaSearchResults = [];
let _iaSearchIdx = -1;

async function onIntradaySearchInput(val) {
  clearTimeout(_iaSearchTimer);
  const dd = document.getElementById('ia-dropdown');
  if (!val.trim()) { dd.style.display = 'none'; return; }
  _iaSearchTimer = setTimeout(async () => {
    if (!isAuthenticated) return;
    try {
      const res = await fetch(`${API}/search?q=${encodeURIComponent(val.trim())}`);
      _iaSearchResults = await res.json();
      renderLgbmDropdown('ia-dropdown', _iaSearchResults, val.trim(), selectIntradayStock);
    } catch(e) {}
  }, 250);
}

function onIntradaySearchKeydown(e) {
  handleDropdownKeydown(e, 'ia-dropdown', _iaSearchResults, _iaSearchIdx,
    (idx) => { _iaSearchIdx = idx; },
    selectIntradayStock,
    document.getElementById('ia-search')
  );
}

function selectIntradayStock(code, name) {
  document.getElementById('ia-symbol').value = code;
  document.getElementById('ia-search').value = name !== code ? `${name} (${code})` : code;
  document.getElementById('ia-dropdown').style.display = 'none';
  document.getElementById('ia-selected-code').textContent = code;
  document.getElementById('ia-selected-name').textContent = name !== code ? name : '';
  document.getElementById('ia-selected').style.display = 'flex';
}

function setIntradayStatus(status, msg) {
  const badge = document.getElementById('ia-status-badge');
  if (!badge) return;
  const map = {
    idle: ['badge-hold', '대기중'],
    running: ['badge-live', '실행중'],
    done: ['badge-buy', '완료'],
    error: ['badge-sell', '오류'],
  };
  const [cls, label] = map[status] || map.idle;
  badge.className = `badge ${cls}`;
  badge.textContent = label;
  if (msg) {
    const labelEl = document.getElementById('ia-progress-label');
    if (labelEl) labelEl.textContent = msg;
  }
}

function getIntradaySymbol() {
  let symbol = document.getElementById('ia-symbol')?.value.trim() || '';
  if (!symbol) {
    const raw = document.getElementById('ia-search')?.value.trim() || '';
    const match = raw.match(/\d{6}/);
    if (match) symbol = match[0];
  }
  return symbol;
}

async function runIntradayTrain() {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  const symbol = getIntradaySymbol();
  const n_trials = parseInt(document.getElementById('ia-trials').value || 30);
  const ohlcv_count = parseInt(document.getElementById('ia-count').value || 240);
  if (!symbol) { toast('종목명 또는 코드를 입력하세요', 'warn'); return; }

  setIntradayStatus('running', '장중 AI 학습 중...');
  const resultEl = document.getElementById('ia-train-result');
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<span style="font-size:12px; color:var(--muted);">분봉 수집 및 Optuna 최적화 중...</span>';

  try {
    const res = await fetch(`${API}/intraday-ai/train`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({symbol, n_trials, ohlcv_count})
    });
    const data = await res.json();
    if (!res.ok || data.status === 'error') throw new Error(data.detail || data.message || '학습 실패');
    renderIntradayTrainResult(data);
    await loadIntradayModels();
    setIntradayStatus('done', '학습 완료');
    toast(`${symbol} 장중 AI 학습 완료`, 'success');
  } catch(e) {
    resultEl.innerHTML = `<span style="font-size:12px; color:var(--red);">학습 실패: ${escapeHtml(e.message)}</span>`;
    setIntradayStatus('error', '학습 실패');
    toast('장중 AI 학습 실패: ' + e.message, 'error');
  }
}

function renderIntradayTrainResult(data) {
  const resultEl = document.getElementById('ia-train-result');
  const bt = data.backtests || {};
  const bestKey = data.best_exit_strategy || '-';
  const bestBt = bt[bestKey] || {};
  const importance = data.importance || [];
  const topImp = importance.slice(0, 8).map(i => `<span class="param-chip" style="padding:3px 7px; font-size:11px;">${escapeHtml(i.feature)} <strong style="color:var(--accent);">${i.score}</strong></span>`).join('');
  resultEl.style.display = 'block';
  resultEl.innerHTML = `
    <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:10px;">
      <div class="metric-card"><div class="metric-label">Precision</div><div class="metric-value" style="font-size:18px;">${Number(data.precision || 0).toFixed(3)}</div></div>
      <div class="metric-card"><div class="metric-label">Recall</div><div class="metric-value" style="font-size:18px;">${Number(data.recall || 0).toFixed(3)}</div></div>
      <div class="metric-card"><div class="metric-label">Best 전략</div><div class="metric-value" style="font-size:18px;">${escapeHtml(bestKey)}</div></div>
      <div class="metric-card"><div class="metric-label">PF</div><div class="metric-value up" style="font-size:18px;">${Number(bestBt.profit_factor || 0).toFixed(2)}</div></div>
    </div>
    <div style="font-size:12px; color:var(--muted); line-height:1.8;">
      Sharpe <strong style="color:var(--text);">${Number(bestBt.sharpe || 0).toFixed(3)}</strong> ·
      MDD <strong style="color:var(--red);">${Number(bestBt.mdd || 0).toFixed(2)}%</strong> ·
      승률 <strong style="color:var(--green);">${Number(bestBt.win_rate || 0).toFixed(1)}%</strong> ·
      평균 보유 <strong style="color:var(--text);">${Number(bestBt.avg_hold_minutes || 0).toFixed(1)}분</strong>
    </div>
    <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:10px;">${topImp || '<span style="font-size:12px; color:var(--muted);">Feature importance 없음</span>'}</div>`;
}

async function loadIntradayModels() {
  const wrap = document.getElementById('ia-model-list');
  if (!wrap) return;
  wrap.innerHTML = '<div style="font-size:12px; color:var(--muted); background:var(--surface2); border-radius:8px; padding:10px; text-align:center;">모델 목록을 불러오는 중...</div>';
  try {
    const res = await fetch(`${API}/intraday-ai/models`);
    const models = await res.json();
    if (!res.ok) throw new Error(models.detail || '모델 목록 조회 실패');
    document.getElementById('ia-model-count').textContent = `${models.length}개`;
    document.getElementById('ia-m-models').textContent = `${models.length}`;
    if (!models.length) {
      wrap.innerHTML = '<div style="font-size:12px; color:var(--muted); background:var(--surface2); border-radius:8px; padding:10px; text-align:center;">저장된 장중 AI 모델이 없습니다</div>';
      return;
    }
    wrap.innerHTML = models.map(m => {
      const trainedAt = m.trained_at ? new Date(m.trained_at).toLocaleString('ko-KR') : '-';
      const pf = m.backtests?.[m.best_exit_strategy]?.profit_factor;
      return `<button class="btn btn-outline" style="width:100%; padding:10px 12px; text-align:left; display:flex; flex-direction:column; gap:5px;"
        onclick="selectIntradayStock('${escapeHtml(m.symbol)}','${escapeHtml(m.name || m.symbol)}'); predictIntradayAI();">
        <span style="display:flex; justify-content:space-between; gap:8px;">
          <span><strong style="color:var(--text);">${escapeHtml(m.name || '')}</strong> <span style="font-family:var(--mono); color:var(--accent);">${escapeHtml(m.symbol)}</span></span>
          <span class="badge ${m.has_model ? 'badge-buy' : 'badge-hold'}">${m.has_model ? '사용 가능' : '메타만'}</span>
        </span>
        <span style="font-size:11px; color:var(--muted); font-family:var(--mono);">${escapeHtml(trainedAt)} · 기준 ${(Number(m.entry_threshold || 0.75) * 100).toFixed(1)}% · 전략 ${escapeHtml(m.best_exit_strategy || '-')} ${pf ? `· PF ${Number(pf).toFixed(2)}` : ''}</span>
      </button>`;
    }).join('');
  } catch(e) {
    wrap.innerHTML = `<div style="font-size:12px; color:var(--red); background:var(--red-dim); border-radius:8px; padding:10px; text-align:center;">모델 목록 조회 실패: ${escapeHtml(e.message)}</div>`;
  }
}

async function predictIntradayAI() {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  const symbol = getIntradaySymbol();
  const count = parseInt(document.getElementById('ia-count').value || 120);
  if (!symbol) { toast('종목명 또는 코드를 입력하세요', 'warn'); return; }
  try {
    const res = await fetch(`${API}/intraday-ai/predict/${symbol}?count=${count}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '예측 실패');
    renderIntradayPrediction(data.symbol, data.score);
    toast(`${symbol} 장중 AI 예측 완료`, data.score.is_buy_candidate ? 'success' : 'info');
  } catch(e) {
    toast('장중 AI 예측 실패: ' + e.message, 'error');
  }
}

function renderIntradayPrediction(symbol, score) {
  const panel = document.getElementById('ia-predict-panel');
  const el = document.getElementById('ia-predict-result');
  panel.style.display = 'block';
  const features = score.features || {};
  const candidateBadge = score.is_buy_candidate
    ? '<span class="badge badge-buy">매수 후보</span>'
    : '<span class="badge badge-hold">관망</span>';
  el.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <div>
        <div style="font-size:12px; color:var(--muted);">${escapeHtml(symbol)} 최신 스코어</div>
        <div style="font-size:26px; font-family:var(--mono); font-weight:600; color:var(--green);">${(score.probability * 100).toFixed(1)}%</div>
      </div>
      ${candidateBadge}
    </div>
    <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:8px;">
      <div class="metric-card"><div class="metric-label">최종점수</div><div class="metric-value" style="font-size:18px;">${Number(score.final_score || 0).toFixed(3)}</div></div>
      <div class="metric-card"><div class="metric-label">진입기준</div><div class="metric-value" style="font-size:18px;">${(Number(score.entry_threshold || 0) * 100).toFixed(1)}%</div></div>
      <div class="metric-card"><div class="metric-label">현재가</div><div class="metric-value" style="font-size:18px;">${fmtWon(score.price || 0)}</div></div>
    </div>
    <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:12px;">
      <span class="param-chip">거래대금증가 <strong style="color:var(--accent);">${Number(features.turnover_growth || 0).toFixed(3)}</strong></span>
      <span class="param-chip">15분돌파 <strong style="color:var(--accent);">${features.break_15m_high ? 'Y' : 'N'}</strong></span>
      <span class="param-chip">VWAP괴리 <strong style="color:var(--accent);">${(Number(features.vwap_deviation || 0) * 100).toFixed(2)}%</strong></span>
      <span class="param-chip">호가불균형 <strong style="color:var(--accent);">${Number(features.orderbook_imbalance || 0).toFixed(3)}</strong></span>
    </div>`;
}

function updateIntradayProgress(done, total, pct) {
  document.getElementById('ia-progress-bar').style.width = `${pct || 0}%`;
  document.getElementById('ia-progress-text').textContent = `${done} / ${total}`;
  document.getElementById('ia-progress-label').textContent = total ? `${pct || 0}% 진행` : '대기중';
}

async function runIntradayRank() {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  const payload = {
    universe: document.getElementById('ia-rank-universe').value,
    limit: parseInt(document.getElementById('ia-rank-limit').value || 20),
    max_symbols: parseInt(document.getElementById('ia-rank-max').value || 80),
    min_probability: parseFloat(document.getElementById('ia-rank-prob').value || 75) / 100,
    require_breakout_15m: document.getElementById('ia-rank-breakout').checked,
    require_turnover_growth: document.getElementById('ia-rank-turnover').checked,
  };
  setIntradayStatus('running', '랭킹 실행 중...');
  updateIntradayProgress(0, 0, 0);
  document.getElementById('ia-rank-body').innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--muted); padding:32px;">랭킹 계산 중...</td></tr>';
  document.getElementById('ia-m-count').textContent = '0';
  try {
    const res = await fetch(`${API}/intraday-ai/rank`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '랭킹 실패');
    renderIntradayRank(data.result || []);
    setIntradayStatus('done', `랭킹 완료 · ${data.count || 0}개 후보`);
    toast(`장중 AI 랭킹 완료 (${data.count || 0}개)`, 'success');
  } catch(e) {
    setIntradayStatus('error', '랭킹 실패');
    toast('장중 AI 랭킹 실패: ' + e.message, 'error');
  }
}

async function loadIntradayRankResult() {
  try {
    const results = await fetch(`${API}/intraday-ai/rank/result`).then(r => r.json());
    renderIntradayRank(Array.isArray(results) ? results : []);
  } catch(e) {}
}

function renderIntradayRank(results) {
  const tbody = document.getElementById('ia-rank-body');
  document.getElementById('ia-m-count').textContent = String(results.length);
  const top = results[0]?.score || {};
  document.getElementById('ia-m-prob').textContent = results.length ? `${(top.probability * 100).toFixed(1)}%` : '-';
  document.getElementById('ia-m-score').textContent = results.length ? Number(top.final_score || 0).toFixed(3) : '-';
  if (!results.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--muted); padding:32px;">조건에 맞는 급등 후보가 없습니다</td></tr>';
    return;
  }
  tbody.innerHTML = results.map(item => {
    const pct = parseFloat(item.change_pct || 0);
    const cls = pct >= 0 ? 'up' : 'down';
    const score = item.score || {};
    const conds = (item.conditions || []).map(c => `<span class="badge badge-live" style="font-size:10px; margin:1px;">${escapeHtml(c)}</span>`).join('');
    return `<tr>
      <td style="font-family:var(--mono); color:var(--accent);">#${item.rank || '-'}</td>
      <td><strong>${escapeHtml(item.symbol)}</strong><br><span style="font-size:11px;color:var(--muted);">${escapeHtml(item.name || '')}</span></td>
      <td style="font-family:var(--mono);">${fmtWon(item.price || score.price || 0)}</td>
      <td class="${cls}" style="font-family:var(--mono);">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
      <td class="up" style="font-family:var(--mono);">${(Number(score.probability || 0) * 100).toFixed(1)}%</td>
      <td style="font-family:var(--mono);">${Number(score.final_score || 0).toFixed(3)}</td>
      <td>${conds}</td>
      <td>
        <button class="btn btn-outline" style="padding:3px 8px; font-size:11px;"
          onclick="showPage('quote', document.querySelector('.nav-item:nth-child(3)')); setTimeout(()=>selectStock('${escapeHtml(item.symbol)}','${escapeHtml(item.name || item.symbol)}'),50);">
          차트
        </button>
        <button class="btn btn-success" style="padding:3px 8px; font-size:11px; margin-top:4px;"
          onclick="document.getElementById('ord-symbol').value='${escapeHtml(item.symbol)}'; showPage('order', document.querySelector('.nav-item:nth-child(4)'));">
          주문
        </button>
      </td>
    </tr>`;
  }).join('');
}


