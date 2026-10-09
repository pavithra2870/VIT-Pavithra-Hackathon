// Presentation-only formatting helpers (no domain logic).
export const usd = (x, signed = false) => {
  if (x === null || x === undefined || Number.isNaN(x)) return '—'
  const a = Math.abs(x)
  const s = a >= 1e9 ? `${(a / 1e9).toFixed(2)}bn` : a >= 1e6 ? `${(a / 1e6).toFixed(1)}m` : a >= 1e3 ? `${(a / 1e3).toFixed(0)}k` : a.toFixed(0)
  const sign = x < 0 ? '−' : signed ? '+' : ''
  return `${sign}$${s}`
}
export const pct = (x, d = 1, signed = false) => {
  if (x === null || x === undefined || Number.isNaN(x)) return '—'
  const v = (x * 100).toFixed(d)
  return `${x < 0 ? '−' : signed && x > 0 ? '+' : ''}${Math.abs(v).toFixed(d)}%`
}
export const bps = (x, signed = true) => {
  const v = Math.round(x * 10000)
  return `${v < 0 ? '−' : signed && v > 0 ? '+' : ''}${Math.abs(v)} bp`
}
export const num = (x, d = 2) => (x === null || x === undefined ? '—' : Number(x).toFixed(d))
export const signed = (x, d = 2) => (x === null || x === undefined ? '—' : `${x < 0 ? '−' : x > 0 ? '+' : ''}${Math.abs(x).toFixed(d)}`)
export const shortDate = (s) => {
  if (!s) return ''
  const d = new Date(`${s}T00:00:00`)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}
export const longDate = (s) => {
  if (!s) return ''
  return new Date(`${s}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
}
export const title = (s) => (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
export const sentClass = (s) => (s <= -0.15 ? 'neg' : s >= 0.15 ? 'pos' : 'muted')
export const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '')
