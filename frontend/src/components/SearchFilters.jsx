import { useEffect, useState } from 'react'
import DateRangePicker from './DateRangePicker'
import { api } from '../utils/api'
import './SearchFilters.css'

export default function SearchFilters({ filters, onChange, onSearch, loading }) {
  const [options, setOptions] = useState({ aps: [], venues: [], ssids: [] })

  const loadOptions = () => {
    api.getOptions().then(setOptions).catch(() => {})
  }

  useEffect(() => {
    loadOptions()
    // Refresh options every 5 minutes
    const t = setInterval(loadOptions, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [])

  const set = (k, v) => onChange(f => ({ ...f, [k]: v }))

  const handleKey = (e) => {
    if (e.key === 'Enter') onSearch()
  }

  const handleReset = () => {
    onChange({
      query: '*', from_dt: null, to_dt: null,
      dst_hostname: '', client_label: '', client_mac: '',
      ap_name: '', venue: '', ssid: '', proto: '', dst_port: '', is_guest: null,
    })
  }

  return (
    <div className="filters">
      {/* Main query bar */}
      <div className="filter-bar">
        <input
          className="query-input"
          value={filters.query}
          onChange={e => set('query', e.target.value)}
          onKeyDown={handleKey}
          placeholder="Lucene query — e.g. dst_port:443 or * for all"
        />
        <DateRangePicker
          from={filters.from_dt}
          to={filters.to_dt}
          onChange={(from, to) => onChange(f => ({ ...f, from_dt: from, to_dt: to }))}
        />
        <button className="btn-primary" onClick={() => onSearch()} disabled={loading}>
          {loading ? '...' : 'Search'}
        </button>
        <button className="btn-ghost" onClick={handleReset}>Reset</button>
      </div>

      {/* Structured filters */}
      <div className="filter-grid">
        <div className="filter-group">
          <label>Client</label>
          <input
            value={filters.client_label}
            onChange={e => set('client_label', e.target.value)}
            onKeyDown={handleKey}
            placeholder="name, alias, hostname..."
          />
        </div>
        <div className="filter-group">
          <label>MAC Address</label>
          <input
            value={filters.client_mac}
            onChange={e => set('client_mac', e.target.value)}
            onKeyDown={handleKey}
            placeholder="aa:bb:cc:dd:ee:ff"
          />
        </div>
        <div className="filter-group">
          <label>Destination</label>
          <input
            value={filters.dst_hostname}
            onChange={e => set('dst_hostname', e.target.value)}
            onKeyDown={handleKey}
            placeholder="yahoo.fr, 8.8.8.8..."
          />
        </div>
        <div className="filter-group">
          <label>Dst Port</label>
          <input
            type="number"
            value={filters.dst_port}
            onChange={e => set('dst_port', e.target.value)}
            onKeyDown={handleKey}
            placeholder="443, 80, 53..."
          />
        </div>
        <div className="filter-group">
          <label>Protocol</label>
          <select value={filters.proto} onChange={e => set('proto', e.target.value)}>
            <option value="">All</option>
            <option value="TCP">TCP</option>
            <option value="UDP">UDP</option>
            <option value="ICMP">ICMP</option>
          </select>
        </div>
        <div className="filter-group">
          <label>AP</label>
          <select value={filters.ap_name} onChange={e => set('ap_name', e.target.value)}>
            <option value="">All APs</option>
            {options.aps.map(ap => (
              <option key={ap} value={ap}>{ap}</option>
            ))}
          </select>
        </div>
        <div className="filter-group">
          <label>Venue</label>
          <select value={filters.venue} onChange={e => set('venue', e.target.value)}>
            <option value="">All Venues</option>
            {options.venues.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
        <div className="filter-group">
          <label>SSID</label>
          <select value={filters.ssid} onChange={e => set('ssid', e.target.value)}>
            <option value="">All SSIDs</option>
            {options.ssids.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="filter-group">
          <label>Client type</label>
          <select
            value={filters.is_guest === null ? '' : String(filters.is_guest)}
            onChange={e => set('is_guest', e.target.value === '' ? null : e.target.value === 'true')}
          >
            <option value="">All</option>
            <option value="true">Guest only</option>
            <option value="false">Non-guest only</option>
          </select>
        </div>
      </div>
    </div>
  )
}
