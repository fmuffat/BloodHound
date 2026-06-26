import { useNavigate } from 'react-router-dom'
import { useState, useRef, useCallback } from 'react'
import './LogTable.css'

const PROTO_CLASS = { TCP: 'badge-tcp', UDP: 'badge-udp', ICMP: 'badge-icmp' }

const COLUMNS = [
  { key: 'ts',      label: 'Timestamp',        default: 150 },
  { key: 'client',  label: 'Client',            default: 220 },
  { key: 'src_ip',  label: 'Src IP',            default: 130 },
  { key: 'dst_ip',  label: 'Destination IP',    default: 150 },
  { key: 'dns',     label: 'Dst Hostname',      default: 180 },
  { key: 'proto',   label: 'Proto',             default: 80  },
  { key: 'ap',      label: 'AP',                default: 130 },
  { key: 'venue',   label: 'Venue',             default: 120 },
  { key: 'ssid',    label: 'SSID',              default: 130 },
]

function formatTs(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' })
}

function ClientCell({ log, onInvestigate }) {
  return (
    <div className="client-cell">
      <button className="client-name" onClick={() => onInvestigate(log.client_mac)}>
        {log.client_mac || '—'}
      </button>
      {log.alias && <span className="client-alias">{log.alias}</span>}
      {log.client_label && log.client_label !== log.client_mac && !log.alias &&
        <span className="client-hostname">{log.client_label}</span>
      }
      {log.username && log.username !== log.client_mac?.replace(/:/g,'').toLowerCase() &&
       log.username !== log.guest_name &&
        <span className="client-username">{log.username}</span>
      }
      {log.is_guest && <span className="badge badge-guest">GUEST</span>}
    </div>
  )
}

function IpCell({ log }) {
  return (
    <div className="dest-cell">
      <span className="dest-host">{log.dst_ip || '—'}</span>
      {log.dst_port && <span className="dest-port">:{log.dst_port}</span>}
    </div>
  )
}

function DnsCell({ log }) {
  const resolved = log.dst_hostname && log.dst_hostname !== log.dst_ip
  return (
    <span className={resolved ? 'dns-resolved' : 'dns-pending'}>
      {resolved ? log.dst_hostname : '—'}
    </span>
  )
}

export default function LogTable({ logs, onInvestigate }) {
  const navigate = useNavigate()
  const [widths, setWidths] = useState(() =>
    COLUMNS.reduce((acc, c) => ({ ...acc, [c.key]: c.default }), {})
  )
  const resizingRef = useRef(null)

  const startResize = useCallback((key, e) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = widths[key]
    resizingRef.current = { key, startX, startWidth }

    const onMove = (ev) => {
      if (!resizingRef.current) return
      const delta = ev.clientX - resizingRef.current.startX
      const newWidth = Math.max(50, resizingRef.current.startWidth + delta)
      setWidths(w => ({ ...w, [resizingRef.current.key]: newWidth }))
    }
    const onUp = () => {
      resizingRef.current = null
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [widths])

  const resetWidths = () => {
    setWidths(COLUMNS.reduce((acc, c) => ({ ...acc, [c.key]: c.default }), {}))
  }

  if (!logs || logs.length === 0) {
    return <div className="table-empty">No logs found for this query.</div>
  }

  const handleTimestamp = (log) => {
    const params = new URLSearchParams({
      ts:       log.timestamp || '',
      src_ip:   log.src_ip   || '',
      dst_ip:   log.dst_ip   || '',
      dst_host: log.dst_hostname || log.dst_ip || '',
      dst_port: log.dst_port || '',
      proto:    log.proto    || '',
      ap:       log.ap_name  || '',
      venue:    log.venue    || '',
      ssid:     log.ssid     || '',
      id:       log.id       || '',
      platform: log.platform || '',
    })
    navigate(`/investigate/${log.client_mac}/event?${params.toString()}`)
  }

  return (
    <div className="table-wrapper">
      <table className="log-table" style={{ tableLayout: 'fixed', width: '100%' }}>
        <colgroup>
          {COLUMNS.map(c => <col key={c.key} style={{ width: widths[c.key] }} />)}
        </colgroup>
        <thead>
          <tr>
            {COLUMNS.map(c => (
              <th key={c.key} style={{ position: 'relative', overflow: 'hidden' }}>
                {c.label}
                <span
                  className="col-resizer"
                  onMouseDown={(e) => startResize(c.key, e)}
                  onDoubleClick={resetWidths}
                  title="Drag to resize, double-click to reset all"
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {logs.map((log, i) => (
            <tr key={log.id || i} className={log.is_guest ? 'row-guest' : ''}>
              <td className="ts-cell">
                <button className="ts-btn" onClick={() => handleTimestamp(log)} title="Investigate this event">
                  {formatTs(log.timestamp)}
                </button>
              </td>
              <td>
                <ClientCell log={log} onInvestigate={onInvestigate} />
              </td>
              <td className="mono">{log.src_ip || '—'}</td>
              <td><IpCell log={log} /></td>
              <td><DnsCell log={log} /></td>
              <td>
                {log.proto && (
                  <span className={`badge ${PROTO_CLASS[log.proto] || ''}`}>
                    {log.proto}
                  </span>
                )}
              </td>
              <td className="dim">{log.ap_name || '—'}</td>
              <td className="dim">{log.venue || '—'}</td>
              <td className="dim">{log.ssid || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
