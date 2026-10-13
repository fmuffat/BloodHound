import { useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'

// Reverse DNS (PTR) for destination and client addresses, as in sFlow Analytics.
export default function DnsPanel() {
  const [data, setData]       = useState(null)
  const [form, setForm]       = useState({ enabled: true, servers: '', max: 500 })
  const [busy, setBusy]       = useState(false)
  const [msg, setMsg]         = useState(null)   // {ok, text}

  const apply = (d) => {
    setData(d)
    setForm({ enabled: d.enabled, servers: (d.servers || []).join(', '), max: d.max_per_run })
  }
  const load = useCallback(() => api.getDnsSettings().then(apply).catch(e => setMsg({ ok: false, text: e.detail || e.message })), [])
  useEffect(() => { load() }, [load])

  const save = async () => {
    setBusy(true); setMsg(null)
    try {
      apply(await api.saveDnsSettings({
        enabled: form.enabled,
        servers: form.servers.split(/[,\s]+/).filter(Boolean),
        max_per_run: Number(form.max),
      }))
      setMsg({ ok: true, text: 'DNS settings saved.' })
    } catch (e) { setMsg({ ok: false, text: e.detail || e.message }) }
    finally { setBusy(false) }
  }

  const resolveNow = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await api.resolveDnsNow()
      setData(d => ({ ...d, cache: r.cache }))
      setMsg({ ok: true, text: r.enabled
        ? `${r.resolved} name(s) found, ${r.failed} address(es) without name${r.skipped ? `, ${r.skipped} private address(es) skipped` : ''}.`
        : 'Reverse DNS is disabled.' })
    } catch (e) { setMsg({ ok: false, text: e.detail || e.message }) }
    finally { setBusy(false) }
  }

  const c = data?.cache
  return (
    <div className="settings-panel">
      <div className="panel-title">DNS</div>
      <div className="panel-desc">
        Bloodhound resolves IP addresses to host names (PTR records) in the background, every 5 minutes,
        for the addresses seen in searches. Names are cached 1 hour; addresses without a name are retried after 5 minutes.
      </div>

      <div className="form-section">
        <label className="toggle-label">
          <input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />
          <span className="toggle-text">Resolve IP addresses to host names</span>
        </label>
      </div>

      <div className="form-row">
        <div>
          <div className="form-label">DNS servers</div>
          <input className="form-input" value={form.servers} placeholder="192.168.1.1, 10.0.0.53"
                 onChange={e => setForm({ ...form, servers: e.target.value })} />
          <div className="form-hint">
            Up to 4, separated by commas. Empty = the appliance's own resolver.
            With your internal DNS servers, <strong>private addresses</strong> (192.168.x.x, 10.x.x.x…) are resolved too.
          </div>
        </div>
        <div>
          <div className="form-label">Lookups per cycle</div>
          <input className="form-input" type="number" min={10} max={5000} value={form.max}
                 onChange={e => setForm({ ...form, max: e.target.value })} />
          <div className="form-hint">10 to 5000, every 5 minutes.</div>
        </div>
      </div>

      {c && (
        <div className="form-section">
          <div className="form-label">Cache</div>
          <div className="form-hint" style={{ marginTop: 0 }}>
            <strong>{c.names}</strong> names · <strong>{c.no_name}</strong> without name · <strong>{c.pending}</strong> waiting for the next cycle
          </div>
        </div>
      )}

      {msg && <div className={msg.ok ? 'form-success' : 'form-error'}>{msg.ok ? '✓' : '⚠'} {msg.text}</div>}

      <div className="form-actions">
        <button className="btn-ghost" onClick={resolveNow} disabled={busy || !data?.enabled}>
          {busy ? 'Working…' : 'Resolve now'}
        </button>
        <button className="btn-primary" onClick={save} disabled={busy}>Save</button>
      </div>
      <div className="form-hint">
        Lookups send the IP addresses to the configured DNS servers. Changing the servers retries the
        addresses that had no name.
      </div>
    </div>
  )
}
