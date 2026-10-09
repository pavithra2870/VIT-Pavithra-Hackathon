import { Component, useEffect, useRef, useState } from 'react'
import { api } from './lib/api'
import { useAsync, useHashRoute } from './lib/hooks'
import { longDate, shortDate } from './lib/format'
import Overview from './pages/Overview.jsx'
import Signals from './pages/Signals.jsx'
import Rebalancer from './pages/Rebalancer.jsx'
import StressLab from './pages/StressLab.jsx'
import Copilot from './pages/Copilot.jsx'

const NAV = [
  ['overview', 'Overview'],
  ['signals', 'Risk Signals'],
  ['rebalancer', 'Tactical Rebalancer'],
  ['stress', 'Stress Lab'],
  ['copilot', 'Copilot'],
]

// A render error in one page must never blank the whole app: show it, keep the masthead usable.
class ErrorBoundary extends Component {
  state = { error: null }
  static getDerivedStateFromError(error) { return { error } }
  componentDidUpdate(prev) { if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null }) }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="err">
        <h2 style={{ color: 'var(--ink)' }}>This view could not be displayed.</h2>
        <p>{String(this.state.error.message || this.state.error)}</p>
        <p className="muted">The backend may be running an older version than the frontend. Restart uvicorn and hard-refresh (Ctrl+F5).</p>
      </div>
    )
  }
}

export default function App() {
  const route = useHashRoute()
  const health = useAsync(() => api.health(), [])
  const portfolios = useAsync(() => api.portfolios(), [])
  const [data, setData] = useState(null)
  const prev = useRef(null)
  useEffect(() => {
    let alive = true
    let timer
    const poll = async () => {
      try {
        const s = await api.dataStatus()
        if (!alive) return
        const changed = prev.current && (prev.current.latest !== (s.live_latest_utc || s.latest_gdelt) || (prev.current.status !== 'idle' && s.status === 'idle' && s.downloaded?.length))
        if (changed && s.status === 'idle') window.location.reload() // new GDELT days scored: show them
        prev.current = { status: s.status, latest: s.live_latest_utc || s.latest_gdelt }
        setData(s)
        timer = setTimeout(poll, s.status === 'idle' ? 60000 : 5000)
      } catch { timer = setTimeout(poll, 30000) }
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [])
  const [portfolioId, setPortfolioId] = useState(() => localStorage.getItem('portfolio') || 'GGF')

  useEffect(() => { try { localStorage.setItem('portfolio', portfolioId) } catch { /* private mode */ } }, [portfolioId])
  const ctx = { portfolioId, setPortfolioId, portfolios: portfolios.data || [] }
  const llm = health.data?.llm

  let page
  switch (route.page) {
    case 'signals': page = <Signals ctx={ctx} eventId={route.param} />; break
    case 'rebalancer': page = <Rebalancer ctx={ctx} />; break
    case 'stress': page = <StressLab ctx={ctx} eventId={route.param} />; break
    case 'copilot': page = <Copilot ctx={ctx} llm={llm} />; break
    default: page = <Overview ctx={ctx} />
  }

  return (
    <>
      <header className="masthead">
        <div className="mast-top">
          <a className="brand" href="#/overview">
            <span className="name">Meridian</span>
            <span className="tag">Risk Intelligence Desk</span>
          </a>
          <div className="mast-meta">
            <span className="llm" title={data?.error || data?.message || ''}>
              <i className={`dot ${data && data.status !== 'idle' ? 'on' : ''}`} />
              {data?.status && data.status !== 'idle' ? data.message : data?.live_latest_utc ? `GDELT live · ${shortDate(data.live_latest_utc.slice(0, 10))} ${data.live_latest_utc.slice(11, 16)} UTC${data.market_live ? ' · prices live' : ''}` : data?.latest_gdelt ? `GDELT through ${shortDate(data.latest_gdelt)}` : 'Data status…'}
            </span>
            <span className="llm" title={llm?.available ? `Local model ${llm.model}` : llm?.reason || ''}>
              <i className={`dot ${llm?.available ? 'on' : ''}`} />
              {health.loading ? 'Checking copilot…' : llm?.available ? `Copilot online · ${llm.model}` : 'Copilot offline · deterministic explanations'}
            </span>
            <span className="selector">
              <label htmlFor="pf">Portfolio</label>
              <select id="pf" value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}>
                {(portfolios.data || [{ portfolio_id: portfolioId, name: portfolioId }]).map((p) => <option key={p.portfolio_id} value={p.portfolio_id}>{p.name}</option>)}
              </select>
            </span>
          </div>
        </div>
        <nav className="nav" aria-label="Primary">
          {NAV.map(([k, label]) => <a key={k} href={`#/${k}`} className={route.page === k || (k === 'overview' && !NAV.some(([p]) => p === route.page)) ? 'on' : ''}>{label}</a>)}
        </nav>
      </header>
      {data && data.status !== 'idle' && (
        <div className="refresh-banner" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <b>{data.status === 'fetching' ? 'Checking for new GDELT data' : 'Recalculating risk signals'}</b>
          <span>{data.message} Figures below are from the previous run until this finishes; the page will refresh automatically.</span>
        </div>
      )}
      <main className="page">
        <ErrorBoundary resetKey={`${route.page}/${route.param}`}>{page}</ErrorBoundary>
        <footer className="footer">
          <span>Prototype built for the S&amp;P Global &amp; Crisil Campus Hackathon 2026. Portfolios are synthetic; the stress model is a simplified prototype methodology, not a regulatory model.</span>
          <span>{health.data ? `${health.data.signals.toLocaleString()} risk signals · sentiment: ${health.data.sentiment_backend}` : ''}</span>
        </footer>
      </main>
    </>
  )
}

export { longDate }
