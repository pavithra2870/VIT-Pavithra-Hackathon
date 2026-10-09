import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/hooks'
import { longDate, pct, shortDate, signed, title } from '../lib/format'
import { COLORS, LineChart } from '../components/charts.jsx'
import { SectionHead, Status } from '../components/ui.jsx'

const HOLD = 0.0005 // moves below 0.05 percentage points are shown as "hold"

export default function Rebalancer() {
  const [p, setP] = useState({ as_of: '', max_turnover: 0.1, max_weight: 0.15, min_weight: 0.01, tilt_strength: 0.6 })
  const res = useAsync(() => api.rebalance({ ...p, as_of: p.as_of || undefined }), [p.as_of, p.max_turnover, p.max_weight, p.min_weight, p.tilt_strength])
  const upd = (k, v) => setP((s) => ({ ...s, [k]: v }))

  return (
    <>
      <div className="head-row">
        <div>
          <div className="kicker">Module A · Tactical rebalancer</div>
          <h1 className="page-title">What to buy and sell.</h1>
          <p className="lede">A 15-stock index is re-weighted from today's news signals. Green means add weight, red means trim. Nothing here is hard-coded: every figure comes from the live risk signals.</p>
        </div>
        <label className="ctrl"><span>As of</span>
          <select value={p.as_of} onChange={(e) => upd('as_of', e.target.value)}>
            <option value="">Latest</option>
            {(res.data?.available_dates || []).slice().reverse().map((d) => <option key={d} value={d}>{longDate(d)}</option>)}
          </select>
        </label>
      </div>
      <Status state={res}>{res.data && <Body r={res.data} p={p} upd={upd} />}</Status>
    </>
  )
}

function Body({ r, p, upd }) {
  const m = r.metrics
  const stocks = [...r.stocks].sort((a, b) => b.change - a.change)
  const up = stocks.filter((s) => s.change > HOLD)
  const down = stocks.filter((s) => s.change < -HOLD).sort((a, b) => a.change - b.change)
  const hold = stocks.filter((s) => Math.abs(s.change) <= HOLD)
  const maxMove = Math.max(...stocks.map((s) => Math.abs(s.change)), 0.001)
  const names = (l) => l.slice(0, 3).map((s) => s.ticker).join(', ')
  const tone = m.signal_score_before <= -0.3 ? 'negative' : m.signal_score_before >= 0.3 ? 'positive' : 'mixed'
  const volDelta = m.vol_after - m.vol_before

  return (
    <>
      <section className="rb-headline">
        <p className="serif">
          {down.length > 0 && <>Trim <b>{names(down)}</b>{up.length > 0 ? ', ' : ''}</>}
          {up.length > 0 && <>add to <b>{names(up)}</b></>}
          {' '}— about <b className="num">{pct(m.turnover, 1)}</b> of the index changes hands.
        </p>
        <p className="muted">
          Starting weights: {r.index.weighting}. {r.market_data?.live ? `Prices and volatility are live (Yahoo Finance, ${r.market_data.as_of?.slice(0, 16)} UTC, up to ~15 min delayed).` : 'Prices are from the committed 2025 snapshot.'} The news backdrop for this index is <b>{tone}</b> overall. Weights must add up to 100%, so a stock gains weight when its news is holding up better than its peers, not only when it is positive.
        </p>
      </section>

      <section className="rb-tiles">
        <div>
          <div className="label">Turnover</div>
          <div className="big num">{pct(m.turnover, 1)}</div>
          <div className="sub">of the index changes hands. Limit {pct(r.constraints.max_turnover, 0)}{m.turnover_scaled_back ? ` — the full signal wanted ${pct(m.unscaled_turnover, 1)}, so it was scaled back` : ''}.</div>
          <div className="hint"><b>Turnover</b> = how much has to be bought and sold to reach the new weights. Lower means cheaper trading.</div>
        </div>
        <div>
          <div className="label">Expected volatility</div>
          <div className="big num">{pct(m.vol_before, 1)} → {pct(m.vol_after, 1)}</div>
          <div className="sub">typical yearly swing in value, from the last year of prices — {volDelta <= 0 ? 'slightly calmer' : 'slightly more volatile'} after the change.</div>
          <div className="hint"><b>Volatility</b> = how much the index value typically moves up and down over a year. Lower means a calmer ride.</div>
        </div>
        <div>
          <div className="label">Stocks changing</div>
          <div className="big num">{up.length + down.length} <span className="muted" style={{ fontSize: 20 }}>of {stocks.length}</span></div>
          <div className="sub">{up.length} to add · {down.length} to trim · {hold.length} unchanged.</div>
          <div className="hint"><b>Add / trim</b> = give a stock more or less of the index. Moves under 0.05 points count as unchanged.</div>
        </div>
      </section>

      <section className="section">
        <SectionHead title="Recommended changes" aside={`As of ${longDate(r.as_of)} · click a row for the evidence`} />
        <div className="rb-legend"><span><i style={{ background: COLORS.forest }} />Add weight</span><span><i style={{ background: COLORS.brick }} />Trim weight</span><span>Number = change in the stock's share of the index, in percentage points (+3.0% turns a 12% weight into 15%)</span></div>
        {up.length > 0 && <Group label="Add" items={up} max={maxMove} />}
        {down.length > 0 && <Group label="Trim" items={down} max={maxMove} />}
        {hold.length > 0 && <p className="muted" style={{ marginTop: 14 }}>Unchanged: {hold.map((s) => s.ticker).join(', ')} (no meaningful signal or already at a limit).</p>}
      </section>

      {r.history.length > 1 && (
        <section className="section">
          <SectionHead title="How the weights moved, day by day" aside="Simulation: the index is re-weighted every day from that day's news" />
          <p className="muted" style={{ maxWidth: '78ch', margin: '0 0 14px' }}>Each line is one stock's suggested share of the index over time. A line rising means the news has been holding up better than its peers; falling means worse. Each day starts from the previous day's weights, so moves build up (limited by the turnover cap). Pick the stocks you want to compare; hover the chart to read exact values.</p>
          <Trend r={r} />
        </section>
      )}

      <section className="section">
        <details className="fold">
          <summary>Change the rules <span className="muted">— turnover limit, weight limits, signal strength</span></summary>
          <p className="muted" style={{ maxWidth: '78ch', margin: '12px 0 0' }}>The suggestion is a balance between following the news and staying close to today's index. These guard-rails set that balance; the page recalculates as you move them. Defaults: 10% turnover, 1–15% per stock, strength 0.6.</p>
          <div className="controls rules" style={{ borderTop: 0 }}>
            <label className="ctrl"><span>Max turnover · {pct(p.max_turnover, 0)}</span><input type="range" min="0.02" max="0.3" step="0.01" value={p.max_turnover} onChange={(e) => upd('max_turnover', +e.target.value)} /><small>The most of the index that may be traded at once. Lower = smaller, cheaper changes.</small></label>
            <label className="ctrl"><span>Max weight per stock · {pct(p.max_weight, 0)}</span><input type="range" min="0.08" max="0.25" step="0.01" value={p.max_weight} onChange={(e) => upd('max_weight', +e.target.value)} /><small>No single stock may grow beyond this share. Limits concentration.</small></label>
            <label className="ctrl"><span>Min weight per stock · {pct(p.min_weight, 1)}</span><input type="range" min="0" max="0.05" step="0.005" value={p.min_weight} onChange={(e) => upd('min_weight', +e.target.value)} /><small>The smallest share a stock may be cut to. Keeps every stock in the index.</small></label>
            <label className="ctrl"><span>How hard to follow the news · {p.tilt_strength.toFixed(1)}</span><input type="range" min="0.1" max="1.5" step="0.1" value={p.tilt_strength} onChange={(e) => upd('tilt_strength', +e.target.value)} /><small>0.1 barely reacts; 1.5 reacts strongly. (Limits above still apply.)</small></label>
            <button className="linkbtn" onClick={() => { upd('max_turnover', 0.1); upd('max_weight', 0.15); upd('min_weight', 0.01); upd('tilt_strength', 0.6) }}>Reset to defaults</button>
          </div>
        </details>
        <details className="fold">
          <summary>How it works and technical detail</summary>
          <div style={{ marginTop: 14 }}>
            <p className="prose">Each stock gets a score from three things: news about the company, news about its sector, and how exposed its sector is to the current geopolitical/macro shocks. Recent news counts more than old news (half-life {r.constraints.signal_half_life_days} days, look-back {r.constraints.lookback_days} days) and bad news counts {r.constraints.loss_aversion}× as much as good news. Volatile stocks get a smaller boost and a larger cut. The score is compared with the index average, tilted, then trimmed to fit the rules.</p>
            <table className="t" style={{ marginTop: 16 }}>
              <thead><tr><th>Measure</th><th className="r">Value</th><th style={{ paddingLeft: 24 }}>What it means</th></tr></thead>
              <tbody>
                <tr><td>Weight limits</td><td className="r num">{pct(r.constraints.min_weight, 1)} – {pct(r.constraints.max_weight, 0)}</td><td style={{ paddingLeft: 24 }}>Every stock must stay between these shares of the index.</td></tr>
                <tr><td>Sector cap</td><td className="r num">{pct(r.constraints.max_sector_weight, 0)}</td><td style={{ paddingLeft: 24 }}>No sector (e.g. technology) may make up more than this share, so the index cannot become a one-sector bet.</td></tr>
                <tr><td>Weights add up to</td><td className="r num">{pct(m.sum_weights, 1)}</td><td style={{ paddingLeft: 24 }}>The index is always fully invested: weight given to one stock must come from others.</td></tr>
                <tr><td>Concentration (HHI)</td><td className="r num">{m.hhi_before.toFixed(3)} → {m.hhi_after.toFixed(3)}</td><td style={{ paddingLeft: 24 }}>0 = spread evenly, 1 = everything in one stock (15 equal stocks = 0.067). Lower means more diversified.</td></tr>
                <tr><td>Average news score</td><td className="r num">{signed(m.signal_score_before)} → {signed(m.signal_score_after)}</td><td style={{ paddingLeft: 24 }}>Weight-averaged news score of the index. Negative = bad news overall. Shifting weight to better-scoring stocks should raise it.</td></tr>
              </tbody>
            </table>
            <h3 style={{ fontSize: 22, margin: '26px 0 6px' }}>Sector weights</h3>
            <p className="muted" style={{ maxWidth: '78ch', marginBottom: 10 }}>How much of the index sits in each sector, now versus after the suggestion. "Change" is in percentage points. A sector may not exceed the {pct(r.constraints.max_sector_weight, 0)} cap.</p>
            <table className="t">
              <thead><tr><th>Sector</th><th className="r">Now</th><th className="r">After</th><th className="r">Change</th></tr></thead>
              <tbody>{[...m.sector_weights].sort((x, y) => y.after - x.after).map((x) => <tr key={x.sector}><td>{x.sector}</td><td className="r num">{pct(x.before, 1)}</td><td className="r num">{pct(x.after, 1)}</td><td className={`r num ${x.after - x.before > 0.0005 ? 'pos' : x.after - x.before < -0.0005 ? 'neg' : 'muted'}`}>{signed((x.after - x.before) * 100, 1)}</td></tr>)}</tbody>
            </table>
          </div>
        </details>
      </section>
    </>
  )
}

function Group({ label, items, max }) {
  const [open, setOpen] = useState(null)
  return (
    <div className="rb-group">
      <div className="rb-group-label">{label}</div>
      {items.map((s) => {
        const up = s.change > 0
        const w = (Math.abs(s.change) / max) * 100
        const verdict = s.vs_index.startsWith('stronger') ? 'Holding up better than peers' : s.vs_index.startsWith('weaker') ? 'Hit harder than peers' : 'In line with peers'
        return (
          <div key={s.ticker} className={`rb-row ${open === s.ticker ? 'open' : ''}`} onClick={() => setOpen(open === s.ticker ? null : s.ticker)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setOpen(open === s.ticker ? null : s.ticker)}>
            <div className="rb-stock"><b>{s.ticker}</b><span>{s.name}</span>{s.last_price != null && <span className="num">${s.last_price.toFixed(2)}{s.day_change != null && <b className={s.day_change >= 0 ? 'pos' : 'neg'}> {pct(s.day_change, 1, true)}</b>} today</span>}</div>
            <div className="rb-weights num"><span className="muted">{pct(s.current_weight, 1)}</span> → <b>{pct(s.proposed_weight, 1)}</b></div>
            <div className="rb-bar">
              <div className="rb-track"><div style={{ width: `${w}%`, background: up ? COLORS.forest : COLORS.brick }} /></div>
              <span className={`num ${up ? 'pos' : 'neg'}`}>{signed(s.change * 100, 1)}%</span>
            </div>
            <div className="rb-why"><b className={up ? 'pos' : 'neg'}>{verdict}</b><span>{s.reason}{nuance(s, up)}</span></div>
            {open === s.ticker && (
              <div className="rb-detail">
                <dl className="dl">
                  <dt>Company news</dt><dd className="num">{signed(s.direct_score)}</dd>
                  <dt>Sector news</dt><dd className="num">{signed(s.sector_score)}</dd>
                  <dt>Geopolitical / macro exposure</dt><dd className="num">{signed(s.macro_score)}</dd>
                  <dt>Volatility (yearly)</dt><dd className="num">{pct(s.vol_60d, 0)}</dd>
                  <dt>Signals behind this</dt><dd className="num">{s.n_signals}</dd>
                  {s.flags && <><dt>Limit reached</dt><dd>{s.flags}</dd></>}
                </dl>
                {s.top_event && <p className="muted" style={{ marginTop: 10 }}>Strongest company signal: {title(s.top_event_type)} — “{s.top_event.slice(0, 120)}”</p>}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// Weights are relative: a stock can gain weight while its own news is negative, as long as peers are hit harder.
function nuance(s, up) {
  const negative = /negative|Exposed/.test(s.reason)
  const positive = /positive|Well placed/.test(s.reason)
  if (up && negative) return ', but less so than peers'
  if (!up && positive) return ', but weaker than peers'
  return ''
}

const SERIES_COLORS = ['#A63D32', '#476653', '#B38B45', '#17191C', '#5B6C7A', '#7A4E6D', '#3F7A7A', '#C0752F', '#6B7A3A', '#8D6E63', '#2F5D8A', '#9A9A96', '#B35C7E', '#4F4F9A', '#7E8F2E']

function Trend({ r }) {
  const hist = r.history
  const order = r.stocks.map((s) => s.ticker)
  const color = (t) => SERIES_COLORS[order.indexOf(t) % SERIES_COLORS.length]
  const movers = [...r.stocks].sort((a, b) => Math.abs(b.change) - Math.abs(a.change)).slice(0, 5).map((s) => s.ticker)
  const [sel, setSel] = useState(movers)
  const toggle = (t) => setSel((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))
  const shown = order.filter((t) => sel.includes(t))
  return (
    <>
      <div className="pickers">
        <div className="chips">
          {order.map((t) => (
            <button key={t} className={`chip chipbtn ${sel.includes(t) ? 'on' : ''}`} style={sel.includes(t) ? { background: color(t), borderColor: color(t), color: '#fff' } : undefined} onClick={() => toggle(t)} aria-pressed={sel.includes(t)}>{t}</button>
          ))}
        </div>
        <div className="picker-actions">
          <button className="linkbtn" onClick={() => setSel(movers)}>Biggest movers</button>
          <button className="linkbtn" onClick={() => setSel(order)}>All</button>
          <button className="linkbtn" onClick={() => setSel([])}>None</button>
        </div>
      </div>
      {shown.length === 0 ? <div className="empty">Select at least one stock above to see its path.</div> : (
        <LineChart
          xLabels={hist.map((h) => shortDate(h.date))}
          height={400}
          endLabels
          hover
          fmt={(v) => pct(v, 1)}
          series={shown.map((t) => ({ name: t, color: color(t), width: 2.2, points: hist.map((h) => h.weights[t]), dots: true }))}
        />
      )}
    </>
  )
}
