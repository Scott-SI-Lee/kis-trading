// ── 시계 ──
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('ko-KR');
}
setInterval(updateClock, 1000);
updateClock();
// checkEnvAuth는 DOM 로드 완료 후 실행 (함수 정의보다 먼저 호출되면 안 됨)

// ── 페이지 전환 ──
function showPage(name, btn) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  btn.classList.add('active');
  if (name === 'intraday-ai') {
    loadIntradayModels();
    loadIntradayRankResult();
  }
  if (name === 'us-close') {
    loadUSCloseAnalysis();
  }
  if (name === 'strategy') {
    loadStrategyStatus();
    loadStrategyLog();
  }
}

// ── 토스트 ──
function toast(msg, type = 'info') {
  const colors = { info: '#3b82f6', success: '#22c55e', error: '#ef4444', warn: '#f59e0b' };
  const el = document.createElement('div');
  el.className = 'toast';
  el.style.borderLeft = `3px solid ${colors[type] || colors.info}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ── 숫자 포맷 ──
function fmt(n) { return Number(n).toLocaleString('ko-KR'); }
function fmtWon(n) { return fmt(n) + '원'; }
function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function fmtSignedPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'N/A';
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}

function badgeForPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'badge-hold';
  if (n > 0) return 'badge-buy';
  if (n < 0) return 'badge-sell';
  return 'badge-hold';
}

function safeJoin(list, fallback = '-') {
  return Array.isArray(list) && list.length ? list.join('; ') : fallback;
}

async function loadUSCloseAnalysis() {
  const hoursEl = document.getElementById('us-close-hours');
  const perSourceEl = document.getElementById('us-close-per-source');
  const hours = hoursEl ? Number(hoursEl.value || 24) : 24;
  const perSource = perSourceEl ? Number(perSourceEl.value || 10) : 10;
  const res = await fetch(`${API}/us-close-analysis?hours=${encodeURIComponent(hours)}&news_per_source=${encodeURIComponent(perSource)}`);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || '분석 불러오기 실패');
  }
  renderUSCloseAnalysis(data);
  return data;
}

async function runUSCloseAnalysis() {
  const status = document.getElementById('us-close-status');
  try {
    if (status) {
      status.textContent = 'RUNNING';
      status.className = 'badge badge-hold';
    }
    await loadUSCloseAnalysis();
    if (status) {
      status.textContent = 'READY';
      status.className = 'badge badge-live';
    }
    toast('미국 마감 분석을 갱신했습니다', 'success');
  } catch (e) {
    if (status) {
      status.textContent = 'ERROR';
      status.className = 'badge badge-sell';
    }
    toast('미국 마감 분석 실패: ' + e.message, 'error');
  }
}

async function sendUSCloseTelegram() {
  const hoursEl = document.getElementById('us-close-hours');
  const perSourceEl = document.getElementById('us-close-per-source');
  const hours = hoursEl ? Number(hoursEl.value || 24) : 24;
  const perSource = perSourceEl ? Number(perSourceEl.value || 10) : 10;
  try {
    const res = await fetch(`${API}/us-close-analysis/send-telegram?hours=${encodeURIComponent(hours)}&news_per_source=${encodeURIComponent(perSource)}`, {
      method: 'POST'
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '텔레그램 전송 실패');
    if (data.telegram_sent) {
      toast('텔레그램으로 전송했습니다', 'success');
    } else {
      const detail = data.telegram_detail || '텔레그램 전송 설정이 비활성화되어 있습니다';
      toast(`텔레그램 전송 실패: ${detail}`, 'error');
    }
  } catch (e) {
    toast('텔레그램 전송 실패: ' + e.message, 'error');
  }
}

function renderUSCloseAnalysis(data) {
  const summary = document.getElementById('us-close-summary');
  const upBody = document.getElementById('us-close-up-body');
  const downBody = document.getElementById('us-close-down-body');
  const marketBody = document.getElementById('us-close-market-body');
  const newsBody = document.getElementById('us-close-news-body');
  const jsonBox = document.getElementById('us-close-json');
  const generated = document.getElementById('us-close-generated');
  const signal = document.getElementById('us-close-news-signal');

  if (generated) generated.textContent = data.generated_at_kst || '-';
  if (signal) {
    const s = data.news_signal || {};
    signal.textContent = `positive ${s.positive_hits ?? 0} / negative ${s.negative_hits ?? 0} / net ${s.net_score ?? 0}`;
  }

  if (summary) {
    const summaryItems = Array.isArray(data.summary) ? data.summary : [];
    summary.innerHTML = summaryItems.length
      ? summaryItems.map(item => `<div style="padding:10px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:8px;">${escapeHtml(item)}</div>`).join('')
      : '<div style="color:var(--muted);">요약이 없습니다.</div>';
  }

  const up = (data.recommendations && data.recommendations.up) || [];
  const down = (data.recommendations && data.recommendations.down) || [];
  upBody.innerHTML = up.length ? up.map(item => `
    <tr>
      <td><div style="font-weight:500;">${escapeHtml(item.name)} <span style="font-family:var(--mono); color:var(--muted);">(${escapeHtml(item.symbol)})</span></div></td>
      <td><span class="badge ${item.direction === 'up' ? 'badge-buy' : 'badge-hold'}">${Number(item.score || 0).toFixed(2)}</span></td>
      <td style="white-space:normal; line-height:1.6;">${escapeHtml(safeJoin(item.reasons))}</td>
    </tr>
  `).join('') : '<tr><td colspan="3" style="text-align:center; color:var(--muted); padding:24px;">추천 없음</td></tr>';

  downBody.innerHTML = down.length ? down.map(item => `
    <tr>
      <td><div style="font-weight:500;">${escapeHtml(item.name)} <span style="font-family:var(--mono); color:var(--muted);">(${escapeHtml(item.symbol)})</span></div></td>
      <td><span class="badge badge-sell">${Number(item.score || 0).toFixed(2)}</span></td>
      <td style="white-space:normal; line-height:1.6;">${escapeHtml(safeJoin(item.risks))}</td>
    </tr>
  `).join('') : '<tr><td colspan="3" style="text-align:center; color:var(--muted); padding:24px;">위험 후보 없음</td></tr>';

  const market = Array.isArray(data.market_data) ? data.market_data : [];
  marketBody.innerHTML = market.length ? market.map(item => `
    <tr>
      <td><div style="font-weight:500;">${escapeHtml(item.label)}</div><div style="font-size:11px; color:var(--muted); font-family:var(--mono);">${escapeHtml(item.symbol)}</div></td>
      <td><span class="badge ${badgeForPct(item.change_pct)}">${fmtSignedPct(item.change_pct)}</span></td>
      <td style="white-space:normal; line-height:1.6;">${item.error ? escapeHtml(item.error) : 'OK'}</td>
    </tr>
  `).join('') : '<tr><td colspan="3" style="text-align:center; color:var(--muted); padding:24px;">데이터 없음</td></tr>';

  const news = Array.isArray(data.news) ? data.news : [];
  newsBody.innerHTML = news.length ? news.map(item => `
    <tr>
      <td><span class="badge badge-live">${escapeHtml(item.source)}</span></td>
      <td style="white-space:normal; line-height:1.6;">
        <a href="${escapeHtml(item.link)}" target="_blank" rel="noreferrer" style="color:var(--text); text-decoration:none;">${escapeHtml(item.title)}</a>
      </td>
      <td style="font-family:var(--mono); color:var(--muted);">${escapeHtml(String(item.published_at || '').replace('T', ' ').slice(0, 19))}</td>
    </tr>
  `).join('') : '<tr><td colspan="3" style="text-align:center; color:var(--muted); padding:24px;">최근 뉴스 없음</td></tr>';

  if (jsonBox) {
    jsonBox.value = JSON.stringify(data, null, 2);
  }
}

const LGBM_PARAM_HELP = {
  horizon: {
    label: '예측 기간',
    description: '며칠 뒤 수익률을 기준으로 매수, 보유, 매도 학습 라벨을 만들지 정합니다.'
  },
  buy_threshold: {
    label: '매수 기준 수익률',
    description: '예측 기간 뒤 수익률이 이 값 이상이면 매수로 학습합니다. 높을수록 더 강한 상승 신호만 매수로 봅니다.'
  },
  sell_threshold: {
    label: '매도 기준 수익률',
    description: '예측 기간 뒤 수익률이 이 값 이하이면 매도로 학습합니다. 더 낮을수록 더 강한 하락 신호만 매도로 봅니다.'
  },
  n_estimators: {
    label: '학습 트리 수',
    description: 'LightGBM이 만드는 결정 트리 개수입니다. 많을수록 세밀하게 학습하지만 시간이 길고 과적합 위험이 커질 수 있습니다.'
  },
  learning_rate: {
    label: '학습 속도',
    description: '각 트리가 모델을 얼마나 크게 보정할지 정합니다. 낮을수록 천천히 안정적으로 학습하는 대신 더 많은 트리가 필요합니다.'
  },
  num_leaves: {
    label: '트리 잎 개수',
    description: '각 트리가 나눌 수 있는 최대 말단 노드 수입니다. 클수록 복잡한 패턴을 잡지만 과적합 가능성도 커집니다.'
  },
  max_depth: {
    label: '트리 최대 깊이',
    description: '트리가 몇 단계까지 조건을 나눌 수 있는지 제한합니다. 깊을수록 복잡한 조건을 표현합니다.'
  },
  min_child_samples: {
    label: '최소 샘플 수',
    description: '하나의 잎 노드에 필요한 최소 데이터 개수입니다. 높을수록 작은 노이즈 패턴을 덜 따라갑니다.'
  },
  subsample: {
    label: '행 샘플 비율',
    description: '각 학습 단계에서 사용할 데이터 행의 비율입니다. 일부 데이터만 써서 과적합을 줄이는 역할을 합니다.'
  },
  colsample_bytree: {
    label: '피처 샘플 비율',
    description: '각 트리가 사용할 피처 비율입니다. 일부 지표만 골라 학습해 특정 피처에 지나치게 의존하는 것을 줄입니다.'
  },
  reg_alpha: {
    label: 'L1 규제',
    description: '불필요한 영향력을 줄이는 규제 강도입니다. 값이 클수록 모델을 더 단순하게 만드는 압력이 커집니다.'
  },
  reg_lambda: {
    label: 'L2 규제',
    description: '큰 가중치를 완만하게 누르는 규제 강도입니다. 값이 클수록 과도한 학습을 줄이는 압력이 커집니다.'
  }
};

function getLGBMParamInfo(key) {
  return LGBM_PARAM_HELP[key] || {
    label: key,
    description: 'LightGBM 또는 전략 최적화 과정에서 선택된 파라미터입니다.'
  };
}

function formatLGBMParamValue(key, value) {
  const n = Number(value);
  if (Number.isFinite(n) && ['buy_threshold', 'sell_threshold'].includes(key)) {
    return `${(n * 100).toFixed(2)}%`;
  }
  if (Number.isFinite(n) && ['subsample', 'colsample_bytree'].includes(key)) {
    return `${(n * 100).toFixed(1)}%`;
  }
  if (Number.isFinite(n) && ['learning_rate', 'reg_alpha', 'reg_lambda'].includes(key)) {
    return n.toFixed(4);
  }
  return value;
}

function renderLGBMCVCompare(cv) {
  const panel = document.getElementById('lgbm-cv-panel');
  const wrap = document.getElementById('lgbm-cv-compare');
  if (!panel || !wrap) return;
  if (!cv || !cv.models) {
    panel.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }

  const modelNames = { lightgbm: 'LightGBM', xgboost: 'XGBoost' };
  wrap.innerHTML = ['lightgbm', 'xgboost'].map(key => {
    const m = cv.models[key];
    if (!m) return '';
    if (m.status === 'missing_dependency') {
      return `<div class="metric-card">
        <div class="metric-label">${modelNames[key]}</div>
        <div style="font-size:12px; color:var(--yellow); line-height:1.6;">${escapeHtml(m.message || '패키지 미설치')}</div>
      </div>`;
    }
    const isWinner = cv.winner === key;
    const folds = Array.isArray(m.folds) && m.folds.length ? m.folds.join(', ') : '-';
    return `<div class="metric-card" style="${isWinner ? 'border-color:rgba(34,197,94,0.35);' : ''}">
      <div class="metric-label">${modelNames[key]} ${isWinner ? '<span class="badge badge-buy" style="margin-left:4px;">BEST</span>' : ''}</div>
      <div class="metric-value ${isWinner ? 'up' : 'neutral'}" style="font-size:18px;">${Number(m.mean_f1 || 0).toFixed(4)}</div>
      <div style="font-size:11px; color:var(--muted); font-family:var(--mono); margin-top:6px;">
        std ${Number(m.std_f1 || 0).toFixed(4)} · folds ${escapeHtml(folds)}
      </div>
    </div>`;
  }).join('');
  panel.style.display = 'block';
}

// ── .env 자동 인증 체크 ──
async function checkEnvAuth() {
  try {
    const res = await fetch(`${API}/env-status`);
    const data = await res.json();

    // .env 카드 표시
    const hasAny = data.mock.configured || data.real.configured;
    if (hasAny) {
      document.getElementById('env-accounts').style.display = 'flex';
    }
    if (data.mock.configured) {
      document.getElementById('env-mock-card').style.display = 'block';
      document.getElementById('env-mock-account').textContent = data.mock.account_no;
      document.getElementById('env-mock-key').textContent = 'APP KEY: ' + data.mock.app_key_preview;
    }
    if (data.real.configured) {
      document.getElementById('env-real-card').style.display = 'block';
      document.getElementById('env-real-account').textContent = data.real.account_no;
      document.getElementById('env-real-key').textContent = 'APP KEY: ' + data.real.app_key_preview;
    }

    // 프로파일 뱃지 표시
    if (data.profile) {
      document.getElementById('profile-badge').style.display = 'inline-flex';
      document.getElementById('profile-name').textContent = data.profile;
      // 프로파일별 색상
      const colors = { default:'var(--muted)', local:'var(--accent)', dev:'var(--yellow)', prod:'var(--red)' };
      document.getElementById('profile-name').style.color = colors[data.profile] || 'var(--green)';
    }

      // 이미 자동 인증됐으면 바로 입장
    if (data.already_authed) {
      const mode = data.current_mode;
      const info = data[mode];
      setAuthenticated(mode, info?.account_no);
      return;
    }

    // .env 키는 있지만 startup 인증이 아직 진행중 → 재시도
    if ((data.mock.configured || data.real.configured) && !data.already_authed) {
      _envRetryCount = (_envRetryCount || 0) + 1;
      if (_envRetryCount <= 5) {
        setTimeout(checkEnvAuth, 2000);
        return;  // 팝업 없이 대기
      }
      // 5회 재시도 후에도 안 되면 팝업 표시 (카드 방식)
      _envRetryCount = 0;
    }

    // .env에 키 자체가 없으면 팝업 표시
    if (!data.mock.configured && !data.real.configured) {
      document.getElementById('auth-overlay').style.display = 'flex';
    }

  } catch(e) {
    // 서버 미실행 상태거나 CORS 문제 → 재시도
    _envRetryCount = (_envRetryCount || 0) + 1;
    if (_envRetryCount <= 3) {
      setTimeout(checkEnvAuth, 1500);
    } else {
      // 서버 자체가 없으면 팝업 표시
      document.getElementById('auth-overlay').style.display = 'flex';
    }
  }
}
let _envRetryCount = 0;

function setAuthenticated(mode, accountNo) {
  isAuthenticated = true;
  document.getElementById('auth-overlay').style.display = 'none';
  const label = mode === 'mock' ? '모의투자' : '실전투자';
  document.getElementById('conn-label').textContent = `${accountNo || ''} (${label})`;
  updateModeSwitcher(mode);
  if (!ws || ws.readyState !== WebSocket.OPEN) connectWS();
  // 동시 호출 시 API 초당 제한 발생 → 순차 호출
  loadBalance().then(() => setTimeout(loadPositions, 300));
  loadStrategyStatus();
}

function updateModeSwitcher(mode) {
  document.getElementById('mode-switcher').style.display = 'flex';
  const btnMock = document.getElementById('btn-mock');
  const btnReal = document.getElementById('btn-real');
  if (mode === 'mock') {
    btnMock.style.background = 'var(--accent-dim)'; btnMock.style.color = 'var(--accent)';
    btnReal.style.background = 'transparent';       btnReal.style.color = 'var(--muted)';
  } else {
    btnReal.style.background = 'var(--red-dim)'; btnReal.style.color = 'var(--red)';
    btnMock.style.background = 'transparent';    btnMock.style.color = 'var(--muted)';
  }
}

async function connectFromEnv(mode) {
  try {
    const res = await fetch(`${API}/switch-mode`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ mode })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    setAuthenticated(mode, data.account_no);
    const label = mode === 'mock' ? '모의투자' : '실전투자';
    toast(`${label} 계좌로 연결됐습니다`, 'success');
  } catch(err) {
    toast('연결 실패: ' + err.message, 'error');
  }
}

async function switchMode(mode) {
  if (!isAuthenticated) return;
  const label = mode === 'mock' ? '모의투자' : '실전투자';
  if (mode === 'real') {
    if (!confirm(`⚠️ 실전투자 계좌로 전환합니다.\n실제 자금이 거래될 수 있습니다. 계속하시겠습니까?`)) return;
  }
  try {
    const res = await fetch(`${API}/switch-mode`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ mode })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    document.getElementById('conn-label').textContent = `${data.account_no} (${label})`;
    updateModeSwitcher(mode);
    loadBalance();
    loadPositions();
    toast(`${label} 계좌로 전환됐습니다`, mode === 'real' ? 'warn' : 'success');
  } catch(err) {
    toast('전환 실패: ' + err.message, 'error');
  }
}

// ── WebSocket 연결 ──
function connectWS() {
  ws = new WebSocket('ws://localhost:8000/ws');
  ws.onopen = () => {
    document.getElementById('conn-dot').classList.add('live');
    document.getElementById('conn-label').textContent = '실시간 연결됨';
  };
  ws.onclose = () => {
    document.getElementById('conn-dot').classList.remove('live');
    document.getElementById('conn-label').textContent = '연결 끊김';
    setTimeout(connectWS, 3000);
  };
  ws.onmessage = _baseOnMessage;
}

function appendLog(containerId, time, text, cls) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const line = document.createElement('div');
  line.className = 'log-line';
  const t = new Date(time).toLocaleTimeString('ko-KR');
  line.innerHTML = `<span class="log-time">${t}</span><span class="${cls}">${text}</span>`;
  container.appendChild(line);
  container.scrollTop = container.scrollHeight;
}

// ── 인증 ──
async function authenticate() {
  const appKey = document.getElementById('inp-appkey').value.trim();
  const appSecret = document.getElementById('inp-appsecret').value.trim();
  const account = document.getElementById('inp-account').value.trim();
  const isMock = document.getElementById('inp-mock').value === 'true';

  if (!appKey || !appSecret || !account) { toast('모든 항목을 입력해주세요', 'warn'); return; }

  try {
    const res = await fetch(`${API}/auth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app_key: appKey, app_secret: appSecret, account_no: account, is_mock: isMock })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '인증 실패');
    const mode = isMock ? 'mock' : 'real';
    setAuthenticated(mode, account);
    toast('인증 성공! 데이터를 불러옵니다.', 'success');
  } catch (err) {
    toast('인증 실패: ' + err.message, 'error');
  }
}

