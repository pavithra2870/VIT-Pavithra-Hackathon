import { useState } from 'react'
import { api } from '../lib/api'
import { go, useAsync } from '../lib/hooks'
import { cap, longDate, pct, shortDate, signed, usd } from '../lib/format'
import { CLASS_COLORS, COLORS, DivergingBars, LineChart, StackedBar } from '../components/charts.jsx'
import { SectionHead, SignalTable, Status } from '../components/ui.jsx'

export default function Overview({ ctx }) {
  const [date, setDate] = useState('')          // '' = newest day; otherwise a past day to view / compare
  const market = useAsync(() => api.market(date), [date])
  const portfolio = useAsync(() => api.portfolio(ctx.portfolioId), [ctx.portfolioId])
  const impact = useAsync(async () => {
    const m = await api.market(date)
    const top = m.top_signals.slice(0, 4)
    const res = await Promise.all(top.map((s) => api.stress({ portfolio_id: ctx.portfolioId, event_id: s.event_id })))
    return top.map((s, i) => ({ signal: s, result: res[i] }))
  }, [ctx.portfolioId, date])

  return (
    <Status state={market}>
      {market.data && <Body m={market.data} portfolio={portfolio} impact={impact} ctx={ctx} setDate={setDate} />}
    </Status>
  )
}

function Body({ m, portfolio, impact, ctx, setDate }) {
  const risk = m.market_risk
  const sent = m.news_sentiment
  const land = m.risk_landscape
  const top = m.top_signals[0]
  const comparison = impact.data?.[0]?.result.portfolio_comparison
  return (
    <>
      <div className="head-row">
        <div>
          <div className="kicker">Risk Intelligence · as of {longDate(m.as_of)}{m.partial_day && m.as_of_time ? ` · live to ${m.as_of_time.slice(11)} UTC (day in progress)` : ''}</div>
          <h1>Market overview</h1>
        </div>
      </div>

      {m.markets?.length > 0 && (
        <div className="markets-strip" aria-label="Live market levels">
          {m.markets.map((x) => (
            <span key={x.symbol}><em>{x.label}</em> <b className="num">{x.unit === '%' ? `${x.price.toFixed(2)}%` : x.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b>
              {x.change_pct !== null && x.change_pct !== undefined && <span className={`num ${x.symbol === '^VIX' ? (x.change_pct > 0 ? 'neg' : 'pos') : (x.change_pct >= 0 ? 'pos' : 'neg')}`}> {pct(x.change_pct, 2, true)}</span>}
            </span>
          ))}
          <span className="muted">Yahoo Finance · {m.markets[0].as_of.slice(0, 16)} UTC (may be ~15 min delayed)</span>
        </div>
      )}

      <section className="figures" aria-label="Headline indicators">
        <div className="figure">
          <div className="label">Market risk index</div>
          <div className="value num">{risk.index.toFixed(1)}<span className="muted" style={{ fontSize: 22 }}> / 10</span></div>
          <div className="sub"><b style={{ color: risk.label === 'High' || risk.label === 'Elevated' ? COLORS.brick : COLORS.ink }}>{risk.label}</b> · {signed(risk.change_1d)} vs prior day</div>
        </div>
        <div className="figure">
          <div className="label">News sentiment</div>
          <div className={`value num ${sent.label === 'negative' ? 'neg' : sent.label === 'positive' ? 'pos' : ''}`}>{signed(sent.value)}</div>
          <div className="sub">{cap(sent.label)} · {pct(sent.negative_share, 0)} of signals negative</div>
        </div>
        <div className="figure">
          <div className="label">Top affected sector</div>
          <div className="value sm">{m.top_sector?.sector || '—'}</div>
          <div className="sub">{m.top_sector ? `${pct(m.top_sector.share, 0)} of 3-day risk pressure, from sectors named in the news` : ''}</div>
        </div>
        <div className="figure">
          <div className="label">High-impact signals</div>
          <div className="value num">{m.high_impact_count}</div>
          <div className="sub">impact ≥ {m.high_impact_threshold.toFixed(0)} on {shortDate(m.as_of)}</div>
        </div>
      </section>

      {!m.is_newest && (
        <div className="refresh-banner" role="status">
          <b>Viewing {longDate(m.as_of)}</b><span>an earlier day, not the latest.</span>
          <button className="linkbtn" onClick={() => setDate('')}>Back to latest ({shortDate(m.newest_day)}) →</button>
        </div>
      )}

      <section className="section" style={{ marginTop: 28 }}>
        <SectionHead title="Last 10 days" aside="Select a day to view the whole overview as of then" />
        <div className="legend" style={{ marginBottom: 10 }}>
          <span>Δ = that day minus the selected day.</span>
          <span><b className="neg">Red</b> = worse than the selected day (higher risk index, lower sentiment)</span>
          <span><b className="pos">Green</b> = better (lower risk, higher sentiment)</span>
        </div>
        <div className="scroll-x">
          <table className="t">
            <thead><tr><th>Day</th><th className="r">Risk index</th><th className="r">Δ</th><th className="r">Sentiment</th><th className="r">Δ</th><th className="r">High-impact</th><th className="r">Signals</th><th style={{ paddingLeft: 28 }}>Top sector (3-day)</th></tr></thead>
            <tbody>
              {[...m.day_table].reverse().map((d) => {
                const sel = d.date === m.as_of
                const base = m.day_table.find((x) => x.date === m.as_of)
                return (
                  <tr key={d.date} className={`click ${sel ? 'sel' : ''}`} onClick={() => setDate(d.date === m.newest_day ? '' : d.date)}>
                    <td><b>{shortDate(d.date)}</b>{d.date === m.newest_day && <span className="chip" style={{ marginLeft: 8 }}>latest</span>}</td>
                    <td className="r num"><b>{d.risk_index.toFixed(2)}</b></td>
                    <td className={`r num ${sel ? 'muted' : d.risk_index - base.risk_index > 0 ? 'neg' : 'pos'}`}>{sel ? '—' : signed(d.risk_index - base.risk_index)}</td>
                    <td className={`r num ${d.avg_sentiment <= -0.15 ? 'neg' : ''}`}>{signed(d.avg_sentiment)}</td>
                    <td className={`r num ${sel ? 'muted' : d.avg_sentiment - base.avg_sentiment < 0 ? 'neg' : 'pos'}`}>{sel ? '—' : signed(d.avg_sentiment - base.avg_sentiment)}</td>
                    <td className="r num">{d.high_impact}</td>
                    <td className="r num">{d.signals}</td>
                    <td style={{ paddingLeft: 28 }}>{d.top_sector || '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid-2 section">
        <div>
          <SectionHead title="Risk landscape" aside="Daily index and aggregate sentiment" />
          <LineChart
            xLabels={land.map((d) => shortDate(d.date))}
            height={210}
            fmt={(v) => v.toFixed(1)}
            series={[{ name: 'Risk index', color: COLORS.brick, points: land.map((d) => d.risk_index), dots: true, area: true }]}
          />
          <LineChart
            xLabels={land.map((d) => shortDate(d.date))}
            height={120}
            zero
            fmt={(v) => v.toFixed(1)}
            series={[{ name: 'Sentiment', color: COLORS.ink, points: land.map((d) => d.avg_sentiment), dots: true }]}
          />
          <div className="legend"><span><i style={{ background: COLORS.brick }} />Risk index (0–10)</span><span><i style={{ background: COLORS.ink }} />Mean signal sentiment (−1 to +1)</span></div>

          <SectionHead title="Top risk signals" aside={`Latest day · select a row for the full anatomy`}>
            <a className="linkbtn" href="#/signals">All signals →</a>
          </SectionHead>
          <SignalTable items={m.top_signals} />
        </div>

        <aside>
          <SectionHead title="Active portfolio" aside="Synthetic" />
          <Status state={portfolio}>
            {portfolio.data && <PortfolioSnapshot p={portfolio.data} />}
          </Status>

          <SectionHead title="Recent portfolio impact" aside="Top signals replayed as stress scenarios" />
          <Status state={impact}>
            {impact.data && (
              <table className="t">
                <tbody>
                  {impact.data.map(({ signal, result }) => (
                    <tr key={signal.event_id} className="click" onClick={() => go(`stress/${signal.event_id}`)}>
                      <td><div className="ev-name">{signal.event_name}</div><div className="ev-sub">{signal.event_label} · impact {signal.impact_score.toFixed(1)}</div></td>
                      <td className="r num"><span className={result.pct_impact < 0 ? 'neg' : 'pos'}>{pct(result.pct_impact, 2, true)}</span><div className="ev-sub">{usd(result.pnl, true)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Status>

          <SectionHead title="Sector pressure" aside="3-day, impact × confidence × adversity" />
          <DivergingBars rows={m.sector_pressure.slice(0, 6).map((s) => ({ label: s.sector, value: s.share }))} fmt={(v) => pct(v, 0)} positiveGood={false} />
        </aside>
      </div>

      {comparison && top && (
        <section className="section">
          <SectionHead title="One event, four portfolios" aside={`“${impact.data[0].signal.event_name}” — the risk of an event is not universal`} />
          <div className="grid-2 even">
            <DivergingBars labelWidth={190} rows={comparison.map((c) => ({ label: c.portfolio_name, value: c.pct_impact, sub: c.portfolio_id === ctx.portfolioId ? '· active' : '' }))} fmt={(v) => pct(v, 2, true)} />
            <p className="prose">
              The same {impact.data[0].signal.event_label.toLowerCase()} event (impact {impact.data[0].signal.impact_score.toFixed(1)}/10) produces very different outcomes depending on what each portfolio holds.
              The stress engine applies configured event-to-shock assumptions to each holding; the largest driver for the worst-hit portfolio is <b>{comparison[0].main_driver.toLowerCase()}</b>.
              <button className="linkbtn" style={{ marginLeft: 6 }} onClick={() => go(`stress/${impact.data[0].signal.event_id}`)}>Open in Stress Lab →</button>
            </p>
          </div>
        </section>
      )}
    </>
  )
}

function coverage(m) {
  const c = m.coverage
  const types = Object.entries(c.by_source_type || {}).map(([k, v]) => `${v.toLocaleString()} ${k}`).join(', ')
  return `Ingesting ${c.documents.toLocaleString()} documents (${types}) from ${c.sources.toLocaleString()} sources.`
}

function PortfolioSnapshot({ p }) {
  return (
    <div>
      <div className="split"><h3 style={{ fontSize: 26 }}>{p.name}</h3><span className="serif num" style={{ fontSize: 26 }}>{usd(p.total_value)}</span></div>
      <p className="muted" style={{ margin: '4px 0 14px' }}>{p.strategy}</p>
      {p.equities_live && <p className="note" style={{ margin: '0 0 14px' }}>Equity positions are valued at live prices ({p.priced_at.slice(0, 16)} UTC). Bonds, loans and derivatives are synthetic and stay at their original marks.</p>}
      <StackedBar parts={p.by_asset_class.map((a) => ({ label: cap(a.key), value: a.weight, color: CLASS_COLORS[a.key] }))} fmt={(v) => pct(v, 0)} />
      <dl className="dl" style={{ margin: '16px 0 10px' }}>
        <dt>Holdings</dt><dd className="num">{p.n_holdings}</dd>
        {p.equity_beta !== null && <><dt>Equity beta</dt><dd className="num">{p.equity_beta.toFixed(2)}</dd></>}
        <dt>Bond duration</dt><dd className="num">{p.bond_duration.toFixed(1)} yrs</dd>
        <dt>Loan notional</dt><dd className="num">{usd(p.loan_notional)}</dd>
        <dt>Derivative notional</dt><dd className="num">{usd(p.derivative_notional)}</dd>
      </dl>
      <table className="t">
        <thead><tr><th>Top holdings</th><th className="r">Weight</th></tr></thead>
        <tbody>
          {p.top_holdings.slice(0, 5).map((h) => (
            <tr key={h.holding_id}><td>{h.name}<span className="ev-sub"> {h.asset_class}</span></td><td className="r num">{pct(h.weight, 1)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
