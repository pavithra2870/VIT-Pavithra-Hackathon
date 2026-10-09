import { useEffect, useRef, useState } from 'react'

// Restrained SVG charts. They only map already-computed API values to pixels.
export const COLORS = { ink: '#17191C', stone: '#6F706D', hair: '#D9D6CC', brick: '#A63D32', forest: '#476653', gold: '#B38B45' }
export const CLASS_COLORS = { equity: '#17191C', bond: '#8d8e8a', loan: '#B38B45', derivative: '#A63D32' }

export function useWidth() {
  const ref = useRef(null)
  const [w, setW] = useState(640)
  useEffect(() => {
    if (!ref.current) return undefined
    const ro = new ResizeObserver((e) => setW(Math.max(280, e[0].contentRect.width)))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

const niceTicks = (min, max, n = 4) => {
  if (min === max) { min -= 1; max += 1 }
  const step = (max - min) / n
  const mag = 10 ** Math.floor(Math.log10(step))
  const s = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((m) => m >= step) || step
  const lo = Math.floor(min / s) * s
  const hi = Math.ceil(max / s) * s
  const t = []
  for (let v = lo; v <= hi + s / 2; v += s) t.push(+v.toFixed(10))
  return t
}

/** series: [{name,color,width,dash,points:[y...] , label?}] over shared xLabels */
export function LineChart({ xLabels, series, height = 220, fmt = (v) => v, zero = false, endLabels = false, yDomain, xEvery, hover = false }) {
  const [ref, w] = useWidth()
  const [hv, setHv] = useState(null)
  const m = { t: 10, r: endLabels ? 70 : 12, b: 24, l: 44 }
  const all = series.flatMap((s) => s.points.filter((p) => p !== null && p !== undefined))
  const [dmin, dmax] = yDomain || [Math.min(...all, zero ? 0 : Infinity), Math.max(...all, zero ? 0 : -Infinity)]
  const ticks = niceTicks(dmin, dmax)
  const lo = ticks[0]
  const hi = ticks[ticks.length - 1]
  const x = (i) => m.l + (xLabels.length === 1 ? 0 : (i * (w - m.l - m.r)) / (xLabels.length - 1))
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo || 1)) * (height - m.t - m.b)
  // end labels: push apart so neighbouring lines never print on top of each other
  const labelY = {}
  if (endLabels) {
    const ends = series.map((s) => { const v = [...s.points].reverse().find((p) => p !== null && p !== undefined); return v === undefined ? null : { name: s.name, y: y(v) } }).filter(Boolean).sort((a, b) => a.y - b.y)
    let prev = -1e9
    ends.forEach((e) => { e.ly = Math.max(e.y, prev + 12); prev = e.ly; labelY[e.name] = e.ly })
  }
  const onMove = (ev) => {
    if (!hover) return
    const box = ev.currentTarget.getBoundingClientRect()
    const i = Math.round(((ev.clientX - box.left - m.l) / Math.max(w - m.l - m.r, 1)) * (xLabels.length - 1))
    setHv(Math.min(Math.max(i, 0), xLabels.length - 1))
  }
  const every = xEvery || Math.ceil(xLabels.length / Math.max(2, Math.floor(w / 80)))
  return (
    <div ref={ref} style={{ width: '100%', position: 'relative' }}>
      <svg width={w} height={height} role="img" aria-label="line chart" onMouseMove={onMove} onMouseLeave={() => setHv(null)} style={{ display: 'block' }}>
        <g className="axis">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={m.l} x2={w - m.r} y1={y(t)} y2={y(t)} stroke={t === 0 && zero ? COLORS.ink : COLORS.hair} strokeWidth={t === 0 && zero ? 1 : 0.6} />
              <text x={m.l - 8} y={y(t) + 3.5} textAnchor="end">{fmt(t)}</text>
            </g>
          ))}
          {xLabels.map((l, i) => ((i === xLabels.length - 1 || (i % every === 0 && xLabels.length - 1 - i >= Math.ceil(every / 2))) ? <text key={l + i} x={x(i)} y={height - 6} textAnchor="middle">{l}</text> : null))}
        </g>
        {series.map((s) => {
          const pts = s.points.map((p, i) => (p === null || p === undefined ? null : [x(i), y(p)])).filter(Boolean)
          const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')
          const last = pts[pts.length - 1]
          return (
            <g key={s.name}>
              {s.area && pts.length > 1 && <path d={`${d} L${last[0]},${y(lo)} L${pts[0][0]},${y(lo)} Z`} fill={s.color} opacity="0.07" />}
              <path d={d} fill="none" stroke={s.color} strokeWidth={s.width || 1.6} strokeDasharray={s.dash} strokeLinejoin="round" />
              {s.dots && pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="2.6" fill={COLORS.ink === s.color ? '#F7F6F2' : s.color} stroke={s.color} strokeWidth="1.2" />)}
              {endLabels && last && <text x={last[0] + 6} y={(labelY[s.name] ?? last[1]) + 3.5} fontSize="11" fontWeight="600" fill={s.color === COLORS.hair ? COLORS.stone : s.color}>{s.name}</text>}
            </g>
          )
        })}
        {hover && hv !== null && <line x1={x(hv)} x2={x(hv)} y1={m.t} y2={height - m.b} stroke={COLORS.stone} strokeWidth="0.8" strokeDasharray="3 3" />}
        {hover && hv !== null && series.map((s) => ((s.points[hv] === null || s.points[hv] === undefined) ? null : <circle key={s.name} cx={x(hv)} cy={y(s.points[hv])} r="3.6" fill={s.color} />))}
      </svg>
      {hover && hv !== null && (
        <div className="chart-tip" style={{ left: Math.min(Math.max(x(hv) + 12, 0), w - 170), top: 6 }}>
          <b>{xLabels[hv]}</b>
          {series.filter((s) => s.points[hv] !== null && s.points[hv] !== undefined).sort((a, b) => b.points[hv] - a.points[hv]).map((s) => (
            <div key={s.name}><i style={{ background: s.color }} />{s.name}<span className="num">{fmt(s.points[hv])}</span></div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Horizontal diverging bars: rows [{label, value, text?, sub?}] — value sign decides colour/side */
export function DivergingBars({ rows, fmt, max, positiveGood = true, labelWidth = 150 }) {
  const m = max || Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9)
  const hasNeg = rows.some((r) => r.value < 0)
  const hasPos = rows.some((r) => r.value > 0)
  const mid = hasNeg && hasPos
  return (
    <div>
      {rows.map((r) => {
        const frac = Math.min(Math.abs(r.value) / m, 1)
        const good = positiveGood ? r.value >= 0 : r.value < 0
        const color = r.value === 0 ? COLORS.hair : good ? COLORS.forest : COLORS.brick
        const style = mid
          ? { left: r.value >= 0 ? '50%' : `${50 - frac * 50}%`, width: `${frac * 50}%` }
          : { left: r.value >= 0 || !hasNeg ? 0 : undefined, right: r.value < 0 && !hasPos ? 0 : undefined, width: `${frac * 100}%` }
        return (
          <div className="bar-row" key={r.key || r.label} style={{ gridTemplateColumns: `${labelWidth}px 1fr 84px` }}>
            <div className="lbl" title={r.label}>{r.label}{r.sub && <span className="muted" style={{ fontSize: 11 }}> {r.sub}</span>}</div>
            <div className={`track ${mid ? 'mid' : ''}`}>
              <div className="fill" style={{ ...style, background: color }} />
            </div>
            <div className="val num">{fmt ? fmt(r.value) : r.value}</div>
          </div>
        )
      })}
    </div>
  )
}

/** Paired bars (before vs after) for a value in dollars; baseline need not start at zero for readability. */
export function BeforeAfter({ before, after, fmt, height = 210 }) {
  const [ref, w] = useWidth()
  const lo = Math.min(before, after)
  const hi = Math.max(before, after)
  const pad = (hi - lo) * 3 || hi * 0.05
  const base = Math.max(0, lo - pad)
  const m = { t: 34, b: 28 }
  const y = (v) => m.t + (1 - (v - base) / (hi - base || 1)) * (height - m.t - m.b)
  const bw = Math.min(110, w / 4)
  const gx = [w * 0.22, w * 0.58]
  const loss = after < before
  return (
    <div ref={ref} style={{ width: '100%' }}>
      <svg width={w} height={height} role="img" aria-label="portfolio value before and after">
        <line x1="0" x2={w} y1={height - m.b} y2={height - m.b} stroke={COLORS.ink} />
        {[['Before', before, COLORS.ink], ['After', after, loss ? COLORS.brick : COLORS.forest]].map(([lbl, v, c], i) => (
          <g key={lbl}>
            <rect x={gx[i]} y={y(v)} width={bw} height={height - m.b - y(v)} fill={c} />
            <text x={gx[i] + bw / 2} y={y(v) - 8} textAnchor="middle" fontSize="13" fontWeight="500" fill={COLORS.ink}>{fmt(v)}</text>
            <text x={gx[i] + bw / 2} y={height - 9} textAnchor="middle" fontSize="10.5" fill={COLORS.stone} style={{ letterSpacing: '0.12em', textTransform: 'uppercase' }}>{lbl}</text>
          </g>
        ))}
        <text x={w - 6} y={m.t + 4} textAnchor="end" fontSize="10" fill={COLORS.stone}>Axis truncated to show the move</text>
      </svg>
    </div>
  )
}

/** Single stacked allocation bar with legend */
export function StackedBar({ parts, fmt }) {
  return (
    <div>
      <div style={{ display: 'flex', height: 12, background: COLORS.hair }}>
        {parts.map((p) => <div key={p.label} title={`${p.label} ${fmt(p.value)}`} style={{ width: `${p.value * 100}%`, background: p.color }} />)}
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        {parts.map((p) => <span key={p.label}><i style={{ background: p.color, height: 8, width: 8, borderRadius: 0 }} />{p.label} <span className="num">{fmt(p.value)}</span></span>)}
      </div>
    </div>
  )
}

export function Impact({ score, high }) {
  const n = Math.round(score)
  return (
    <span className={`impact ${high ? 'hi' : ''}`} title={`Impact ${score.toFixed(1)} / 10`}>
      <b className="num">{score.toFixed(1)}</b>
      <span className="pips">{Array.from({ length: 10 }, (_, i) => <i key={i} className={i < n ? 'on' : ''} />)}</span>
    </span>
  )
}

export function Sparkline({ values, width = 120, height = 28, color = COLORS.ink }) {
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const x = (i) => (i * (width - 4)) / (values.length - 1 || 1) + 2
  const y = (v) => 2 + (1 - (v - lo) / (hi - lo || 1)) * (height - 4)
  return (
    <svg width={width} height={height} aria-hidden="true">
      <path d={values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')} fill="none" stroke={color} strokeWidth="1.4" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="2.4" fill={color} />
    </svg>
  )
}
