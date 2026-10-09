import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { go, useAsync } from '../lib/hooks'
import { cap, pct, shortDate, signed, title, usd } from '../lib/format'
import { BeforeAfter, CLASS_COLORS, COLORS, DivergingBars, Impact, LineChart } from '../components/charts.jsx'
import { SectionHead, Status } from '../components/ui.jsx'

const Hint = ({ children }) => <p className="sec-hint">{children}</p>
const ordinal = (n) => ['', 'most', '2nd most', '3rd most', '4th most'][n] || `${n}th most`

export default function StressLab({ ctx, eventId }) {
  const [mode, setMode] = useState('signal')
  const [custom, setCustom] = useState({ event_type: 'INTEREST_RATE', impact_score: 8, parallel_bp: '' })
  const [showAll, setShowAll] = useState(false)
  const top = useAsync(() => api.signals({ source_type: 'gdelt', min_impact: 5, sort: 'date', limit: 40 }), [])   // live GDELT events only, newest first
  const scenarios = useAsync(() => api.scenarios(), [])
  const selected = eventId || top.data?.items?.[0]?.event_id

  useEffect(() => { if (!eventId && top.data?.items?.length) go(`stress/${top.data.items[0].event_id}`) }, [eventId, top.data])

  const body = mode === 'signal'
    ? (selected ? { portfolio_id: ctx.portfolioId, event_id: selected } : null)
    : { portfolio_id: ctx.portfolioId, event_type: custom.event_type, impact_score: +custom.impact_score, overrides: custom.parallel_bp !== '' ? { parallel_bp: +custom.parallel_bp } : undefined }
  const key = JSON.stringify(body)
  const res = useAsync(() => (body ? api.stress(body) : Promise.resolve(null)), [key])

  const items = top.data?.items || []
  const cards = items.slice(0, 6)
  const more = items.slice(6)
  const inList = items.some((i) => i.event_id === selected)

  return (
    <>
      <div className="head-row">
        <div>
          <div className="kicker">Module B · Portfolio stress testing</div>
          <h1 className="page-title">What if this event hit?</h1>
          <p className="lede">A stress test asks: if something bad happened, how much would this portfolio lose? Pick a live news event. We apply the typical market reaction for that kind of event to every holding (stocks fall, bond prices change, some loans default…) and add up the damage. The portfolios are mock and the model is deliberately simple.</p>
        </div>
      </div>

      <div className="tabs">
        <button className={mode === 'signal' ? 'on' : ''} onClick={() => setMode('signal')}>Live news event</button>
        <button className={mode === 'custom' ? 'on' : ''} onClick={() => setMode('custom')}>Custom what-if</button>
      </div>

      {mode === 'signal' ? (
        <>
          <Hint>The newest events detected from live GDELT news (impact 5 or higher). Each card shows the type of event, how much market impact the engine expects (1–10) and whether the news tone is negative or positive. Click one to stress-test it.</Hint>
          <div className="ev-cards">
            {cards.map((s) => (
              <button key={s.event_id} className={`ev-card ${s.event_id === selected ? 'on' : ''}`} onClick={() => go(`stress/${s.event_id}`)}>
                <span className="ev-card-top"><span className="chip">{shortDate(s.signal_date)}</span><span className="chip ink">{s.event_label.split(' / ')[0]}</span></span>
                <span className="ev-card-title">{s.event_name}</span>
                <span className="ev-card-foot"><Impact score={s.impact_score} high={s.high_impact} /><span className={`num ${s.sentiment <= -0.15 ? 'neg' : s.sentiment >= 0.15 ? 'pos' : 'muted'}`}>{s.sentiment <= -0.15 ? 'negative' : s.sentiment >= 0.15 ? 'positive' : 'neutral'} tone</span></span>
              </button>
            ))}
          </div>
          {more.length > 0 && (
            <label className="ctrl" style={{ marginTop: 14, maxWidth: 720 }}><span>More recent events</span>
              <select value={more.some((m2) => m2.event_id === selected) ? selected : ''} onChange={(e) => e.target.value && go(`stress/${e.target.value}`)} style={{ width: '100%' }}>
                <option value="">Choose another…</option>
                {more.map((s) => <option key={s.event_id} value={s.event_id}>{shortDate(s.signal_date)} · impact {s.impact_score.toFixed(1)} · {s.event_label.split(' / ')[0]} · {s.event_name.slice(0, 80)}</option>)}
              </select>
            </label>
          )}
          {!inList && selected && <p className="muted" style={{ marginTop: 10 }}>Showing event {selected} (not among the latest list).</p>}
        </>
      ) : (
        <>
          <Hint>Test a what-if of your own: choose a type of event and how severe it is (1 = mild, 10 = extreme). Optionally force an interest-rate move: 200 bp means rates rise by 2 percentage points.</Hint>
          <div className="controls">
            <label className="ctrl"><span>Type of event</span>
              <select value={custom.event_type} onChange={(e) => setCustom({ ...custom, event_type: e.target.value })}>
                {(scenarios.data || []).map((s) => <option key={s.event_type} value={s.event_type}>{s.label}</option>)}
              </select>
            </label>
            <label className="ctrl"><span>Severity (impact score) · {Number(custom.impact_score).toFixed(1)}</span>
              <input type="range" min="1" max="10" step="0.5" value={custom.impact_score} onChange={(e) => setCustom({ ...custom, impact_score: e.target.value })} />
            </label>
            <label className="ctrl"><span>Force a rate move (bp, optional)</span>
              <input type="text" placeholder="e.g. 200" value={custom.parallel_bp} onChange={(e) => setCustom({ ...custom, parallel_bp: e.target.value.replace(/[^\d.-]/g, '') })} style={{ width: 120 }} />
            </label>
          </div>
        </>
      )}

      <Status state={res}>{res.data && <Result r={res.data} ctx={ctx} showAll={showAll} setShowAll={setShowAll} />}</Status>
    </>
  )
}

function Result({ r, ctx, showAll, setShowAll }) {
  const a = r.assumptions
  const loss = r.pnl < 0
  const assets = showAll ? r.assets : r.assets.slice(0, 12)
  const maxAbs = Math.max(...r.assets.map((x) => Math.abs(x.pnl)), 1)
  const shocks = Object.entries(a.sector_shocks_effective || {}).sort((x, y) => x[1] - y[1])
  const rank = r.portfolio_comparison.findIndex((c) => c.portfolio_id === r.portfolio_id) + 1
  const driver = r.by_driver[0]
  const strategy = ctx.portfolios.find((p) => p.portfolio_id === r.portfolio_id)?.strategy

  const shockRows = [
    ['Equity (default)', pct(a.equity_default_effective, 2, true), 'Price move applied to stocks that have no sector-specific rule.'],
    ['Parallel yield shift', `${signed(a.parallel_bp_effective, 0)} bp`, 'Interest rates move up or down by this much (100 bp = 1 percentage point). When rates rise, bonds you already own lose value; when they fall, they gain.'],
    ...(a.base_10y_yield !== undefined ? [['US 10Y yield (live)', `${pct(a.base_10y_yield, 2)} → ${pct(a.shocked_10y_yield, 2)}`, 'The benchmark US government borrowing rate today, and where it would be after the shock.']] : []),
    ...Object.entries(a.spread_bp_effective).map(([k, v]) => [`${k === 'IG' ? 'Safe-bond' : 'Risky-bond'} spread widening (${k})`, `+${v.toFixed(0)} bp`, k === 'IG' ? 'Extra interest investors demand to hold safe (investment-grade) company bonds. Wider means those bonds fall in price.' : 'The same for risky ("junk" / high-yield) bonds, which react more strongly.']),
    ['Loan default multiplier', `${a.pd_multiplier_effective.toFixed(2)}×`, `Each borrower's chance of defaulting is multiplied by this (${a.pd_multiplier_effective.toFixed(2)}× means ${Math.round((a.pd_multiplier_effective - 1) * 100)}% more likely).`],
    ['Fear (volatility) shock', `+${(a.vol_shock_effective * 100).toFixed(1)} pts`, 'How much expected market turbulence rises. This makes options (bought as protection) worth more.'],
  ]
  const driverHelp = [
    ['Equity price shock', 'stock prices falling or rising'],
    ['Interest-rate move', 'existing bonds and swaps repricing as rates change'],
    ['Credit-spread widening', 'bonds, loans and credit insurance repricing as default fears change'],
    ['Default / credit-loss', 'expected losses on loans where borrowers fail to repay'],
    ['Volatility (vega)', 'options gaining or losing value as market fear changes'],
    ['Convexity', 'a small correction for how bond prices curve when rates move a lot'],
  ]
  const curveIdx = r.impact_curve?.length ? r.impact_curve.reduce((best, c, i) => (Math.abs(c.impact - r.event.impact_score) < Math.abs(r.impact_curve[best].impact - r.event.impact_score) ? i : best), 0) : 0

  return (
    <>
      <section className="section" style={{ marginTop: 28 }}>
        <div className="grid-2 even">
          <div>
            <div className="chips"><span className="chip ink">{a.label}</span><span className="chip">{a.scope === 'broad' ? 'broad shock: hits the whole market' : 'targeted shock: hits named companies/sectors'}</span>{r.event.event_id && <button className="chip chipbtn" onClick={() => go(`signals/${r.event.event_id}`)}>View signal →</button>}</div>
            <h2 className="detail-title">{r.event.name || a.label}</h2>
            <div className="split" style={{ justifyContent: 'flex-start', gap: 28 }}>
              <span><span className="muted">Impact </span><Impact score={r.event.impact_score} high={r.event.impact_score >= 7} /></span>
              <span><span className="muted">News tone </span><b className="num">{signed(r.event.sentiment)}</b></span>
              <span className="muted">Strength applied <b className="num" style={{ color: 'var(--ink)' }}>{pct(a.severity_scalar, 0)}</b></span>
            </div>
            <p className="muted" style={{ fontSize: 12.5, marginTop: 8, maxWidth: '52ch' }}><b>Impact</b> = how much market effect the engine expects (1–10). <b>News tone</b> runs from −1 (very negative) to +1. <b>Strength applied</b> = impact ÷ 10: our shock rules are written for impact 10, so this event, at {r.event.impact_score.toFixed(1)}, applies {pct(a.severity_scalar, 0)} of them.</p>
          </div>
          <p className="prose">{a.description}</p>
        </div>
        {r.notes?.map((n) => <p key={n} className="note warn" style={{ marginTop: 14 }}>{n}</p>)}
      </section>

      <section className="rb-headline" style={{ marginTop: 26 }}>
        <p className="serif">
          If this event played out, <b>{r.portfolio_name}</b> would {loss ? 'lose' : 'gain'} about <b className={loss ? 'neg' : 'pos'}>{pct(Math.abs(r.pct_impact), 1)}</b> ({usd(Math.abs(r.pnl))})
          {driver ? <>, mostly through <b>{driver.label.toLowerCase()}</b></> : null}.
        </p>
        <p className="muted">{rank > 0 && <>That makes it the <b>{ordinal(rank)}</b> exposed of our {r.portfolio_comparison.length} portfolios to this event. </>}The loss comes from what the portfolio holds, not only from how big the event is.</p>
      </section>

      <section className="rb-tiles" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        <div>
          <div className="label">{r.portfolio_name}</div>
          <div className="big" style={{ fontSize: 28 }}>Portfolio value</div>
          <div className="sub">{strategy}</div>
          
        </div>
        <div>
          <div className="label">Before</div>
          <div className="big num">{usd(r.value_before)}</div>
          <div className="sub">value today</div>
          <div className="hint"><b>Mark-to-market</b> = valued at current market prices, as if you sold everything now.</div>
        </div>
        <div>
          <div className="label">After</div>
          <div className="big num">{usd(r.value_after)}</div>
          <div className="sub">value in the scenario</div>
          <div className="hint">What it would be worth if every holding reacted the way our shock rules say for this kind of event.</div>
        </div>
        <div>
          <div className="label">Impact</div>
          <div className={`big num ${loss ? 'neg' : 'pos'}`}>{pct(r.pct_impact, 2, true)}</div>
          <div className="sub">{usd(r.pnl, true)}</div>
          <div className="hint">The change in value. Negative = a loss; the percentage is relative to the fund's size.</div>
        </div>
      </section>

      {/* row 1: what happened (left) and what was assumed (right) - columns kept to similar height */}
      <div className="grid-2 section">
        <div>
          <SectionHead title="Before and after" />
          <Hint>The same two values as above (the bar axis is cut off so the difference is visible).</Hint>
          <BeforeAfter before={r.value_before} after={r.value_after} fmt={(v) => usd(v)} />
          <SectionHead title="By asset class" aside="Contribution to the move" />
          <Hint>Where the loss comes from by type of investment: stocks, bonds, loans, or derivatives (contracts such as options whose value depends on something else, often used as protection).</Hint>
          <DivergingBars labelWidth={120} fmt={(v) => usd(v, true)} rows={r.by_asset_class.map((c) => ({ key: c.key, label: cap(c.key), value: c.pnl, sub: `${pct(c.weight, 0)} of value` }))} />
          <SectionHead title="Main drivers" />
          <Hint>Which market effect caused the gain or loss.</Hint>
          <DivergingBars labelWidth={150} fmt={(v) => usd(v, true)} rows={r.by_driver.map((d) => ({ key: d.key, label: d.label, value: d.pnl }))} />
          <details className="fold" style={{ marginTop: 10 }}>
            <summary>What do these mean?</summary>
            <ul className="plain-list">{driverHelp.map(([k, v]) => <li key={k}><b>{k}</b> — {v}</li>)}</ul>
          </details>
        </div>
        <aside>
          <SectionHead title="Scenario shocks" aside="What was applied" />
          <Hint>The market moves assumed for this kind of event, already scaled to this event's strength.</Hint>
          <table className="t">
            <tbody>{shockRows.map(([k, v, h]) => <tr key={k}><td style={{ width: '38%' }}>{k}</td><td className="r num" style={{ whiteSpace: 'nowrap' }}>{v}</td><td className="ev-sub" style={{ paddingLeft: 16 }}>{h}</td></tr>)}</tbody>
          </table>
        </aside>
      </div>

      {/* row 2: the two sector views side by side - what the portfolio lost vs what we assumed */}
      <div className="grid-2 even section">
        <div>
          <SectionHead title="Result by sector" aside="Gain / loss in this portfolio" />
          <Hint>The loss grouped by industry (technology, energy, banks…). The small percentage is that sector's change in value.</Hint>
          <DivergingBars labelWidth={170} fmt={(v) => usd(v, true)} rows={r.by_sector.filter((s) => s.key).slice(0, 11).map((c) => ({ key: c.key, label: c.key, value: c.pnl, sub: pct(c.pct_change, 1, true) }))} />
        </div>
        {shocks.length > 0 && (
          <div>
            <SectionHead title="Assumed stock move by sector" aside="The rule applied" />
            <Hint>The price move our rules assume for stocks in each sector in this event. A minus is a drop; a plus is a gain (for example energy companies when oil spikes). Compare with the result on the left: it differs because the portfolio holds different amounts in each sector.</Hint>
            <DivergingBars labelWidth={170} fmt={(v) => pct(v, 1, true)} rows={shocks.map(([k, v]) => ({ key: k, label: k, value: v }))} />
          </div>
        )}
      </div>

      {r.impact_curve?.length > 0 && (
        <section className="section">
          <SectionHead title="How bad would it need to be?" aside="Same event type, different severity" />
          <Hint>Our model scales the shocks with the impact score, so we can ask what the loss would be at every severity from 1 to 10, and how severe an event of this type must be to hit loss levels such as 5%.</Hint>
          <div className="grid-2">
            <div>
              <LineChart
                xLabels={r.impact_curve.map((c) => String(c.impact))}
                height={240}
                zero
                xEvery={2}
                fmt={(v) => pct(v, 0)}
                series={[
                  { name: 'Loss at each severity', color: COLORS.brick, width: 2, points: r.impact_curve.map((c) => c.pct_impact), area: true },
                  { name: 'This event', color: COLORS.ink, width: 1, points: r.impact_curve.map((_, i) => (i === curveIdx ? r.pct_impact : null)), dots: true },
                ]}
              />
              <div className="legend"><span><i style={{ background: COLORS.brick }} />Change in value at each impact score (1–10)</span><span><i style={{ background: COLORS.ink }} />This event (impact {r.event.impact_score.toFixed(1)})</span></div>
            </div>
            <div>
              <table className="t">
                <thead><tr><th>If the loss reached…</th><th className="r">Impact needed</th></tr></thead>
                <tbody>
                  {r.break_even.map((b) => (
                    <tr key={b.loss_pct}>
                      <td>{pct(b.loss_pct, 0)} of the fund</td>
                      <td className="r num">{b.impact === null ? <span className="muted">not even at impact 10</span> : `≈ ${b.impact.toFixed(1)}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="sec-hint" style={{ marginTop: 10 }}>Read it as: "an event of this type needs an impact score of about X to cost this portfolio that much". Compare with the event's actual score ({r.event.impact_score.toFixed(1)}).</p>
            </div>
          </div>
        </section>
      )}

      <Ranking ctx={ctx} selected={r.event.event_id} />

      <section className="section">
        <SectionHead title="Explanation" />
        <p className="prose serif">{r.explanation}</p>
        <p className="note" style={{ marginTop: 14 }}>{r.disclaimer}</p>
      </section>

      <section className="section">
        <SectionHead title="One event, four portfolios" aside="The risk of an event depends on exposure" />
        <Hint>The same event applied to each of our four mock portfolios. A portfolio full of tech stocks reacts very differently from one mostly in government bonds.</Hint>
        <DivergingBars labelWidth={200} fmt={(v) => pct(v, 2, true)} rows={r.portfolio_comparison.map((c) => ({ key: c.portfolio_id, label: c.portfolio_name, value: c.pct_impact, sub: c.portfolio_id === r.portfolio_id ? '· selected' : '' }))} />
        <table className="t" style={{ marginTop: 10 }}>
          <thead><tr><th>Portfolio</th><th className="r">Before</th><th className="r">After</th><th className="r">Gain / loss</th><th style={{ paddingLeft: 24 }}>Biggest cause</th></tr></thead>
          <tbody>{r.portfolio_comparison.map((c) => <tr key={c.portfolio_id}><td>{c.portfolio_name}</td><td className="r num">{usd(c.value_before)}</td><td className="r num">{usd(c.value_after)}</td><td className={`r num ${c.pnl < 0 ? 'neg' : 'pos'}`}>{usd(c.pnl, true)}</td><td style={{ paddingLeft: 24 }}>{c.main_driver}</td></tr>)}</tbody>
        </table>
      </section>

      <section className="section">
        <SectionHead title="Asset-level impact" aside={`${r.assets.length} holdings, worst first`}>
          <button className="linkbtn" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer' : 'Show all holdings'}</button>
        </SectionHead>
        <Hint>Every position in the portfolio: its value now, how much it gains or loses in this scenario, and which effect caused it. "Named" means the event mentions that company directly.</Hint>
        <div className="scroll-x">
          <table className="t">
            <thead><tr><th>Holding</th><th>Type</th><th>Sector</th><th className="r">Value now</th><th className="r">Gain / loss</th><th className="r">Change</th><th style={{ width: 130 }} /><th>Caused by</th></tr></thead>
            <tbody>
              {assets.map((x) => (
                <tr key={x.holding_id}>
                  <td>{x.name}{x.named_in_event && <span className="chip gold" style={{ marginLeft: 8 }}>named</span>}<div className="ev-sub">{x.ticker || x.rating}</div></td>
                  <td><span className="chip" style={{ borderColor: CLASS_COLORS[x.asset_class] }}>{x.asset_class}</span></td>
                  <td>{x.sector}</td>
                  <td className="r num">{usd(x.value_before)}</td>
                  <td className={`r num ${x.pnl < 0 ? 'neg' : x.pnl > 0 ? 'pos' : ''}`}>{usd(x.pnl, true)}</td>
                  <td className="r num">{pct(x.pct_change, 2, true)}</td>
                  <td><div style={{ height: 6, width: `${(Math.abs(x.pnl) / maxAbs) * 100}%`, minWidth: x.pnl ? 2 : 0, background: x.pnl < 0 ? 'var(--brick)' : 'var(--forest)' }} /></td>
                  <td className="ev-sub" style={{ whiteSpace: 'normal' }}>{Object.entries(x.drivers).map(([k, v]) => `${title(k)} ${usd(v, true)}`).join(' · ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}

function Ranking({ ctx, selected }) {
  const rk = useAsync(() => api.stressRanking(ctx.portfolioId, 20), [ctx.portfolioId])
  const [all, setAll] = useState(false)
  return (
    <section className="section">
      <SectionHead title="Latest live events, ranked by hit to this portfolio" aside="The 20 newest GDELT events (impact 5+)" />
      <Hint>The same portfolio stress-tested against each recent event, worst first. A bigger event is not always a bigger loss: it depends on what this portfolio holds. Click a row to open it above.</Hint>
      <Status state={rk}>
        {rk.data && (
          <div className="scroll-x">
            <table className="t">
              <thead><tr><th>Date</th><th>Event</th><th>Type</th><th className="r">Impact</th><th className="r">Gain / loss</th><th className="r">%</th></tr></thead>
              <tbody>
                {(all ? rk.data : rk.data.slice(0, 8)).map((x) => (
                  <tr key={x.event_id} className={`click ${x.event_id === selected ? 'sel' : ''}`} onClick={() => go(`stress/${x.event_id}`)}>
                    <td className="num">{shortDate(x.signal_date)}</td>
                    <td className="ev"><div className="ev-name">{x.event_name}</div></td>
                    <td><span className="chip">{x.event_label.split(' / ')[0]}</span></td>
                    <td className="r num">{x.impact_score.toFixed(1)}</td>
                    <td className={`r num ${x.pnl < 0 ? 'neg' : 'pos'}`}>{usd(x.pnl, true)}</td>
                    <td className={`r num ${x.pnl < 0 ? 'neg' : 'pos'}`}>{pct(x.pct_impact, 2, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rk.data.length > 8 && <button className="linkbtn" style={{ marginTop: 10 }} onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${rk.data.length}`}</button>}
          </div>
        )}
      </Status>
    </section>
  )
}
