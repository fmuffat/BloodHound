import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../utils/api'
import SearchFilters from '../components/SearchFilters'
import LogTable from '../components/LogTable'
import './SearchPage.css'

const PAGE_SIZE = 100

const now = new Date()
const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000)

const DEFAULT_FILTERS = {
  query: '*',
  from_dt: yesterday.toISOString(),
  to_dt: now.toISOString(),
  dst_hostname: '',
  client_label: '',
  client_mac: '',
  ap_name: '',
  venue: '',
  ssid: '',
  proto: '',
  dst_port: '',
  is_guest: null,
}

export default function SearchPage() {
  const [filters, setFilters]   = useState(DEFAULT_FILTERS)
  const [results, setResults]   = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  const [page, setPage]         = useState(0)
  const navigate = useNavigate()

  const doSearch = async (f, pageNum) => {
    setLoading(true)
    setError(null)
    try {
      const payload = {
        query:        f.query || '*',
        from_dt:      f.from_dt || null,
        to_dt:        f.to_dt || null,
        dst_hostname: f.dst_hostname || null,
        client_label: f.client_label || null,
        client_mac:   f.client_mac   || null,
        ap_name:      f.ap_name      || null,
        venue:        f.venue        || null,
        ssid:         f.ssid         || null,
        proto:        f.proto        || null,
        dst_port:     f.dst_port ? parseInt(f.dst_port) : null,
        is_guest:     f.is_guest,
        limit:  PAGE_SIZE,
        offset: pageNum * PAGE_SIZE,
      }
      const data = await api.search(payload)
      setResults(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = useCallback(async (f = filters) => {
    setPage(0)
    await doSearch(f, 0)
  }, [filters])

  const handlePageChange = async (newPage) => {
    setPage(newPage)
    await doSearch(filters, newPage)
    // Scroll table to top
    window.scrollTo(0, 0)
  }

  const handleExport = async () => {
    try {
      const payload = {
        query:        filters.query || '*',
        from_dt:      filters.from_dt || null,
        to_dt:        filters.to_dt   || null,
        dst_hostname: filters.dst_hostname || null,
        client_label: filters.client_label || null,
        client_mac:   filters.client_mac   || null,
        ap_name:      filters.ap_name      || null,
        venue:        filters.venue        || null,
        ssid:         filters.ssid         || null,
        proto:        filters.proto        || null,
        dst_port:     filters.dst_port ? parseInt(filters.dst_port) : null,
        is_guest:     filters.is_guest,
        limit: 500,
      }
      await api.exportInvestigation(payload)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleInvestigate = (mac) => {
    navigate(`/investigate/${mac}`)
  }

  const totalPages = results ? Math.ceil(results.total / PAGE_SIZE) : 0
  const from = page * PAGE_SIZE + 1
  const to   = results ? Math.min((page + 1) * PAGE_SIZE, results.total) : 0

  return (
    <div className="search-page">
      <div className="search-header">
        <div className="search-title">
          <span className="search-title-label">FLOW LOGS</span>
          {results && (
            <span className="search-count">
              {from}–{to} / {results.total.toLocaleString()} results
            </span>
          )}
        </div>
        <div className="search-actions">
          {results && results.returned > 0 && (
            <button className="btn-ghost" onClick={handleExport}>
              {'\u2193'} Export CSV
            </button>
          )}
        </div>
      </div>

      <SearchFilters
        filters={filters}
        onChange={setFilters}
        onSearch={handleSearch}
        loading={loading}
      />

      <div className="search-results">
        {error && <div className="error-banner">⚠ {error}</div>}
        {loading && <div className="loading-state">Searching...</div>}
        {!loading && results && (
          <>
            <LogTable
              logs={results.logs}
              onInvestigate={handleInvestigate}
            />
            {totalPages > 1 && (
              <div className="pagination">
                <button
                  className="btn-ghost page-btn"
                  onClick={() => handlePageChange(page - 1)}
                  disabled={page === 0}
                >
                  {'\u2190'} Previous
                </button>
                <span className="page-info">
                  Page {page + 1} / {totalPages}
                </span>
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
        {!loading && !results && !error && (
          <div className="empty-state">
            <div className="empty-icon">⌕</div>
            <div className="empty-text">Run a search to see flow logs</div>
          </div>
        )}
      </div>
    </div>
  )
}
