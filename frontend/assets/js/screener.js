// ── 스크리너 ──────────────────────────────────────────────
let scHitCount = 0;
let scProgressTimer = null;

function toggleCond(name, forceState) {
  const cb = document.getElementById('use-' + name);
  const panel = document.getElementById('cond-' + name);
  if (!cb || !panel) return;
  const show = forceState !== undefined ? forceState : !cb.checked;
  cb.checked = show;
  panel.style.display = show ? 'block' : 'none';
}

async function runScreener() {
  if (!isAuthenticated) { toast('먼저 인증이 필요합니다', 'warn'); return; }

  const g = (id) => document.getElementById(id);
  const flt = (id) => { const v = g(id)?.value; return v !== '' && v != null ? parseFloat(v) : null; };
  const int_ = (id, def) => parseInt(g(id)?.value || def);

  const payload = {
    universe: g('sc-universe').value,
    use_rsi:      g('use-rsi').checked,
    rsi_period:   int_('sc-rsi-period', 14),
    rsi_min:      flt('sc-rsi-min'),
    rsi_max:      flt('sc-rsi-max'),
    use_bollinger: g('use-bollinger').checked,
    bb_period:    int_('sc-bb-period', 20),
    bb_k:         parseFloat(g('sc-bb-k')?.value || 2),
    bb_position:  g('sc-bb-position')?.value || 'below_lower',
    use_macd:     g('use-macd').checked,
    macd_fast:    int_('sc-macd-fast', 12),
    macd_slow:    int_('sc-macd-slow', 26),
    macd_signal:  int_('sc-macd-signal', 9),
    macd_cross:   g('sc-macd-cross')?.value || 'golden',
    use_ma_cross: g('use-macross').checked,
    ma_short:     int_('sc-ma-short', 5),
    ma_long:      int_('sc-ma-long', 20),
    ma_cross:     g('sc-ma-cross')?.value || 'golden',
    use_volume:   g('use-volume').checked,
    volume_ratio: parseFloat(g('sc-vol-ratio')?.value || 2),
    volume_avg_days: int_('sc-vol-days', 20),
    use_change:   g('use-change').checked,
    change_min:   flt('sc-chg-min'),
    change_max:   flt('sc-chg-max'),
    use_near_high: g('use-trend').checked,
    high_days:    int_('sc-high-days', 20),
    high_within_pct: parseFloat(g('sc-high-pct')?.value || 3),
    use_above_ma60: g('use-trend').checked && !!g('sc-above-ma60')?.checked,
    use_foreign:  g('use-quality').checked,
    foreign_days: int_('sc-foreign-days', 5),
    use_fundamental: g('use-quality').checked,
    growth_metric: g('sc-growth-metric')?.value || 'any',
    growth_min:   parseFloat(g('sc-growth-min')?.value || 0),
    use_ai:        g('use-ai').checked,
    ai_signal:     g('sc-ai-signal')?.value || 'BUY',
    ai_min_prob:   parseFloat(g('sc-ai-prob')?.value || 45) / 100,
  };

  const conds = ['rsi','bollinger','macd','macross','volume','change','trend','quality','ai'];
  const anyChecked = conds.some(c => {
    const el = document.getElementById('use-' + c);
    return el && el.checked;
  });
  if (!anyChecked) { toast('조건을 최소 1개 이상 선택하세요', 'warn'); return; }

  // UI 초기화
  scHitCount = 0;
  document.getElementById('screener-body').innerHTML =
    '<tr><td colspan="7" style="text-align:center; color:var(--muted); padding:24px;">스크리닝 중...</td></tr>';
  document.getElementById('sc-hit-count').textContent = '조건 충족: 0건';
  document.getElementById('sc-progress-bar').style.width = '0%';
  document.getElementById('sc-progress-label').textContent = '실행중...';

  try {
    const res = await fetch(`${API}/screener/run`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    toast(`스크리닝 시작 (${data.total}개 종목)`, 'info');
    // 진행률 폴링
    if (scProgressTimer) clearInterval(scProgressTimer);
    scProgressTimer = setInterval(pollScreenerProgress, 1500);
  } catch(err) {
    toast('스크리닝 실패: ' + err.message, 'error');
  }
}

async function pollScreenerProgress() {
  try {
    const p = await fetch(`${API}/screener/progress`).then(r => r.json());
    document.getElementById('sc-progress-bar').style.width = (p.pct || 0) + '%';
    document.getElementById('sc-progress-text').textContent = `${p.done} / ${p.total}`;
    document.getElementById('sc-progress-label').textContent = `${p.pct || 0}%`;
    if (p.status === 'done') {
      clearInterval(scProgressTimer);
      document.getElementById('sc-progress-label').textContent = '완료';
      await loadScreenerResult();
      toast('스크리닝 완료!', 'success');
    }
  } catch(e) {}
}

async function stopScreener() {
  await fetch(`${API}/screener/stop`, { method: 'POST' });
  if (scProgressTimer) clearInterval(scProgressTimer);
  document.getElementById('sc-progress-label').textContent = '중지됨';
  toast('스크리닝 중지', 'warn');
}

function startLGBMFromScreener(symbol, name) {
  const lgbmNav = Array.from(document.querySelectorAll('.nav-item'))
    .find(btn => btn.getAttribute('onclick')?.includes("showPage('lgbm'"));
  showPage('lgbm', lgbmNav);
  selectLgbmStock(symbol, name || symbol);
  document.getElementById('lgbm-predict-result').style.display = 'none';
  setTimeout(() => runLGBM(), 100);
}

async function loadScreenerResult() {
  try {
    const results = await fetch(`${API}/screener/result`).then(r => r.json());
    const tbody = document.getElementById('screener-body');
    if (!results.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--muted); padding:32px;">조건에 맞는 종목이 없습니다</td></tr>';
      return;
    }
    document.getElementById('sc-hit-count').textContent = `조건 충족: ${results.length}건`;
    tbody.innerHTML = results.map(r => {
      const pct = parseFloat(r.change_pct);
      const cls = pct >= 0 ? 'up' : 'down';
      const conds = r.conditions.map(c => `<span class="badge badge-live" style="font-size:10px; margin:1px;">${c}</span>`).join('');
      const ai = r.ai;
      const aiHtml = ai ? (() => {
        const sigCls = ai.signal === 'BUY' ? 'badge-buy' : ai.signal === 'SELL' ? 'badge-sell' : 'badge-hold';
        return `<div style="display:flex; flex-direction:column; gap:4px;">
          <span class="badge ${sigCls}" style="width:max-content;">${ai.signal}</span>
          <span style="font-size:11px; color:var(--muted); font-family:var(--mono);">
            B ${(ai.prob_buy*100).toFixed(1)} · H ${(ai.prob_hold*100).toFixed(1)} · S ${(ai.prob_sell*100).toFixed(1)}
          </span>
        </div>`;
      })() : '<span style="font-size:11px; color:var(--muted);">-</span>';
      const inds = Object.entries(r.indicators)
        .map(([k,v]) => `<span style="font-size:11px; color:var(--muted);">${k}: <strong style="color:var(--text);">${typeof v === 'number' ? (v > 1000 ? fmt(v) : v) : v}</strong></span>`)
        .join(' &nbsp;');
      return `<tr>
        <td><strong>${r.symbol}</strong><br><span style="font-size:11px;color:var(--muted);">${r.name}</span></td>
        <td style="font-family:var(--mono);">${fmtWon(r.price)}</td>
        <td class="${cls}" style="font-family:var(--mono);">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
        <td>${aiHtml}</td>
        <td>${conds}</td>
        <td style="font-size:11px;">${inds}</td>
        <td>
          <button class="btn btn-outline" style="padding:3px 8px; font-size:11px;"
            onclick="showPage('quote', document.querySelector('.nav-item:nth-child(4)')); setTimeout(()=>selectStock('${r.symbol}','${r.name}'),50);">
            차트
          </button>
          <button class="btn btn-primary" style="padding:3px 8px; font-size:11px; margin-top:4px;"
            onclick='startLGBMFromScreener(${JSON.stringify(r.symbol)}, ${JSON.stringify(r.name)})'>
            AI 탐색
          </button>
        </td>
      </tr>`;
    }).join('');
  } catch(e) {
    toast('결과 조회 실패: ' + e.message, 'error');
  }
}

// WebSocket에서 실시간 결과 수신
const _origOnMessage = null;

// ws onmessage 확장 - 기존 핸들러 이후에 screener 처리
const _baseOnMessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === 'trade') {
    const d = msg.data;
    const cls = msg.signal === 'BUY' ? 'log-buy' : 'log-sell';
    appendLog('strategy-log-dash', d.time, `[${msg.signal}] ${d.action}`, cls);
    appendLog('strategy-log-full', d.time, `[${msg.signal}] ${d.action} | ${formatStrategyLogEntry(d)}`, cls);
    loadStrategyStatus();
    toast(`${msg.signal === 'BUY' ? '🟢 매수' : '🔴 매도'} ${d.action}`, msg.signal === 'BUY' ? 'success' : 'error');
  } else if (msg.type === 'signal') {
    const d = msg.data;
    appendLog('strategy-log-full', d.time, `[${d.signal || 'HOLD'}] ${formatStrategyLogEntry(d)}`, 'log-hold');
    loadStrategyStatus();
  } else if (msg.type === 'screener_hit') {
    scHitCount++;
    document.getElementById('sc-hit-count').textContent = `조건 충족: ${scHitCount}건`;
  } else if (msg.type === 'screener_done') {
    loadScreenerResult();
  } else if (msg.type === 'lgbm') {
    const p = msg.progress || {};
    const pct = calcLGBMProgressPct(p);
    const bar = document.getElementById('lgbm-progress-bar');
    if (bar) bar.style.width = pct + '%';
    const tt  = document.getElementById('lgbm-trial-text');
    if (tt) tt.textContent = formatLGBMTrialText(p);
    const bs  = document.getElementById('lgbm-best-score');
    if (bs) bs.textContent = `Best F1: ${p.best_score || '-'}`;
    const sm  = document.getElementById('lgbm-status-msg');
    if (sm && msg.message) sm.textContent = msg.message;
    if (msg.status === 'done') {
      if (lgbmPollTimer) { clearInterval(lgbmPollTimer); lgbmPollTimer = null; }
      setLGBMStatus('done', msg.message);
      // 서버가 _result를 저장하는 시간 여유를 주고 조회
      setTimeout(() => autoLoadLatestLGBMModel(p.symbol), 700);
    } else if (msg.status === 'error') {
      if (lgbmPollTimer) { clearInterval(lgbmPollTimer); lgbmPollTimer = null; }
      setLGBMStatus('error', msg.message);
    }
  } else if (msg.type === 'intraday_ai_progress') {
    updateIntradayProgress(msg.done || 0, msg.total || 0, msg.pct || 0);
  } else if (msg.type === 'intraday_ai_hit') {
    const countEl = document.getElementById('ia-m-count');
    if (countEl) countEl.textContent = String((parseInt(countEl.textContent) || 0) + 1);
  } else if (msg.type === 'intraday_ai_done') {
    renderIntradayRank(msg.data || []);
    setIntradayStatus('done', `랭킹 완료 · ${msg.count || 0}개 후보`);
  } else if (msg.type === 'intraday_trade') {
    // 단타 자동매매 신호
    if (msg.status === 'position_opened') {
      const log = msg.data;
      appendIntradaySignal(`✅ 진입: ${log.symbol} ${log.qty}주 @ ₩${log.entry_price.toLocaleString('ko-KR')}`, 'success');
    } else if (msg.status === 'position_closed') {
      const log = msg.data;
      const pnl = log.pnl || 0;
      appendIntradaySignal(`❌ 청산: ${log.symbol} ${log.reason} | PnL: ${pnl >= 0 ? '+' : ''}₩${pnl.toLocaleString('ko-KR')}`, pnl >= 0 ? 'success' : 'error');
    } else if (msg.status === 'candidate_found') {
      appendIntradaySignal(`🎯 후보: ${msg.symbol} (확률: ${(msg.probability*100).toFixed(1)}%)`, 'info');
    } else if (msg.status === 'position_update') {
      const pos = msg.position;
      if (pos) {
        const pnlColor = pos.pnl_pct >= 0 ? 'green' : pos.pnl_pct <= -1 ? 'red' : 'yellow';
        appendIntradaySignal(`${pos.symbol}: ₩${pos.current_price.toLocaleString('ko-KR')} (${pos.pnl_pct.toFixed(2)}%)`, pnlColor);
      }
    }
  }
};


