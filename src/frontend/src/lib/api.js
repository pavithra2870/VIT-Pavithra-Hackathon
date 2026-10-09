// Thin typed-ish API client. All numbers shown in the UI come from these endpoints (FastAPI);
// the frontend performs no financial calculation.
async function request(path, options) {
  const res = await fetch(path, options)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    throw new Error(`${res.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
  return res.json()
}

const post = (path, body) => request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  health: () => request('/api/health'),
  dataStatus: () => request('/api/data-status'),
  refreshData: () => request('/api/data-refresh', { method: 'POST' }),
  market: (date) => request(`/api/market/summary${date ? `?date=${date}` : ''}`),
  signals: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '' && v !== null))
    return request(`/api/risk-signals?${q}`)
  },
  signal: (id) => request(`/api/risk-signals/${id}`),
  portfolios: () => request('/api/portfolios'),
  portfolio: (id) => request(`/api/portfolios/${id}`),
  scenarios: () => request('/api/stress-scenarios'),
  rebalance: (body) => post('/api/rebalance', body),
  stress: (body) => post('/api/stress-test', body),
  stressRanking: (portfolioId, limit = 20) => request(`/api/stress-ranking?portfolio_id=${portfolioId}&limit=${limit}`),
  copilot: (body) => post('/api/copilot', body),
}
