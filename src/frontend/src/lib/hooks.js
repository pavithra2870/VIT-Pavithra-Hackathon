import { useEffect, useState } from 'react'

/** Run an async loader whenever deps change. Returns {data, error, loading}. Stale responses are ignored. */
export function useAsync(loader, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: null }))
    loader()
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error, loading: false }))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}

/** Minimal hash router: #/overview, #/signals/<id>, #/stress/<id> ... */
export function useHashRoute() {
  const parse = () => {
    const [page = 'overview', param = ''] = window.location.hash.replace(/^#\/?/, '').split('/')
    return { page: page || 'overview', param }
  }
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const h = () => setRoute(parse())
    window.addEventListener('hashchange', h)
    return () => window.removeEventListener('hashchange', h)
  }, [])
  return route
}

export const go = (path) => { window.location.hash = `#/${path}` }
