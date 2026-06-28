// ── LGBM 종목 자동완성 ──────────────────────────────────────
let _lgbmSearchTimer = null;
let _lgbmSearchResults = [];
let _lgbmSearchIdx = -1;

async function onLgbmSearchInput(val) {
  clearTimeout(_lgbmSearchTimer);
  const dd = document.getElementById('lgbm-dropdown');
  if (!val.trim()) { dd.style.display = 'none'; return; }
  _lgbmSearchTimer = setTimeout(async () => {
    if (!isAuthenticated) return;
    try {
      const res = await fetch(`${API}/search?q=${encodeURIComponent(val.trim())}`);
      _lgbmSearchResults = await res.json();
      renderLgbmDropdown('lgbm-dropdown', _lgbmSearchResults, val.trim(), selectLgbmStock);
    } catch(e) {}
  }, 250);
}

function onLgbmSearchKeydown(e) {
  handleDropdownKeydown(e, 'lgbm-dropdown', _lgbmSearchResults, _lgbmSearchIdx,
    (idx) => { _lgbmSearchIdx = idx; },
    selectLgbmStock,
    document.getElementById('lgbm-search')
  );
}

function selectLgbmStock(code, name) {
  document.getElementById('lgbm-symbol').value = code;
  document.getElementById('lgbm-search').value = name !== code ? `${name} (${code})` : code;
  document.getElementById('lgbm-dropdown').style.display = 'none';
  document.getElementById('lgbm-selected-code').textContent = code;
  document.getElementById('lgbm-selected-name').textContent = name !== code ? name : '';
  document.getElementById('lgbm-selected').style.display = 'flex';
}

// ── LGBM 저장 모델 불러오기 자동완성 ──
let _lgbmLoadTimer = null;
let _lgbmLoadResults = [];
let _lgbmLoadIdx = -1;

async function onLgbmLoadSearchInput(val) {
  clearTimeout(_lgbmLoadTimer);
  const dd = document.getElementById('lgbm-load-dropdown');
  if (!val.trim()) { dd.style.display = 'none'; return; }
  _lgbmLoadTimer = setTimeout(async () => {
    if (!isAuthenticated) return;
    try {
      const res = await fetch(`${API}/search?q=${encodeURIComponent(val.trim())}`);
      _lgbmLoadResults = await res.json();
      renderLgbmDropdown('lgbm-load-dropdown', _lgbmLoadResults, val.trim(), selectLgbmLoadStock);
    } catch(e) {}
  }, 250);
}

function onLgbmLoadSearchKeydown(e) {
  handleDropdownKeydown(e, 'lgbm-load-dropdown', _lgbmLoadResults, _lgbmLoadIdx,
    (idx) => { _lgbmLoadIdx = idx; },
    selectLgbmLoadStock,
    document.getElementById('lgbm-load-search')
  );
}

function selectLgbmLoadStock(code, name) {
  document.getElementById('lgbm-load-symbol').value = code;
  document.getElementById('lgbm-load-search').value = name !== code ? `${name} (${code})` : code;
  document.getElementById('lgbm-load-dropdown').style.display = 'none';
}

async function loadSavedLGBMModels() {
  const wrap = document.getElementById('lgbm-saved-models');
  if (!wrap) return;
  wrap.innerHTML = `
    <div style="font-size:12px; color:var(--muted); background:var(--surface2); border-radius:8px; padding:10px; text-align:center;">
      저장된 모델 목록을 불러오는 중...
    </div>`;
  try {
    const res = await fetch(`${API}/lgbm/models`);
    const models = await res.json();
    if (!res.ok) throw new Error(models.detail || '목록 조회 실패');
    if (!models.length) {
      wrap.innerHTML = `
        <div style="font-size:12px; color:var(--muted); background:var(--surface2); border-radius:8px; padding:10px; text-align:center;">
          저장된 모델이 없습니다
        </div>`;
      return;
    }

    wrap.innerHTML = models.map(m => {
      const name = m.name ? `${escapeHtml(m.name)} ` : '';
      const trainedAt = m.trained_at ? new Date(m.trained_at).toLocaleString('ko-KR') : '-';
      const modelMetaChips = [
        m.feature_count ? {
          label: `피처 ${m.feature_count}`,
          title: '피처',
          desc: '모델 예측에 사용한 입력 지표 개수입니다. RSI, 이동평균, MACD, 볼린저밴드, 거래량 비율, 캔들 패턴 등이 포함됩니다.'
        } : null,
        m.horizon ? {
          label: `H${m.horizon}`,
          title: 'Horizon',
          desc: `예측 기준 기간입니다. H${m.horizon}은 오늘 피처를 기준으로 ${m.horizon}거래일 뒤 수익률을 예측하도록 학습했다는 의미입니다.`
        } : null,
        Number.isFinite(Number(m.buy_threshold)) ? {
          label: `B ${(Number(m.buy_threshold) * 100).toFixed(2)}%`,
          title: 'Buy Threshold',
          desc: `BUY 라벨 기준 수익률입니다. ${m.horizon ? `${m.horizon}거래일 뒤 ` : ''}수익률이 이 값 이상이면 학습 데이터에서 BUY로 분류했습니다.`
        } : null,
        Number.isFinite(Number(m.sell_threshold)) ? {
          label: `S ${(Number(m.sell_threshold) * 100).toFixed(2)}%`,
          title: 'Sell Threshold',
          desc: `SELL 라벨 기준 수익률입니다. ${m.horizon ? `${m.horizon}거래일 뒤 ` : ''}수익률이 이 값 이하이면 학습 데이터에서 SELL로 분류했습니다.`
        } : null,
      ].filter(Boolean).map(item => `
        <span class="param-chip" tabindex="0" style="padding:3px 7px; font-size:11px;" onclick="event.stopPropagation();">
          <span>${escapeHtml(item.label)}</span>
          <span class="param-help" style="width:13px; height:13px; font-size:9px;">?</span>
          <span class="param-tooltip">
            <strong>${escapeHtml(item.title)}</strong>
            ${escapeHtml(item.desc)}
          </span>
        </span>`).join('');
      const predictionHtml = renderSavedLGBMPrediction(null, m.has_model ? 'loading' : 'missing_model');
      return `
        <button class="btn btn-outline lgbm-saved-model" data-symbol="${escapeHtml(m.symbol)}" data-name="${escapeHtml(m.name || m.symbol)}"
          data-signal="" data-pred-status="${m.has_model ? 'loading' : 'missing_model'}"
          style="width:100%; padding:10px 12px; text-align:left; display:flex; flex-direction:column; gap:6px;">
          <span style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
            <span>
              <strong style="color:var(--text);">${name}</strong>
              <span style="font-family:var(--mono); color:var(--accent);">${escapeHtml(m.symbol)}</span>
            </span>
            <span class="badge ${m.has_model ? 'badge-buy' : 'badge-hold'}">${m.has_model ? '사용 가능' : '메타만'}</span>
          </span>
          <span style="font-size:11px; color:var(--muted); font-family:var(--mono); display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
            <span>${escapeHtml(trainedAt)}</span>
            ${modelMetaChips}
          </span>
          <span id="lgbm-pred-${escapeHtml(m.symbol)}">${predictionHtml}</span>
        </button>`;
    }).join('');

    wrap.querySelectorAll('.lgbm-saved-model').forEach(btn => {
      btn.addEventListener('click', () => {
        selectLgbmLoadStock(btn.dataset.symbol, btn.dataset.name);
        loadLGBMModel();
      });
    });
    applySavedLGBMFilter();
    loadSavedLGBMPredictions(models);
  } catch(e) {
    wrap.innerHTML = `
      <div style="font-size:12px; color:var(--red); background:var(--red-dim); border-radius:8px; padding:10px; text-align:center;">
        모델 목록 조회 실패: ${escapeHtml(e.message)}
      </div>`;
  }
}

function applySavedLGBMFilter() {
  const wrap = document.getElementById('lgbm-saved-models');
  const filter = document.getElementById('lgbm-signal-filter')?.value || 'ALL';
  if (!wrap) return;

  let visibleCount = 0;
  const cards = wrap.querySelectorAll('.lgbm-saved-model');
  cards.forEach(card => {
    const show = filter === 'ALL' || card.dataset.signal === filter;
    card.style.display = show ? 'flex' : 'none';
    if (show) visibleCount += 1;
  });

  const countEl = document.getElementById('lgbm-model-count');
  if (countEl) countEl.textContent = `${visibleCount} / ${cards.length}`;

  let empty = document.getElementById('lgbm-filter-empty');
  if (!empty) {
    empty = document.createElement('div');
    empty.id = 'lgbm-filter-empty';
    empty.style.cssText = 'display:none; font-size:12px; color:var(--muted); background:var(--surface2); border-radius:8px; padding:10px; text-align:center;';
    wrap.appendChild(empty);
  }
  empty.textContent = filter === 'ALL' ? '저장된 모델이 없습니다' : `${filter} 우세 모델이 없습니다`;
  empty.style.display = visibleCount === 0 ? 'block' : 'none';
}

function renderSavedLGBMPrediction(pred, status = 'ok') {
  if (pred) {
    const signalClass = pred.signal === 'BUY' ? 'badge-buy' : pred.signal === 'SELL' ? 'badge-sell' : 'badge-hold';
    return `
      <span style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; padding-top:2px;">
        <span style="font-size:11px; color:var(--muted);">현재신호</span>
        <span class="badge ${signalClass}">${escapeHtml(pred.signal)}</span>
        <span style="font-size:11px; color:var(--muted); font-family:var(--mono);">
          B ${(pred.prob_buy * 100).toFixed(1)}% · H ${(pred.prob_hold * 100).toFixed(1)}% · S ${(pred.prob_sell * 100).toFixed(1)}%
        </span>
      </span>`;
  }

  const labels = {
    loading: '현재신호 계산 중...',
    auth_required: '현재신호 인증 후 표시',
    missing_model: '현재신호 모델 없음',
    load_failed: '현재신호 로드 실패',
    predict_failed: '현재신호 예측 실패',
    error: '현재신호 예측 오류',
  };
  return `<span style="font-size:11px; color:var(--muted);">${labels[status] || '현재신호 예측 불가'}</span>`;
}

async function loadSavedLGBMPredictions(models) {
  if (!isAuthenticated) {
    models.forEach(m => updateSavedLGBMPrediction(m.symbol, null, 'auth_required'));
    return;
  }

  for (const model of models) {
    if (!model.has_model) {
      updateSavedLGBMPrediction(model.symbol, null, 'missing_model');
      continue;
    }
    try {
      const res = await fetch(`${API}/lgbm/saved-predict/${encodeURIComponent(model.symbol)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '예측 실패');
      updateSavedLGBMPrediction(model.symbol, data.prediction, data.prediction_status);
    } catch(e) {
      updateSavedLGBMPrediction(model.symbol, null, 'error');
    }
  }
}

function updateSavedLGBMPrediction(symbol, pred, status) {
  const el = document.getElementById(`lgbm-pred-${symbol}`);
  if (!el) return;
  el.innerHTML = renderSavedLGBMPrediction(pred, status);
  const card = el.closest('.lgbm-saved-model');
  if (card) {
    card.dataset.signal = pred?.signal || '';
    card.dataset.predStatus = status || '';
  }
  applySavedLGBMFilter();
}

// ── 공통 드롭다운 렌더/키 핸들러 ──
function renderLgbmDropdown(ddId, items, query, onSelect) {
  const dd = document.getElementById(ddId);
  if (!items.length) { dd.style.display = 'none'; return; }
  dd.innerHTML = items.map((item, i) => {
    const hl = (str) => {
      const idx = str.toLowerCase().indexOf(query.toLowerCase());
      if (idx < 0) return str;
      return str.slice(0, idx) +
        `<strong style="color:var(--text);">${str.slice(idx, idx + query.length)}</strong>` +
        str.slice(idx + query.length);
    };
    return `<div class="search-item" data-idx="${i}"
      onmousedown="(function(){document.getElementById('${ddId}').style.display='none';})();
                   ${onSelect.name}('${item.symbol}','${item.name.replace(/'/g,"\'")}')">
      <span class="search-code">${item.symbol}</span>
      <span class="search-name">${hl(item.name)}</span>
    </div>`;
  }).join('');
  dd.style.display = 'block';
}

function handleDropdownKeydown(e, ddId, results, currentIdx, setIdx, onSelect, input) {
  const dd = document.getElementById(ddId);
  const items = dd.querySelectorAll('.search-item');
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    const next = Math.min(currentIdx + 1, items.length - 1);
    setIdx(next);
    items.forEach((el, i) => el.classList.toggle('active', i === next));
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    const prev = Math.max(currentIdx - 1, 0);
    setIdx(prev);
    items.forEach((el, i) => el.classList.toggle('active', i === prev));
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (currentIdx >= 0 && results[currentIdx]) {
      const s = results[currentIdx];
      onSelect(s.symbol, s.name);
    } else if (results.length > 0) {
      onSelect(results[0].symbol, results[0].name);
    } else {
      const val = input?.value.trim();
      if (val && /^\d{6}$/.test(val)) onSelect(val, val);
    }
  } else if (e.key === 'Escape') {
    dd.style.display = 'none';
  }
}

// 드롭다운 외부 클릭 시 닫기 (기존 핸들러에 추가)
document.addEventListener('click', (e) => {
  ['lgbm-dropdown','lgbm-load-dropdown','strat-symbol-dropdown','ia-dropdown'].forEach(id => {
    const dd = document.getElementById(id);
    const searchMap = {
      'lgbm-dropdown': 'lgbm-search',
      'lgbm-load-dropdown': 'lgbm-load-search',
      'strat-symbol-dropdown': 'strat-symbol',
      'ia-dropdown': 'ia-search',
    };
    const search = searchMap[id];
    if (dd && !e.target.closest('#' + id) && !e.target.matches('#' + search)) {
      dd.style.display = 'none';
    }
  });
});

// ── LightGBM AI 파라미터 탐색 ──────────────────────────────
let lgbmPollTimer = null;
let lgbmAutoLoadedSymbol = null;

function calcLGBMProgressPct(p) {
  if (p.batch_total > 0) {
    const currentTrialPct = p.total > 0 ? Math.min(Math.max(p.trial / p.total, 0), 1) : 0;
    const completedModels = Math.max((p.batch_index || 1) - 1, 0);
    return Math.round((completedModels + currentTrialPct) / p.batch_total * 100);
  }
  return p.total > 0 ? Math.round(p.trial / p.total * 100) : 0;
}

function formatLGBMTrialText(p) {
  const trialText = `Trial ${p.trial || 0} / ${p.total || 0}`;
  if (p.batch_total > 0) {
    return `모델 ${p.batch_index || 0} / ${p.batch_total} · ${trialText}`;
  }
  return trialText;
}

async function runLGBM() {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  let symbol = document.getElementById('lgbm-symbol').value.trim();
  // hidden input이 비어있으면 검색창에서 6자리 코드 추출 시도
  if (!symbol) {
    const raw = document.getElementById('lgbm-search')?.value.trim() || '';
    const m = raw.match(/\d{6}/);
    if (m) symbol = m[0];
  }
  const n_trials = parseInt(document.getElementById('lgbm-trials').value);
  const ohlcv_count = parseInt(document.getElementById('lgbm-days').value);
  if (!symbol) { toast('종목명 또는 코드를 입력하세요', 'warn'); return; }

  setLGBMStatus('running', '데이터 수집 중...');
  lgbmAutoLoadedSymbol = null;
  document.getElementById('lgbm-result-panel').style.display = 'none';
  document.getElementById('lgbm-progress-bar').style.width = '0%';

  try {
    const res = await fetch(`${API}/lgbm/run`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({symbol, n_trials, ohlcv_count})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast(`${symbol} LightGBM 탐색 시작 (${n_trials} trials)`, 'info');
    if (lgbmPollTimer) clearInterval(lgbmPollTimer);
    lgbmPollTimer = setInterval(pollLGBMProgress, 2000);
  } catch(e) {
    toast('시작 실패: ' + e.message, 'error');
    setLGBMStatus('idle', '오류 발생');
  }
}

async function rerunSavedLGBMModels() {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }
  const n_trials = parseInt(document.getElementById('lgbm-trials').value);
  const ohlcv_count = parseInt(document.getElementById('lgbm-days').value);

  setLGBMStatus('running', '저장 모델 전체 재분석을 시작하는 중...');
  lgbmAutoLoadedSymbol = null;
  document.getElementById('lgbm-result-panel').style.display = 'none';
  document.getElementById('lgbm-progress-bar').style.width = '0%';
  document.getElementById('lgbm-trial-text').textContent = '모델 0 / 0 · Trial 0 / 0';
  document.getElementById('lgbm-best-score').textContent = 'Best F1: -';

  try {
    const res = await fetch(`${API}/lgbm/rerun-saved`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({n_trials, ohlcv_count})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast(`저장 모델 ${data.symbols.length}개 재분석 시작 (${n_trials} trials)`, 'info');
    if (lgbmPollTimer) clearInterval(lgbmPollTimer);
    lgbmPollTimer = setInterval(pollLGBMProgress, 2000);
  } catch(e) {
    toast('전체 재분석 시작 실패: ' + e.message, 'error');
    setLGBMStatus('idle', '오류 발생');
  }
}

async function stopLGBM() {
  await fetch(`${API}/lgbm/stop`, {method:'POST'});
  if (lgbmPollTimer) clearInterval(lgbmPollTimer);
  setLGBMStatus('idle', '중지됨');
  toast('LightGBM 탐색 중지', 'warn');
}

async function pollLGBMProgress() {
  try {
    const p = await fetch(`${API}/lgbm/progress`).then(r => r.json());
    const pct = calcLGBMProgressPct(p);
    document.getElementById('lgbm-progress-bar').style.width = pct + '%';
    document.getElementById('lgbm-trial-text').textContent = formatLGBMTrialText(p);
    document.getElementById('lgbm-best-score').textContent = `Best F1: ${p.best_score || '-'}`;
    if (p.message) document.getElementById('lgbm-status-msg').textContent = p.message;

    if (p.status === 'done' || p.status === 'error') {
      clearInterval(lgbmPollTimer);
      lgbmPollTimer = null;
      setLGBMStatus(p.status, p.message || '완료');
      if (p.status === 'done') {
        // WebSocket이 이미 처리했을 수 있으므로 패널이 비어있을 때만 렌더
        await autoLoadLatestLGBMModel(p.symbol);
      } else {
        toast('⚠️ 오류: ' + p.message, 'error');
      }
    } else {
      setLGBMStatus('running', p.message || '최적화 중...');
    }
  } catch(e) {}
}

async function renderLGBMResult() {
  const result = await fetch(`${API}/lgbm/result`).then(r => r.json());
  if (!result || result.status === 'idle') return;

  document.getElementById('lgbm-result-panel').style.display = 'block';

  // 백테스트 지표 — 값이 없으면 '-' 로 안전하게 표시
  const bt = result.backtest || {};
  const toFixed = (v, d=2) => (v !== undefined && v !== null && !isNaN(v)) ? Number(v).toFixed(d) : '-';
  const pnlVal = bt.total_return;
  const pnlCls = (pnlVal !== undefined && pnlVal >= 0) ? 'up' : 'down';
  const pnlStr = (pnlVal !== undefined && !isNaN(pnlVal))
    ? (pnlVal >= 0 ? '+' : '') + toFixed(pnlVal) + '%' : '-';
  document.getElementById('lgbm-bt-metrics').innerHTML = [
    {label:'총 수익률', value: pnlStr,                        cls: pnlCls},
    {label:'MDD',      value: toFixed(bt.mdd) + (bt.mdd !== undefined ? '%' : ''), cls:'down'},
    {label:'승률',     value: toFixed(bt.win_rate) + (bt.win_rate !== undefined ? '%' : ''), cls:'neutral'},
    {label:'샤프비율', value: toFixed(bt.sharpe, 3),          cls:'neutral'},
  ].map(m => `
    <div class="metric-card">
      <div class="metric-label">${m.label}</div>
      <div class="metric-value ${m.cls}" style="font-size:18px;">${m.value}</div>
    </div>`).join('');

  renderLGBMCVCompare(result.cv_compare);

  // 최적 파라미터 뱃지
  const p = result.best_params || {};
  document.getElementById('lgbm-params').innerHTML = Object.entries(p).map(([k,v]) => {
    const info = getLGBMParamInfo(k);
    const value = formatLGBMParamValue(k, v);
    return `<span class="param-chip" tabindex="0" title="${escapeHtml(info.description)}">
      <span>
        <span style="color:var(--muted);">${escapeHtml(info.label)}</span>
        <span class="param-key">(${escapeHtml(k)})</span>
      </span>
      <strong style="color:var(--accent);">${escapeHtml(value)}</strong>
      <span class="param-help">?</span>
      <span class="param-tooltip">
        <strong>${escapeHtml(info.label)}</strong>
        ${escapeHtml(info.description)}
      </span>
    </span>`;
  }).join('');

  // 피처 중요도 바 차트
  const imp = result.importance || [];
  const maxScore = imp[0]?.score || 1;
  document.getElementById('lgbm-importance').innerHTML = imp.map(item => `
    <div style="display:flex; align-items:center; gap:8px;">
      <div style="width:130px; font-size:11px; color:var(--muted); text-align:right; flex-shrink:0;">${item.feature}</div>
      <div style="flex:1; background:var(--surface2); border-radius:4px; height:14px; overflow:hidden;">
        <div style="height:100%; background:var(--accent); border-radius:4px;
          width:${Math.round(item.score/maxScore*100)}%; transition:width .5s;"></div>
      </div>
      <div style="font-size:11px; color:var(--muted); font-family:var(--mono); width:40px;">${item.score}</div>
    </div>`).join('');
}

async function predictLGBM() {
  const symbol = document.getElementById('lgbm-symbol').value.trim()
              || document.getElementById('lgbm-load-symbol').value.trim()
              || document.getElementById('lgbm-search')?.value.match(/\d{6}/)?.[0]
              || '';
  if (!symbol) { toast('종목명 또는 코드를 입력하세요', 'warn'); return; }
  try {
    const res = await fetch(`${API}/lgbm/predict/${symbol}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    const sigColor = {BUY:'var(--green)', SELL:'var(--red)', HOLD:'var(--yellow)'};
    const sigEmoji = {BUY:'🟢', SELL:'🔴', HOLD:'⚪'};
    const el = document.getElementById('lgbm-predict-result');
    el.style.display = 'block';
    el.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;">
        <span style="font-size:16px; font-weight:600; color:${sigColor[data.signal]};">
          ${sigEmoji[data.signal]} ${data.signal}
        </span>
        <span style="font-size:12px; color:var(--muted);">${symbol} 현재 신호</span>
      </div>
      <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:8px;">
        ${[['매수 확률', data.prob_buy, 'var(--green)'],
           ['HOLD 확률', data.prob_hold,'var(--muted)'],
           ['매도 확률', data.prob_sell,'var(--red)']].map(([label, prob, color]) => `
          <div style="text-align:center;">
            <div style="font-size:11px; color:var(--muted); margin-bottom:4px;">${label}</div>
            <div style="font-size:18px; font-weight:600; color:${color}; font-family:var(--mono);">
              ${(prob*100).toFixed(1)}%
            </div>
            <div style="background:var(--surface); border-radius:4px; height:6px; margin-top:4px; overflow:hidden;">
              <div style="height:100%; background:${color}; width:${(prob*100).toFixed(0)}%;"></div>
            </div>
          </div>`).join('')}
      </div>`;
    toast(`예측 완료: ${data.signal}`, data.signal === 'BUY' ? 'success' : data.signal === 'SELL' ? 'error' : 'info');
  } catch(e) {
    toast('예측 실패: ' + e.message, 'error');
  }
}

function syncLGBMLoadedSymbol(symbol, label) {
  document.getElementById('lgbm-load-symbol').value = symbol;
  document.getElementById('lgbm-symbol').value = symbol;
  document.getElementById('lgbm-search').value = label || symbol;
  document.getElementById('lgbm-load-search').value = label || symbol;
  document.getElementById('lgbm-selected-code').textContent = symbol;
  document.getElementById('lgbm-selected-name').textContent = label && label !== symbol ? label.replace(/\s*\(\d{6}\)\s*$/, '') : '';
  document.getElementById('lgbm-selected').style.display = 'flex';
}

async function loadLGBMModelBySymbol(symbol, label, options = {}) {
  if (!symbol) { toast('종목코드를 입력하세요', 'warn'); return; }
  try {
    const res = await fetch(`${API}/lgbm/load/${symbol}`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    syncLGBMLoadedSymbol(symbol, label || symbol);
    await renderLGBMResult();
    await predictLGBM();
    if (!options.silent) toast(`${symbol} 모델 불러오기 완료`, 'success');
    return data;
  } catch(e) {
    toast('불러오기 실패: ' + e.message, 'error');
    return null;
  }
}

async function autoLoadLatestLGBMModel(symbol) {
  symbol = symbol || document.getElementById('lgbm-symbol').value.trim();
  if (!symbol || lgbmAutoLoadedSymbol === symbol) return;
  const label = document.getElementById('lgbm-search')?.value || symbol;
  const data = await loadLGBMModelBySymbol(symbol, label, {silent: true});
  if (!data) return;
  lgbmAutoLoadedSymbol = symbol;
  toast(`✅ LightGBM 최적화 완료 · ${symbol} 모델 자동 불러오기 완료`, 'success');
}

async function loadLGBMModel() {
  const symbol = document.getElementById('lgbm-load-symbol').value.trim();
  const label = document.getElementById('lgbm-load-search')?.value || symbol;
  await loadLGBMModelBySymbol(symbol, label);
}

function setLGBMStatus(status, msg) {
  const badge = document.getElementById('lgbm-status-badge');
  const msgEl = document.getElementById('lgbm-status-msg');
  const map = {
    idle:    ['badge-hold',  '대기중'],
    running: ['badge-live',  '실행중'],
    done:    ['badge-buy',   '완료'],
    error:   ['badge-sell',  '오류'],
  };
  const [cls, label] = map[status] || map.idle;
  badge.className = `badge ${cls}`;
  badge.textContent = label;
  if (msg) msgEl.textContent = msg;
}

// WebSocket에서 LGBM 이벤트 수신 (기존 _baseOnMessage 확장)
