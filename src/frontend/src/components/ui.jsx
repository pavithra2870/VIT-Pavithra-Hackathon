import { Impact } from './charts.jsx'
import { go } from '../lib/hooks'
import { sentClass, signed, title } from '../lib/format'

export function Status({ state, children }) {
  if (state.loading && !state.data) return <div className="loading">Reading the wire…</div>
  if (state.error) return <div className="err">Could not load: {String(state.error.message || state.error)}</div>
  return children
}

export const SectionHead = ({ title: t, aside, children }) => (
  <div className="section-head">
    <h2>{t}</h2>
    {aside && <span className="aside">{aside}</span>}
    {children}
  </div>
)

export function SignalTable({ items, selected, onSelect, compact = false }) {
  return (
    <div className="scroll-x">
      <table className="t">
        <thead>
          <tr>
            <th>Event</th>
            {!compact && <th>Class</th>}
            <th className="r">Sentiment</th>
            <th className="r">Impact</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.event_id} className={`click ${selected === s.event_id ? 'sel' : ''}`} onClick={() => (onSelect ? onSelect(s.event_id) : go(`signals/${s.event_id}`))}>
              <td className="ev">
                <div className="ev-name">{s.event_name}</div>
                <div className="ev-sub">{s.subject} · {s.n_sources} source{s.n_sources === 1 ? '' : 's'} · {s.source_types.join(' + ')}</div>
              </td>
              {!compact && <td><span className="chip">{s.event_label.split(' / ')[0]}</span></td>}
              <td className={`r num ${sentClass(s.sentiment)}`}>{signed(s.sentiment)}</td>
              <td className="r"><Impact score={s.impact_score} high={s.high_impact} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export const EventTypeChip = ({ type, label }) => <span className="chip ink">{label || title(type)}</span>
