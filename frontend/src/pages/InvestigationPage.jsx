import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../utils/api'
import { usePrefs } from '../utils/PrefsContext'
import { useUser, canManageLogs } from '../utils/UserContext'
import DateRangePicker from '../components/DateRangePicker'

const PROTO_CLASS = { TCP: 'badge-tcp', UDP: 'badge-udp', ICMP: 'badge-icmp' }
import './InvestigationPage.css'

const PAGE_SIZE = 100

function formatTs(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' })
}

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
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

export default function InvestigationPage() {
  const canErase = canManageLogs(useUser())   // read-only accounts cannot erase
  const { mac } = useParams()
  const { timezone = 'Europe/Paris' } = usePrefs() || {}
  const navigate = useNavigate()

  const [logs, setLogs]         = useState([])
  const [total, setTotal]       = useState(0)
  const [page, setPage]         = useState(0)
  const [clientInfo, setClient] = useState(null)
  const [timeline, setTimeline] = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [fromDt, setFromDt]     = useState('')
  const [toDt, setToDt]         = useState('')
  const [showZipModal, setShowZipModal] = useState(false)
  const [zipPassword, setZipPassword]   = useState('')
  const [zipPassword2, setZipPassword2] = useState('')
  const [zipLoading, setZipLoading]     = useState(false)
  const [zipError, setZipError]         = useState(null)
  const [showErase, setShowErase]         = useState(false)
  const [eraseLoading, setEraseLoading]   = useState(false)
  const [eraseError, setEraseError]       = useState(null)
  const [eraseResult, setEraseResult]     = useState(null)

  const load = async (pageNum = 0) => {
    setLoading(true)
    setError(null)
    try {
      const searchPromise = api.search({
        client_mac: mac,
        from_dt: fromDt ? fromDt + ':00Z' : null,
        to_dt:   toDt   ? toDt   + ':00Z' : null,
        limit:  PAGE_SIZE,
        offset: pageNum * PAGE_SIZE,
      })

      // Only fetch client info on first load
      if (pageNum === 0) {
        const [info, result, tl] = await Promise.all([
          api.lookupMac(mac).catch(() => null),
          searchPromise,
          api.getMacTimeline(mac).catch(() => null),
        ])
        if (tl) setTimeline(tl)
        const logs = result.logs || []
        const firstLog = logs[0]
        setClient(info || (firstLog ? {
          hostname:            firstLog.hostname    || mac,
          username:            firstLog.username    || '',
          os_type:             firstLog.os_type     || '',
          device_type:         firstLog.device_type || '',
          venue:               firstLog.venue       || '',
          ssid:                firstLog.ssid        || '',
          ap_name:             firstLog.ap_name     || '',
          alias:               firstLog.alias       || '',
          is_guest:            firstLog.is_guest    || false,
          guest_name:          firstLog.guest_name  || '',
          email:               firstLog.email       || '',
          phone:               firstLog.phone       || '',
          guest_type:          firstLog.guest_type  || '',
          sponsor_email:       firstLog.sponsor_email || '',
          pass_duration_hours: firstLog.pass_duration_hours || 0,
          expiry_date:         firstLog.expiry_date || '',
          creation_date:       firstLog.creation_date || '',
        } : null))
        setLogs(logs)
        setTotal(result.total || 0)
      } else {
        const result = await searchPromise
        setLogs(result.logs || [])
        setTotal(result.total || 0)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(0) }, [mac])

  const handlePageChange = async (newPage) => {
    setPage(newPage)
    await load(newPage)
  }

  const handleRefresh = () => {
    setPage(0)
    load(0)
  }

  const handleExport = async () => {
    await api.exportInvestigation({
      client_mac: mac,
      from_dt: fromDt ? fromDt + ':00Z' : null,
      to_dt:   toDt   ? toDt   + ':00Z' : null,
      limit: 500,
    })
  }

  const handleZipExport = async () => {
    if (!zipPassword) { setZipError('Please enter a password'); return }
    if (zipPassword !== zipPassword2) { setZipError('Passwords do not match'); return }
    if (zipPassword.length < 4) { setZipError('Password must be at least 4 characters'); return }
    setZipLoading(true)
    setZipError(null)
    try {
      await api.exportZip({
        client_mac: mac,
        from_dt: fromDt ? fromDt + ':00Z' : null,
        to_dt:   toDt   ? toDt   + ':00Z' : null,
        limit: 500,
      }, zipPassword)
      setShowZipModal(false)
      setZipPassword('')
      setZipPassword2('')
    } catch (e) {
      setZipError(e.message)
    } finally {
      setZipLoading(false)
    }
  }

  const label = clientInfo?.alias || clientInfo?.guest_name ||
                clientInfo?.hostname || mac

  const isHostGuest = clientInfo?.guest_type === 'HostGuest'
  const totalPages  = Math.ceil(total / PAGE_SIZE)
  const from        = page * PAGE_SIZE + 1
  const to          = Math.min((page + 1) * PAGE_SIZE, total)

  return (
    <div className="invest-page">
      <div className="invest-header">
        <button className="btn-ghost back-btn" onClick={() => navigate('/')}>
          {'\u2190'} Back
        </button>
        <div className="invest-title">
          <span className="invest-label">INVESTIGATION</span>
          <span className="invest-name">{label}</span>
        </div>
        <button className="btn-primary" onClick={handleExport}>
          {'\u2193'} Export CSV
        </button>
        <button className="btn-primary" onClick={() => setShowZipModal(true)}>
          {'\u2193'} Export ZIP
        </button>
      </div>

      {clientInfo && (
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
              <div className="block-title">
                <span className="badge badge-guest">GUEST</span>
              </div>
              <div className="client-fields">
                <Field label="Guest name" value={clientInfo.guest_name} highlight />
                <Field label="Email"      value={clientInfo.email}      highlight />
                <Field label="Phone"      value={clientInfo.phone}      highlight />
              </div>
            </div>
          )}

          {isHostGuest && (
            <div className="sponsor-block">
              <div className="block-title">
                <span className="badge badge-sponsor">SPONSORED ACCESS</span>
              </div>
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

      <div className="invest-filters">
        <DateRangePicker
          from={fromDt ? fromDt + ':00Z' : null}
          to={toDt ? toDt + ':00Z' : null}
          onChange={(from, to) => {
            setFromDt(from ? from.slice(0,16) : '')
            setToDt(to ? to.slice(0,16) : '')
          }}
        />
        <button className="btn-ghost" onClick={handleRefresh}>Refresh</button>
        <span className="invest-count">
          {total > 0 ? `${from}–${to} / ${total.toLocaleString()} events` : '0 events'}
        </span>
      </div>

      <div className="invest-table-wrapper">
        {loading && <div className="loading-state">Loading...</div>}
        {error   && <div className="error-banner">Error: {error}</div>}
        {!loading && !error && (
          <>
            <table className="invest-table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Src IP</th>
                  <th>Destination</th>
                  <th>Port</th>
                  <th>Proto</th>
                  <th>SSID</th>
                  <th>AP</th>
                  <th>Venue</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log, i) => (
                  <tr key={log.id || i}>
                    <td className="mono-sm">
                      <button className="ts-btn" onClick={() => {
                        const params = new URLSearchParams({
                          ts: log.timestamp || '', src_ip: log.src_ip || '',
                          dst_ip: log.dst_ip || '', dst_host: log.dst_hostname || log.dst_ip || '',
                          dst_port: log.dst_port || '', proto: log.proto || '',
                          ap: log.ap_name || '', venue: log.venue || '',
                          ssid: log.ssid || '', id: log.id || '', platform: log.platform || '',
                        })
                        navigate(`/investigate/${mac}/event?${params.toString()}`)
                      }}>
                        {formatTs(log.timestamp)}
                      </button>
                    </td>
                    <td className="mono-sm">{log.src_ip}</td>
                    <td className="mono-sm dest">
                      {log.dst_hostname !== log.dst_ip ? log.dst_hostname : log.dst_ip}
                    </td>
                    <td className="mono-sm dim">{log.dst_port}</td>
                    <td>
                      {log.proto && (
                        <span className={`badge ${PROTO_CLASS[log.proto] || ''}`}>
                          {log.proto}
                        </span>
                      )}
                    </td>
                    <td className="dim ssid-cell">{log.ssid || '—'}</td>
                    <td className="dim">{log.ap_name}</td>
                    <td className="dim">{log.venue}</td>
                  </tr>
                ))}
                {logs.length === 0 && (
                  <tr><td colSpan={8} className="table-empty">No events found</td></tr>
                )}
              </tbody>
            </table>

            {totalPages > 1 && (
              <div className="pagination">
                <button
                  className="btn-ghost page-btn"
                  onClick={() => handlePageChange(page - 1)}
                  disabled={page === 0}
                >
                  {'\u2190'} Previous
                </button>
                <span className="page-info">Page {page + 1} / {totalPages}</span>
                <button
                  className="btn-ghost page-btn"
                  onClick={() => handlePageChange(page + 1)}
                  disabled={page >= totalPages - 1}
                >
                  Next {'\u2192'}
                </button>
              </div>
            )}
          </>
        )}
      </div>
      {/* ZIP export modal */}
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

      {showZipModal && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 8, padding: 28, width: 360
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
                <input type="password" value={zipPassword} onChange={e=>setZipPassword(e.target.value)}
                  style={{width:'100%', padding:'8px 12px', boxSizing:'border-box', background:'var(--bg)', border:'1px solid var(--border)', borderRadius:4, color:'var(--text)', fontFamily:'var(--mono)'}} />
              </div>
              <div>
                <div style={{fontFamily:'var(--mono)', fontSize:10, color:'var(--text-dim)', textTransform:'uppercase', marginBottom:4}}>Confirm password</div>
                <input type="password" value={zipPassword2} onChange={e=>setZipPassword2(e.target.value)}
                  onKeyDown={e=>e.key==='Enter'&&handleZipExport()}
                  style={{width:'100%', padding:'8px 12px', boxSizing:'border-box', background:'var(--bg)', border:'1px solid var(--border)', borderRadius:4, color:'var(--text)', fontFamily:'var(--mono)'}} />
              </div>
            </div>
            {zipError && <div style={{fontFamily:'var(--mono)', fontSize:11, color:'var(--red)', marginTop:8}}>⚠ {zipError}</div>}
            <div style={{display:'flex', gap:8, marginTop:20}}>
              <button onClick={()=>{setShowZipModal(false);setZipPassword('');setZipPassword2('');setZipError(null)}}
                style={{flex:1, padding:'8px', background:'none', border:'1px solid var(--border)', borderRadius:4, color:'var(--text-dim)', fontFamily:'var(--mono)', cursor:'pointer'}}>
                Cancel
              </button>
              <button onClick={handleZipExport} disabled={zipLoading}
                style={{flex:2, padding:'8px', background:'var(--accent)', border:'none', borderRadius:4, color:'#fff', fontFamily:'var(--mono)', fontWeight:700, cursor:'pointer', opacity:zipLoading?0.5:1}}>
                {zipLoading ? 'Exporting...' : 'Download ZIP'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
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
