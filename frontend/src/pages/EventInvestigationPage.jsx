import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { api } from '../utils/api'
import { usePrefs } from '../utils/PrefsContext'
import { useUser, canManageLogs } from '../utils/UserContext'
import './InvestigationPage.css'
import './EventInvestigationPage.css'

function formatTs(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' })
}

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}

function Field({ label, value, mono, highlight, highlightSponsor }) {
  if (!value) return null
  return (
    <div className={`client-field ${highlight ? 'field-highlight' : ''} ${highlightSponsor ? 'field-highlight-sponsor' : ''}`}>
      <span className="field-label">{label}</span>
      <span className={`field-value ${mono ? 'mono-sm' : ''}`}>{value}</span>
    </div>
  )
}

function ZipModal({ onExport, onClose, loading, error }) {
  const [pwd, setPwd]   = useState('')
  const [pwd2, setPwd2] = useState('')
  const match    = pwd && pwd2 && pwd === pwd2
  const mismatch = pwd && pwd2 && pwd !== pwd2

  return (
    <div style={{
      position:'fixed', inset:0, background:'rgba(0,0,0,0.7)',
      display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000
    }}>
      <div style={{
        background:'var(--bg-card)', border:'1px solid var(--border)',
        borderRadius:8, padding:28, width:360
      }}>
        <div style={{fontFamily:'var(--mono)', fontSize:14, fontWeight:700, color:'var(--accent)', marginBottom:16}}>
          EXPORT ZIP
        </div>
        <div style={{fontFamily:'var(--mono)', fontSize:11, color:'var(--text-dim)', marginBottom:20}}>
          The ZIP is encrypted with AES-256. Use 7-Zip or WinRAR to open it. Windows Explorer does not support AES encryption.
        </div>
        <div style={{display:'flex', flexDirection:'column', gap:12}}>
          <div>
            <div style={{fontFamily:'var(--mono)', fontSize:10, color:'var(--text-dim)', textTransform:'uppercase', marginBottom:4}}>Password</div>
            <input type="password" value={pwd} onChange={e=>setPwd(e.target.value)}
              style={{width:'100%', padding:'8px 12px', boxSizing:'border-box', background:'var(--bg)', border:'1px solid var(--border)', borderRadius:4, color:'var(--text)', fontFamily:'var(--mono)'}} />
          </div>
          <div>
            <div style={{fontFamily:'var(--mono)', fontSize:10, color:'var(--text-dim)', textTransform:'uppercase', marginBottom:4}}>Confirm password</div>
            <input type="password" value={pwd2} onChange={e=>setPwd2(e.target.value)}
              onKeyDown={e=>e.key==='Enter' && match && onExport(pwd)}
              style={{width:'100%', padding:'8px 12px', boxSizing:'border-box', background:'var(--bg)', border:`1px solid ${mismatch?'var(--red)':match?'var(--green,#22c55e)':'var(--border)'}`, borderRadius:4, color:'var(--text)', fontFamily:'var(--mono)'}} />
            {match    && <div style={{fontFamily:'var(--mono)', fontSize:10, color:'var(--green,#22c55e)', marginTop:2}}>✓ Passwords match</div>}
            {mismatch && <div style={{fontFamily:'var(--mono)', fontSize:10, color:'var(--red)', marginTop:2}}>✗ Passwords do not match</div>}
          </div>
        </div>
        {error && <div style={{fontFamily:'var(--mono)', fontSize:11, color:'var(--red)', marginTop:8}}>⚠ {error}</div>}
        <div style={{display:'flex', gap:8, marginTop:20}}>
          <button onClick={onClose}
            style={{flex:1, padding:'8px', background:'none', border:'1px solid var(--border)', borderRadius:4, color:'var(--text-dim)', fontFamily:'var(--mono)', cursor:'pointer'}}>
            Cancel
          </button>
          <button onClick={() => onExport(pwd)} disabled={loading || !match}
            style={{flex:2, padding:'8px', background:'var(--accent)', border:'none', borderRadius:4, color:'#fff', fontFamily:'var(--mono)', fontWeight:700, cursor:'pointer', opacity:(loading||!match)?0.4:1}}>
            {loading ? 'Exporting...' : 'Download ZIP'}
          </button>
        </div>
      </div>
    </div>
  )
}

function EraseClientModal({ mac, onConfirm, onClose, loading, error, result }) {
  const [pwd, setPwd]   = useState('')
  const [typed, setTyped] = useState(mac || '')
  const canConfirm = pwd && typed.trim().toUpperCase() === (mac || '').toUpperCase()

  if (result) {
    return (
      <div style={{
        position:'fixed', inset:0, background:'rgba(0,0,0,0.7)',
        display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000
      }}>
        <div style={{
          background:'var(--bg-card)', border:'1px solid var(--border)',
          borderRadius:8, padding:28, width:380
        }}>
          <div style={{fontFamily:'var(--mono)', fontSize:14, fontWeight:700, color:'var(--green,#22c55e)', marginBottom:12}}>
            ✓ ERASED
          </div>
          <div style={{fontFamily:'var(--mono)', fontSize:11, color:'var(--text-dim)', marginBottom:20}}>
            {result.deleted_logs} log entr{result.deleted_logs===1?'y':'ies'} and {result.deleted_history} history snapshot{result.deleted_history===1?'':'s'} permanently deleted for {mac}.
          </div>
          <button onClick={onClose}
            style={{width:'100%', padding:'8px', background:'var(--accent)', border:'none', borderRadius:4, color:'#fff', fontFamily:'var(--mono)', fontWeight:700, cursor:'pointer'}}>
            Close
          </button>
        </div>
      </div>
    )
  }

  return (
    <div style={{
      position:'fixed', inset:0, background:'rgba(0,0,0,0.7)',
      display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000
    }}>
      <div style={{
        background:'var(--bg-card)', border:'1px solid var(--red)',
        borderRadius:8, padding:28, width:380
      }}>
        <div style={{fontFamily:'var(--mono)', fontSize:14, fontWeight:700, color:'var(--red)', marginBottom:16}}>
          ⚠ ERASE ALL LOGS FOR THIS CLIENT
        </div>
        <div style={{fontFamily:'var(--mono)', fontSize:11, color:'var(--text-dim)', marginBottom:16}}>
          This permanently deletes every log entry and history record for <span style={{color:'var(--text)'}}>{mac}</span> across all platforms. This cannot be undone.
        </div>
        <div style={{display:'flex', flexDirection:'column', gap:12}}>
          <div>
            <div style={{fontFamily:'var(--mono)', fontSize:10, color:'var(--text-dim)', textTransform:'uppercase', marginBottom:4}}>
              Type the MAC address to confirm
            </div>
            <input value={typed} onChange={e=>setTyped(e.target.value)} placeholder={mac}
              style={{width:'100%', padding:'8px 12px', boxSizing:'border-box', background:'var(--bg)', border:'1px solid var(--border)', borderRadius:4, color:'var(--text)', fontFamily:'var(--mono)'}} />
          </div>
          <div>
            <div style={{fontFamily:'var(--mono)', fontSize:10, color:'var(--text-dim)', textTransform:'uppercase', marginBottom:4}}>Your password</div>
            <input type="password" value={pwd} onChange={e=>setPwd(e.target.value)}
              onKeyDown={e=>e.key==='Enter' && canConfirm && onConfirm(pwd)}
              style={{width:'100%', padding:'8px 12px', boxSizing:'border-box', background:'var(--bg)', border:'1px solid var(--border)', borderRadius:4, color:'var(--text)', fontFamily:'var(--mono)'}} />
          </div>
        </div>
        {error && <div style={{fontFamily:'var(--mono)', fontSize:11, color:'var(--red)', marginTop:8}}>⚠ {error}</div>}
        <div style={{display:'flex', gap:8, marginTop:20}}>
          <button onClick={onClose}
            style={{flex:1, padding:'8px', background:'none', border:'1px solid var(--border)', borderRadius:4, color:'var(--text-dim)', fontFamily:'var(--mono)', cursor:'pointer'}}>
            Cancel
          </button>
          <button onClick={() => onConfirm(pwd)} disabled={loading || !canConfirm}
            style={{flex:2, padding:'8px', background:'var(--red)', border:'none', borderRadius:4, color:'#fff', fontFamily:'var(--mono)', fontWeight:700, cursor:'pointer', opacity:(loading||!canConfirm)?0.4:1}}>
            {loading ? 'Erasing...' : 'Permanently erase'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function EventInvestigationPage() {
  const canErase = canManageLogs(useUser())   // read-only accounts cannot erase
  const { mac } = useParams()
  const { timezone = 'Europe/Paris' } = usePrefs() || {}
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const [clientInfo, setClient] = useState(null)
  const [timeline, setTimeline] = useState(null)
  const [loading, setLoading]   = useState(true)
  const [showZip, setShowZip]   = useState(false)
  const [zipLoading, setZipLoading] = useState(false)
  const [zipError, setZipError]     = useState(null)
  const [showErase, setShowErase]     = useState(false)
  const [eraseLoading, setEraseLoading] = useState(false)
  const [eraseError, setEraseError]     = useState(null)
  const [eraseResult, setEraseResult]   = useState(null)

  const event = {
    timestamp: searchParams.get('ts')       || '',
    src_ip:    searchParams.get('src_ip')   || '',
    dst_ip:    searchParams.get('dst_ip')   || '',
    dst_host:  searchParams.get('dst_host') || '',
    dst_port:  searchParams.get('dst_port') || '',
    proto:     searchParams.get('proto')    || '',
    ap_name:   searchParams.get('ap')       || '',
    venue:     searchParams.get('venue')    || '',
    ssid:      searchParams.get('ssid')     || '',
    id:        searchParams.get('id')       || '',
    platform:  searchParams.get('platform') || '',
  }

  useEffect(() => {
    Promise.all([
      api.lookupMac(mac, event.platform, event.timestamp).catch(() => null),
      api.getMacTimeline(mac).catch(() => null),
    ]).then(([info, tl]) => {
      if (info) setClient(info)
      if (tl)   setTimeline(tl)
    }).finally(() => setLoading(false))
  }, [mac])

  const isHostGuest = clientInfo?.guest_type === 'HostGuest'

  const handleExport = async () => {
    const rows = [
      ['Field', 'Value'],
      ['--- CLIENT INFO ---', ''],
      ['MAC Address', mac],
      ['Name', clientInfo?.alias || clientInfo?.guest_name || clientInfo?.hostname || ''],
      ['Hostname', clientInfo?.hostname || ''],
      ['Username', clientInfo?.username || ''],
      ['OS', clientInfo?.os_type || ''],
      ['Device', clientInfo?.device_type || ''],
      ['Venue', clientInfo?.venue || ''],
      ['SSID', clientInfo?.ssid || ''],
    ]
    if (isHostGuest) {
      rows.push(['--- SPONSORED ACCESS ---', ''])
      rows.push(['Guest Name', clientInfo?.guest_name || ''])
      rows.push(['Phone', clientInfo?.phone || ''])
      rows.push(['Sponsor Email', clientInfo?.sponsor_email || ''])
      rows.push(['Duration', clientInfo?.pass_duration_hours ? clientInfo.pass_duration_hours + 'h' : ''])
      rows.push(['Created', formatDate(clientInfo?.creation_date)])
      rows.push(['Expires', formatDate(clientInfo?.expiry_date)])
    } else if (clientInfo?.is_guest) {
      rows.push(['--- GUEST INFO ---', ''])
      rows.push(['Guest Name', clientInfo?.guest_name || ''])
      rows.push(['Email', clientInfo?.email || ''])
      rows.push(['Phone', clientInfo?.phone || ''])
    }
    rows.push(['--- EVENT ---', ''])
    rows.push(['Timestamp', formatTs(event.timestamp)])
    rows.push(['Source IP', event.src_ip])
    rows.push(['Destination IP', event.dst_ip])
    rows.push(['Destination Host', event.dst_host])
    rows.push(['Destination Port', event.dst_port])
    rows.push(['Protocol', event.proto])
    rows.push(['AP', event.ap_name])
    rows.push(['Venue', event.venue])
    rows.push(['SSID', event.ssid])

    const csv  = rows.map(r => r.map(v => `"${v}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `event_${mac}_${event.timestamp?.slice(0,10) || 'unknown'}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleZipExport = async (password) => {
    setZipLoading(true)
    setZipError(null)
    try {
      await api.exportEventZip(mac, event, password)
      setShowZip(false)
    } catch (e) {
      setZipError(e.message)
    } finally {
      setZipLoading(false)
    }
  }

  return (
    <div className="invest-page">
      <div className="invest-header">
        <button className="btn-ghost back-btn" onClick={() => navigate(-1)}>
          {'\u2190'} Back
        </button>
        <div className="invest-title">
          <span className="invest-label">EVENT INVESTIGATION</span>
          <span className="invest-name">{formatTs(event.timestamp)}</span>
        </div>
        <button className="btn-primary" onClick={handleExport}>
          {'\u2193'} Export CSV
        </button>
        <button className="btn-primary" onClick={() => setShowZip(true)}>
          {'\u2193'} Export ZIP
        </button>
      </div>

      {!loading && clientInfo && (
        <div className="client-card">
          <div className="client-rows">
            <div className="client-row">
              <Field label="MAC"      value={mac} mono />
              <Field label="Alias"    value={clientInfo.alias} />
              <Field label="Hostname" value={clientInfo.hostname} />
              <Field label="Username" value={clientInfo.username} />
              <Field label="OS"       value={clientInfo.os_type} />
              <Field label="Device"   value={clientInfo.device_type} />
            </div>
            <div className="client-row">
              <Field label="Venue"    value={clientInfo.venue} />
              <Field label="SSID"     value={clientInfo.ssid} />
            </div>
            {timeline && <>
              <div className="client-row">
                <Field label="First seen" value={timeline.first_seen ? new Date(timeline.first_seen.replace(' ', 'T') + 'Z').toLocaleString('fr-FR', { timeZone: timezone, dateStyle: 'short', timeStyle: 'medium' }) : null} />
                <Field label="Last seen"  value={timeline.last_seen  ? new Date(timeline.last_seen.replace(' ', 'T')  + 'Z').toLocaleString('fr-FR', { timeZone: timezone, dateStyle: 'short', timeStyle: 'medium' }) : null} />
              </div>
              <div className="client-row">
                <Field label="Total logs" value={timeline.total_logs ? timeline.total_logs.toLocaleString() + ' connections' : null} />
              </div>
            </>}
          </div>

          {canErase && <div style={{display:'flex', justifyContent:'flex-end', marginTop:8}}>
            <button onClick={() => setShowErase(true)}
              style={{padding:'6px 12px', background:'none', border:'1px solid var(--red)', borderRadius:4, color:'var(--red)', fontFamily:'var(--mono)', fontSize:11, cursor:'pointer'}}>
              Erase all logs for this client
            </button>
          </div>}

          {clientInfo.is_guest && !isHostGuest && (
            <div className="guest-block">
              <div className="block-title"><span className="badge badge-guest">GUEST</span></div>
              <div className="client-fields">
                <Field label="Guest name" value={clientInfo.guest_name} highlight />
                <Field label="Email"      value={clientInfo.email}      highlight />
                <Field label="Phone"      value={clientInfo.phone}      highlight />
              </div>
            </div>
          )}

          {isHostGuest && (
            <div className="sponsor-block">
              <div className="block-title"><span className="badge badge-sponsor">SPONSORED ACCESS</span></div>
              <div className="client-fields">
                <Field label="Guest name"      value={clientInfo.guest_name}    highlight />
                <Field label="Phone"           value={clientInfo.phone}         highlight />
                <Field label="Sponsor email"   value={clientInfo.sponsor_email} highlightSponsor />
                <Field label="Access duration" value={clientInfo.pass_duration_hours ? clientInfo.pass_duration_hours + 'h' : ''} />
                <Field label="Created"         value={formatDate(clientInfo.creation_date)} />
                <Field label="Expires"         value={formatDate(clientInfo.expiry_date)} />
              </div>
            </div>
          )}
        </div>
      )}

      <div className="event-card">
        <div className="event-card-title">
          <span className="badge-event">CONNECTION EVENT</span>
          <span className="event-ts">{formatTs(event.timestamp)}</span>
        </div>
        <div className="event-grid">
          <div className="event-field"><span className="field-label">Source IP</span><span className="field-value mono-sm">{event.src_ip || '—'}</span></div>
          <div className="event-field"><span className="field-label">Destination IP</span><span className="field-value mono-sm">{event.dst_ip || '—'}</span></div>
          <div className="event-field"><span className="field-label">Destination Host</span><span className="field-value mono-sm">{event.dst_host !== event.dst_ip ? event.dst_host : '—'}</span></div>
          <div className="event-field"><span className="field-label">Port</span><span className="field-value mono-sm">{event.dst_port || '—'}</span></div>
          <div className="event-field"><span className="field-label">Protocol</span><span className="field-value mono-sm">{event.proto || '—'}</span></div>
          <div className="event-field"><span className="field-label">SSID</span><span className="field-value mono-sm">{event.ssid || '—'}</span></div>
          <div className="event-field"><span className="field-label">AP</span><span className="field-value mono-sm">{event.ap_name || '—'}</span></div>
          <div className="event-field"><span className="field-label">Venue</span><span className="field-value mono-sm">{event.venue || '—'}</span></div>
        </div>
      </div>

      {showErase && (
        <EraseClientModal
          mac={mac}
          loading={eraseLoading}
          error={eraseError}
          result={eraseResult}
          onConfirm={async (password) => {
            setEraseLoading(true)
            setEraseError(null)
            try {
              const res = await api.eraseClient(mac, password)
              setEraseResult(res)
            } catch (e) {
              setEraseError(e.message === 'API error 401' ? 'Incorrect password' : e.message)
            } finally {
              setEraseLoading(false)
            }
          }}
          onClose={() => { setShowErase(false); setEraseError(null); setEraseResult(null) }}
        />
      )}

      {showZip && (
        <ZipModal
          onExport={handleZipExport}
          onClose={() => { setShowZip(false); setZipError(null) }}
          loading={zipLoading}
          error={zipError}
        />
      )}
    </div>
  )
}
