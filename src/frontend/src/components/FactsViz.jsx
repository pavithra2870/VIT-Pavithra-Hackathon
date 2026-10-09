import { CLASS_COLORS, DivergingBars, StackedBar } from './charts.jsx'
import { cap, pct, signed, title, usd } from '../lib/format'

// Turns the structured facts the backend sent to the copilot into small visuals.
// Presentation only: every number is read from `facts`, nothing is calculated here.
const Block = ({ label, children }) => (
  <div className="viz-block">
    <div className="viz-h">{label}</div>
    {children}
  </div>
)

export default function FactsViz({ facts }) {
  const f = facts || {}
  const res = f.stress_result || f.result
  const blocks = []

  if (f.event) {
    blocks.push(
      <Block key="event" label="The event">
        <div className="viz-event"><b>{f.event.name}</b><span>{title(f.event.type)} · impact {Number(f.event.impact_score).toFixed(1)} / 10 · tone {signed(f.event.sentiment)} · {f.event.n_sources} sources · confidence {pct(f.event.confidence, 0)}</span></div>
      </Block>,
    )
  }
  if (f.scenario) {
    blocks.push(
      <Block key="scn" label="The scenario">
        <div className="viz-event"><b>Parallel rate shock {signed(f.scenario.shift_bp, 0)} bp</b><span>{f.scenario.note}</span></div>
      </Block>,
    )
  }
  if (res) {
    const loss = res.pnl < 0
    blocks.push(
      <Block key="res" label={`Result for ${f.portfolio?.name || 'the portfolio'}`}>
        <div className="viz-stats">
          <div><span>Before</span><b className="num">{usd(res.value_before)}</b></div>
          <div><span>After</span><b className="num">{usd(res.value_after)}</b></div>
          <div><span>Change</span><b className={`num ${loss ? 'neg' : 'pos'}`}>{pct(res.pct_impact, 2, true)}</b><em className="num">{usd(res.pnl, true)}</em></div>
        </div>
      </Block>,
    )
    if (res.top_losers?.length) {
      blocks.push(
        <Block key="los" label="Biggest losers (holdings)">
          <DivergingBars labelWidth={150} fmt={(v) => usd(v, true)} rows={res.top_losers.slice(0, 6).map((x) => ({ key: x.holding_id, label: x.name, value: x.pnl }))} />
        </Block>,
      )
    }
    if (res.by_driver?.length) {
      blocks.push(
        <Block key="drv" label="What drove it">
          <DivergingBars labelWidth={150} fmt={(v) => usd(v, true)} rows={res.by_driver.map((d) => ({ key: d.key, label: d.label, value: d.pnl }))} />
        </Block>,
      )
    }
    if (res.by_asset_class?.length) {
      blocks.push(
        <Block key="cls" label="By asset class">
          <DivergingBars labelWidth={110} fmt={(v) => usd(v, true)} rows={res.by_asset_class.map((c) => ({ key: c.key, label: cap(c.key), value: c.pnl, sub: c.weight != null ? `${pct(c.weight, 0)} of value` : '' }))} />
        </Block>,
      )
    }
  }
  if (f.same_event_other_portfolios?.length) {
    blocks.push(
      <Block key="all" label="Same event, every portfolio">
        <DivergingBars labelWidth={170} fmt={(v) => pct(v, 2, true)} rows={f.same_event_other_portfolios.map((c) => ({ key: c.portfolio_id, label: c.portfolio_name, value: c.pct_impact }))} />
      </Block>,
    )
  }
  if (f.event?.top_risk_factors?.length) {
    blocks.push(
      <Block key="fac" label="Why the event scores what it does">
        <DivergingBars labelWidth={170} positiveGood={false} fmt={(v) => `${(v * 10).toFixed(1)} pts`} rows={f.event.top_risk_factors.map((x) => ({ key: x.factor, label: x.factor, value: x.contribution }))} />
      </Block>,
    )
  }
  if (f.market_risk) {
    blocks.push(
      <Block key="mr" label="Market risk index">
        <div className="viz-stats"><div><span>Index</span><b className="num">{Number(f.market_risk.index).toFixed(1)} / 10</b><em>{f.market_risk.label}</em></div><div><span>Change vs prior day</span><b className="num">{signed(f.market_risk.change_1d)}</b></div></div>
      </Block>,
    )
  }
  if (f.rebalance) {
    const moves = [...(f.rebalance.largest_cuts || []), ...(f.rebalance.largest_adds || [])].map(([t, c]) => ({ key: t, label: t, value: c })).sort((a, b) => a.value - b.value)
    blocks.push(
      <Block key="rb" label={`Rebalancer: biggest moves (turnover ${pct(f.rebalance.turnover, 1)})`}>
        <DivergingBars labelWidth={90} fmt={(v) => pct(v, 1, true)} rows={moves} />
      </Block>,
    )
  }
  if (f.portfolio?.allocation) {
    blocks.push(
      <Block key="alloc" label={`${f.portfolio.name}: what it holds`}>
        <StackedBar parts={Object.entries(f.portfolio.allocation).map(([k, v]) => ({ label: cap(k), value: v, color: CLASS_COLORS[k] }))} fmt={(v) => pct(v, 0)} />
      </Block>,
    )
  }

  return (
    <div className="viz">
      {blocks}
      <details className="viz-raw"><summary>Raw data (JSON)</summary><pre>{JSON.stringify(facts, null, 2)}</pre></details>
    </div>
  )
}
