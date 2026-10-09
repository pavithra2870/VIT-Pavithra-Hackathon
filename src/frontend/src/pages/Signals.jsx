import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { go, useAsync } from '../lib/hooks'
import { cap, pct, shortDate, signed, sentClass, title, usd } from '../lib/format'
import { COLORS, DivergingBars, Impact } from '../components/charts.jsx'
import { SectionHead, SignalTable, Status } from '../components/ui.jsx'

export default function Signals({ ctx, eventId }) {
  const [f, setF] = useState({ event_type: '', min_impact: '', sentiment: '', source_type: '', q: '', sort: 'date' })
  const scenarios = useAsync(() => api.scenarios(), [])
  const list = useAsync(() => api.signals({ ...f, limit: 60 }), [f.event_type, f.min_impact, f.sentiment, f.source_type, f.q, f.sort])
  const upd = (k, v) => setF((s) => ({ ...s, [k]: v }))

  // default to the highest-impact signal when none is selected
  useEffect(() => {
    if (!eventId && list.data?.items?.length) go(`signals/${list.data.items[0].event_id}`)
  }, [eventId, list.data])

  return (
    <>
      <div className="head-row">
        <div>
          <div className="kicker">Risk Engine output</div>
          <h1 className="page-title">Risk signals.</h1>
          <p className="lede">Every signal is a structured, machine-readable record — sentiment, event class, impact score, confidence and the factors behind it — built from clusters of news, social and GDELT event records.</p>
        </div>
      </div>
      <div className="controls" style={{ marginBottom: 26 }}>
        <label className="ctrl"><span>Event class</span>
          <select value={f.event_type} onChange={(e) => upd('event_type', e.target.value)}>
            <option value="">All classes</option>
            {(scenarios.data || []).map((s) => <option key={s.event_type} value={s.event_type}>{title(s.event_type)}</option>)}
          </select>
        </label>
        <label className="ctrl"><span>Minimum impact</span>
          <select value={f.min_impact} onChange={(e) => upd('min_impact', e.target.value)}>
            <option value="">Any</option><option value="4">4+</option><option value="5">5+</option><option value="6">6+</option><option value="7">7+ (high)</option>
          </select>
        </label>
        <label className="ctrl"><span>Sentiment</span>
          <select value={f.sentiment} onChange={(e) => upd('sentiment', e.target.value)}>
            <option value="">All</option><option value="negative">Negative</option><option value="neutral">Neutral</option><option value="positive">Positive</option>
          </select>
        </label>
        <label className="ctrl"><span>Source</span>
          <select value={f.source_type} onChange={(e) => upd('source_type', e.target.value)}>
            <option value="">All sources</option><option value="news">News</option><option value="social">Social</option><option value="gdelt">GDELT</option>
          </select>
        </label>
        <label className="ctrl"><span>Sort</span>
          <select value={f.sort} onChange={(e) => upd('sort', e.target.value)}>
            <option value="date">Newest first</option><option value="impact">Highest impact</option><option value="confidence">Confidence</option><option value="sentiment">Most negative</option>
          </select>
        </label>
        <label className="ctrl"><span>Search</span><input type="search" placeholder="company, country, topic" value={f.q} onChange={(e) => upd('q', e.target.value)} /></label>
      </div>

      <div className="split-view">
        <div className="list">
          <Status state={list}>
            {list.data && (
              <>
                <div className="muted" style={{ marginBottom: 8, fontSize: 12 }}>{list.data.total.toLocaleString()} signals{list.data.total > 60 ? ' · showing top 60' : ''}</div>
                {list.data.items.length ? <SignalTable items={list.data.items} selected={eventId} onSelect={(id) => go(`signals/${id}`)} compact /> : <div className="empty">No signals match these filters.</div>}
              </>
            )}
          </Status>
        </div>
        <div>{eventId ? <Detail id={eventId} ctx={ctx} /> : <div className="empty">Select a signal.</div>}</div>
      </div>
    </>
  )
}

function Detail({ id, ctx }) {
  const d = useAsync(() => api.signal(id), [id])
  return (
    <Status state={d}>
      {d.data && <DetailBody d={d.data} ctx={ctx} />}
    </Status>
  )
}

function DetailBody({ d, ctx }) {
  const worst = Math.min(...d.portfolio_exposure.map((p) => p.pct_impact))
  return (
    <article>
      <div className="chips">
        <span className="chip ink">{d.event_label}</span>
        <span className="chip">{shortDate(d.signal_date)}</span>
        {d.high_impact && <span className="chip brick">High impact</span>}
        {d.source_types.map((s) => <span className="chip" key={s}>{s}</span>)}
      </div>
      <h2 className="detail-title">{d.event_name}</h2>
      <p className="muted">{[d.subject, ...d.countries.filter((c) => c !== d.subject)].join(' · ')}</p>

      <div className="kv-strip">
        <div><div className="l">Impact score</div><div className="v"><Impact score={d.impact_score} high={d.high_impact} /></div></div>
        <div><div className="l">Sentiment</div><div className={`v num ${sentClass(d.sentiment)}`}>{signed(d.sentiment)} <span style={{ fontSize: 14 }}>{d.sentiment_label}</span></div></div>
        <div><div className="l">Confidence</div><div className="v num">{pct(d.confidence, 0)}</div></div>
        <div><div className="l">Source consensus</div><div className="v num">{pct(d.source_consensus, 0)}</div></div>
      </div>

      <dl className="summary">
        {(d.summary || []).map((x) => <div key={x.label}><dt>{x.label}</dt><dd>{x.text}</dd></div>)}
      </dl>

      <SectionHead title="Why this score" aside="Points of the raw score (factor × weight × 10); the raw score is then calibrated to the 1–10 impact" />
      <DivergingBars
        labelWidth={170}
        positiveGood={false}
        max={Math.max(...d.factors.map((f) => f.contribution)) * 1.05}
        rows={d.factors.map((f) => ({ key: f.key, label: f.label, value: f.contribution, sub: `${pct(f.value, 0)} × ${pct(f.weight, 0)}` }))}
        fmt={(v) => `${(v * 10).toFixed(2)} pts`}
      />
      <p className="note" style={{ marginTop: 12 }}>Impact = 1 + 9 × calibrated(Σ weight × factor). Polarity is not the score: strong language, many agreeing sources, a named company and a volatile market all raise impact in either direction.</p>

      <div className="grid-2 even" style={{ marginTop: 10 }}>
        <div>
          <SectionHead title="Classification" />
          <dl className="dl">
            <dt>Event class</dt><dd>{d.event_type_description}</dd>
            <dt>Matched evidence</dt><dd>{d.matched_terms.length ? d.matched_terms.join(', ') : '—'}</dd>
            <dt>Novelty</dt><dd className="num">{pct(d.novelty, 0)}</dd>
            <dt>Source agreement</dt><dd className="num">{pct(d.source_agreement, 0)}</dd>
            <dt>Documents / sources</dt><dd className="num">{d.n_docs} / {d.n_sources}</dd>
          </dl>
        </div>
        <div>
          <SectionHead title="Affected" />
          <div className="muted" style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 6 }}>Companies</div>
          <div className="chips" style={{ marginBottom: 12 }}>
            {d.entities.length ? d.entities.slice(0, 8).map((e) => <span key={e.ticker} className="chip ink" title={`${e.name} · relevance ${e.relevance}`}>{e.ticker}</span>) : <span className="muted">None named</span>}
          </div>
          <div className="muted" style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 6 }}>Sectors</div>
          <div className="chips">
            {d.affected_sectors.map((s) => <span key={s} className="chip">{s}</span>)}
            {d.inferred_sectors.map((s) => <span key={s} className="chip" title="Inferred from event-class shock sensitivity">{s} · inferred</span>)}
            {!d.affected_sectors.length && !d.inferred_sectors.length && <span className="muted">—</span>}
          </div>
        </div>
      </div>

      <SectionHead title="Affected portfolio exposure" aside="Same event, each portfolio — via the stress engine">
        <button className="linkbtn" onClick={() => go(`stress/${d.event_id}`)}>Open in Stress Lab →</button>
      </SectionHead>
      <DivergingBars labelWidth={190} max={Math.max(Math.abs(worst), 0.001)} fmt={(v) => pct(v, 2, true)}
        rows={d.portfolio_exposure.map((p) => ({ key: p.portfolio_id, label: p.portfolio_name, value: p.pct_impact, sub: p.portfolio_id === ctx.portfolioId ? '· active' : '' }))} />
      <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        {d.portfolio_exposure.map((p) => `${p.portfolio_name}: ${usd(p.pnl, true)}`).join(' · ')}
      </div>

      <SectionHead title="Source consensus" aside={`${d.total_articles.toLocaleString()} underlying articles / records`} />
      <div className="grid-2 even">
        <table className="t">
          <thead><tr><th>Source type</th><th className="r">Docs</th><th className="r">Sentiment</th></tr></thead>
          <tbody>{d.source_breakdown.map((s) => <tr key={s.source_type}><td>{cap(s.source_type)}</td><td className="r num">{s.documents}</td><td className={`r num ${sentClass(s.sentiment)}`}>{signed(s.sentiment)}</td></tr>)}</tbody>
        </table>
        <table className="t">
          <thead><tr><th>Top sources</th><th className="r">Docs</th></tr></thead>
          <tbody>{d.top_sources.map((s) => <tr key={s.source}><td>{s.source}</td><td className="r num">{s.documents}</td></tr>)}</tbody>
        </table>
      </div>

      <SectionHead title="Evidence" aside="Most strongly worded documents" />
      <table className="t">
        <tbody>
          {d.sample_documents.map((x) => (
            <tr key={x.doc_id}>
              <td>{x.url ? <a href={x.url} target="_blank" rel="noreferrer">{x.title}</a> : x.title}<div className="ev-sub">{x.source_name} · {x.dataset}</div></td>
              <td className={`r num ${sentClass(x.sentiment)}`}>{signed(x.sentiment)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  )
}

export { COLORS }
