import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import FactsViz from '../components/FactsViz.jsx'

const STARTERS = [
  'Why is my portfolio exposed to this event?',
  'Which holdings are most vulnerable?',
  'Why did my risk score increase?',
  'What happens if rates rise 2%?',
  'Explain this stress-test result.',
  'What would the rebalancer change?',
]
const STORE = 'copilot-chat-v1'
const clock = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const load = () => { try { return JSON.parse(sessionStorage.getItem(STORE) || '[]') } catch { return [] } }

export default function Copilot({ ctx, llm }) {
  const [log, setLog] = useState(load)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef(null)
  const inputRef = useRef(null)
  const portfolio = ctx.portfolios.find((p) => p.portfolio_id === ctx.portfolioId)
  const lastBotId = [...log].reverse().find((m) => m.role === 'copilot')?.id   // the newest answer shows its visuals open

  // keep the newest message in view (scroll the chat, not the page) and remember the conversation for this tab
  useEffect(() => {
    try { sessionStorage.setItem(STORE, JSON.stringify(log.slice(-40))) } catch { /* private mode */ }
    const el = logRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [log, busy])

  const ask = async (q) => {
    const message = (q ?? text).trim()
    if (message.length < 2 || busy) return
    setText('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
    setLog((l) => [...l, { id: Date.now(), role: 'user', text: message, time: clock() }])
    setBusy(true)
    try {
      const r = await api.copilot({ message, portfolio_id: ctx.portfolioId })
      setLog((l) => [...l, { id: Date.now() + 1, role: 'copilot', text: r.answer, time: clock(), source: r.source, facts: r.facts, note: r.note, portfolio: portfolio?.name }])
    } catch (e) {
      setLog((l) => [...l, { id: Date.now() + 1, role: 'copilot', text: `Sorry, that request failed: ${e.message}`, time: clock(), source: 'error' }])
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() }
  }
  const grow = (e) => { e.target.style.height = 'auto'; e.target.style.height = `${Math.min(e.target.scrollHeight, 140)}px` }
  const clear = () => { setLog([]); try { sessionStorage.removeItem(STORE) } catch { /* ignore */ } }

  return (
    <>
      <div className="head-row">
        <div>
          <div className="kicker">Optional layer</div>
          <h1 className="page-title">Risk copilot.</h1>
          <p className="lede">Ask in plain English. Every figure in an answer is computed by the Risk Engine, Stress Lab or Rebalancer first; a local language model only rephrases those facts. It answers about <b>{portfolio?.name || ctx.portfolioId}</b> (change it in the header).</p>
        </div>
      </div>

      <section className="cp-shell" aria-label="Chat with the risk copilot">
        <header className="cp-bar">
          <span className="llm"><i className={`dot ${llm?.available ? 'on' : ''}`} />{llm?.available ? `Answering with ${llm.model}` : 'No local model running · answers use built-in explanations'}</span>
          {log.length > 0 && <button className="linkbtn" onClick={clear}>Clear chat</button>}
        </header>

        <div className="cp-log" ref={logRef} role="log" aria-live="polite">
          <div className="cp-row bot">
            <div className="cp-bubble bot">
              <div className="cp-meta"><b>Copilot</b></div>
              <p>Hello. I explain results the engine has already calculated: how exposed <b>{portfolio?.name || 'your portfolio'}</b> is to a news event, which holdings would suffer most, what a rate rise would do, and what the rebalancer would change. Pick a question below or type your own.</p>
            </div>
          </div>

          {log.map((m) => (
            <div key={m.id} className={`cp-row ${m.role === 'user' ? 'me' : 'bot'}`}>
              <div className={`cp-bubble ${m.role === 'user' ? 'me' : 'bot'} ${m.source === 'error' ? 'err' : ''}`}>
                <div className="cp-meta"><b>{m.role === 'user' ? 'You' : 'Copilot'}</b><span>{m.time}</span></div>
                <p>{m.text}</p>
                {m.role === 'copilot' && m.source && m.source !== 'error' && (
                  <div className="cp-foot">{m.source === 'template' ? 'Built-in explanation (no model)' : `Rephrased by ${m.source.replace('ollama:', '')}`}{m.portfolio ? ` · ${m.portfolio}` : ''}</div>
                )}
                {m.note && <div className="cp-note">{m.note}</div>}
                {m.facts && Object.keys(m.facts).length > 0 && (
                  <details className="cp-facts" open={m.id === lastBotId}><summary>Data behind this answer</summary><FactsViz facts={m.facts} /></details>
                )}
              </div>
            </div>
          ))}

          {busy && (
            <div className="cp-row bot">
              <div className="cp-bubble bot cp-typing" aria-label="Copilot is writing"><span /><span /><span /></div>
            </div>
          )}
        </div>

        <div className="cp-compose">
          <div className="cp-chips">
            {STARTERS.map((s) => <button key={s} className="chip chipbtn" disabled={busy} onClick={() => ask(s)}>{s}</button>)}
          </div>
          <div className="cp-input">
            <textarea ref={inputRef} rows={1} value={text} placeholder="Ask about exposure, vulnerable holdings, a rate shock…" aria-label="Your question"
              onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onInput={grow} />
            <button className="btn" disabled={busy || text.trim().length < 2} onClick={() => ask()}>{busy ? 'Thinking…' : 'Send'}</button>
          </div>
          <div className="cp-help">Enter to send · Shift+Enter for a new line · each question is answered on its own</div>
        </div>
      </section>
    </>
  )
}
