// ── 종목 마스터 상태 확인 ──
async function checkMasterStatus() {
  const input = document.getElementById('quote-search');
  if (!input) return;
  try {
    const res = await fetch(`${API}/master/status`);
    const data = await res.json();
    if (data.loaded) {
      input.placeholder = `종목명 또는 코드 검색 (전체 ${data.total.toLocaleString()}종목)`;
    } else {
      input.placeholder = '종목 마스터 로딩 중... (잠시 후 검색 가능)';
      // 3초 후 재확인
      setTimeout(checkMasterStatus, 3000);
    }
  } catch(e) {
    input.placeholder = '종목명 또는 코드 검색 (예: 삼성전자, 005930)';
  }
}

// 30초마다 잔고 새로고침
setInterval(() => { if (isAuthenticated) loadBalance(); }, 30000);
setInterval(() => { if (isAuthenticated) loadStrategyStatus(); }, 30000);

// 모든 함수 정의 완료 후 실행
window.addEventListener('DOMContentLoaded', () => {
  checkEnvAuth();
  checkMasterStatus();
  loadStrategyAIModels();
  loadStrategyStatus();
  loadIntradayTraderStatus();
});

