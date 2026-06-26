import { useState, useRef, useEffect } from 'react'
import { usePrefs } from '../utils/PrefsContext'
import './DateRangePicker.css'

const MONTHS = ['January','February','March','April','May','June',
                 'July','August','September','October','November','December']
const DAYS   = ['Su','Mo','Tu','We','Th','Fr','Sa']

const QUICK = [
  { label: 'Last 15 min', minutes: 15 },
  { label: 'Last 1h',     minutes: 60 },
  { label: 'Last 6h',     minutes: 360 },
  { label: 'Last 24h',    minutes: 1440 },
  { label: 'Last 7d',     minutes: 10080 },
]

function pad(n) { return String(n).padStart(2, '0') }

function TimeInput({ value, onChange }) {
  const parts = (value || '00:00').split(':')
  const h = parseInt(parts[0], 10) || 0
  const m = parseInt(parts[1], 10) || 0

  const hours   = Array.from({length: 24}, (_, i) => i)
  const minutes = Array.from({length: 60}, (_, i) => i)

  return (
    <div className="drp-time-inputs">
      <select
        className="drp-time-sel"
        value={h}
        onChange={e => onChange(pad(parseInt(e.target.value, 10)) + ":" + pad(m))}
      >
        {hours.map(i => <option key={i} value={i}>{pad(i)}</option>)}
      </select>
      <span className="drp-time-sep">:</span>
      <select
        className="drp-time-sel"
        value={m}
        onChange={e => onChange(pad(h) + ":" + pad(parseInt(e.target.value, 10)))}
      >
        {minutes.map(i => <option key={i} value={i}>{pad(i)}</option>)}
      </select>
    </div>
  )
}

function toISO(year, month, day, timeStr) {
  const parts = (timeStr || '00:00').split(':')
  const h = parseInt(parts[0]) || 0
  const m = parseInt(parts[1]) || 0
  return `${year}-${pad(month+1)}-${pad(day)}T${pad(h)}:${pad(m)}:00Z`
}

function getDaysInMonth(year, month) { return new Date(year, month + 1, 0).getDate() }
function getFirstDay(year, month)    { return new Date(year, month, 1).getDay() }

export default function DateRangePicker({ from, to, onChange }) {
  const prefs = usePrefs()
  const clock = prefs?.clock || '24h'

  const formatTime = (hh, mm) => {
    const h = parseInt(hh, 10)
    const m = parseInt(mm, 10)
    if (clock === '12h') {
      const period = h >= 12 ? 'PM' : 'AM'
      const h12 = h % 12 || 12
      return `${pad(h12)}:${pad(m)} ${period}`
    }
    return `${pad(h)}:${pad(m)}`
  }

  const formatDisplay = (from, to) => {
    if (!from && !to) return 'All time'
    const fmt = iso => {
      if (!iso) return ''
      const [datePart, timePart] = iso.split('T')
      const [year, month, day] = datePart.split('-')
      const [hh, mm] = timePart.split(':')
      return `${day}/${month}/${year} ${formatTime(hh, mm)}`
    }
    if (from && to) return `${fmt(from)} → ${fmt(to)}`
    if (from) return `From ${fmt(from)}`
    return `Until ${fmt(to)}`
  }
  const [open, setOpen]           = useState(false)
  const [viewYear, setViewYear]   = useState(() => new Date().getFullYear())
  const [viewMonth, setViewMonth] = useState(() => new Date().getMonth())
  const [step, setStep]           = useState('from')
  const [hovered, setHovered]     = useState(null)
  const [fromDay, setFromDay]     = useState(null)
  const [toDay, setToDay]         = useState(null)
  const [fromTime, setFromTime]   = useState('00:00')
  const [toTime, setToTime]       = useState('23:59')
  const ref = useRef()

  useEffect(() => {
    const handler = e => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const openPicker = () => {
    if (!open) {
      setFromDay(null)
      setToDay(null)
      setStep('from')
      if (from) {
        // Parse ISO directly to avoid timezone conversion
        const t = from.split('T')[1] || '00:00'
        setFromTime(t.slice(0,5))
      } else {
        setFromTime('00:00')
      }
      if (to) {
        const t = to.split('T')[1] || '23:59'
        setToTime(t.slice(0,5))
      } else {
        setToTime('23:59')
      }
    }
    setOpen(o => !o)
  }

  const handleQuick = (minutes) => {
    const now  = new Date()
    const past = new Date(now.getTime() - minutes * 60000)
    // Build ISO string without timezone conversion
    const fmt = d => {
      const Y = d.getUTCFullYear(), M = d.getUTCMonth()+1, D = d.getUTCDate()
      const h = d.getUTCHours(), m = d.getUTCMinutes()
      return `${Y}-${pad(M)}-${pad(D)}T${pad(h)}:${pad(m)}:00Z`
    }
    onChange(fmt(past), fmt(now))
    setOpen(false)
  }

  const handleDayClick = (day) => {
    if (step === 'from') {
      setFromDay({ year: viewYear, month: viewMonth, day })
      setToDay(null)
      setStep('to')
    } else if (step === 'to') {
      const fd = new Date(fromDay.year, fromDay.month, fromDay.day)
      const td = new Date(viewYear, viewMonth, day)
      if (td < fd) {
        setToDay(fromDay)
        setFromDay({ year: viewYear, month: viewMonth, day })
      } else {
        setToDay({ year: viewYear, month: viewMonth, day })
      }
      setStep('done')
    } else {
      setFromDay({ year: viewYear, month: viewMonth, day })
      setToDay(null)
      setStep('to')
    }
  }

  const handleApply = () => {
    if (!fromDay || !toDay) return
    onChange(
      toISO(fromDay.year, fromDay.month, fromDay.day, fromTime),
      toISO(toDay.year,   toDay.month,   toDay.day,   toTime)
    )
    setOpen(false)
  }

  const handleClear = () => {
    onChange(null, null)
    setFromDay(null)
    setToDay(null)
    setStep('from')
    setOpen(false)
  }

  const sameDay = (obj, y, m, d) => obj && obj.year === y && obj.month === m && obj.day === d
  const isFrom    = d => sameDay(fromDay, viewYear, viewMonth, d)
  const isTo      = d => sameDay(toDay,   viewYear, viewMonth, d)
  const isToday   = d => {
    const n = new Date()
    return n.getFullYear() === viewYear && n.getMonth() === viewMonth && n.getDate() === d
  }
  const isInRange = d => {
    const cur  = new Date(viewYear, viewMonth, d)
    const fD   = fromDay ? new Date(fromDay.year, fromDay.month, fromDay.day) : null
    const tD   = toDay
      ? new Date(toDay.year, toDay.month, toDay.day)
      : (hovered && step === 'to' ? new Date(viewYear, viewMonth, hovered) : null)
    if (!fD || !tD) return false
    const lo = fD < tD ? fD : tD
    const hi = fD < tD ? tD : fD
    return cur > lo && cur < hi
  }

  const prevMonth = () => {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(y => y - 1) }
    else setViewMonth(m => m - 1)
  }
  const nextMonth = () => {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(y => y + 1) }
    else setViewMonth(m => m + 1)
  }

  const daysInMonth = getDaysInMonth(viewYear, viewMonth)
  const firstDay    = getFirstDay(viewYear, viewMonth)
  const cells = [
    ...Array(firstDay).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1)
  ]

  const canApply = fromDay && toDay

  return (
    <div className="drp" ref={ref}>
      <button
        className={`drp-trigger ${open ? 'active' : ''} ${(from || to) ? 'has-value' : ''}`}
        onClick={openPicker}
      >
        <span className="drp-icon">📅</span>
        <span className="drp-label">{formatDisplay(from, to)}</span>
        {(from || to) && (
          <span className="drp-clear" onClick={e => { e.stopPropagation(); handleClear() }}>✕</span>
        )}
      </button>

      {open && (
        <div className="drp-popup">
          <div className="drp-quick">
            {QUICK.map(q => (
              <button key={q.label} className="drp-quick-btn" onClick={() => handleQuick(q.minutes)}>
                {q.label}
              </button>
            ))}
          </div>

          <div className="drp-divider" />

          <div className="drp-step-hint">
            {step === 'from' && <span className="drp-step active">① Select start date</span>}
            {step === 'to'   && <span className="drp-step active">② Select end date</span>}
            {step === 'done' && canApply && (
              <span className="drp-step done">
                {pad(fromDay.day)}/{pad(fromDay.month+1)}/{fromDay.year} {fromTime}
                {' \u2192 '}
                {pad(toDay.day)}/{pad(toDay.month+1)}/{toDay.year} {toTime}
              </span>
            )}
          </div>

          <div className="drp-calendar">
            <div className="drp-nav">
              <button className="drp-nav-btn" onClick={prevMonth}>‹</button>
              <span className="drp-month-year">{MONTHS[viewMonth]} {viewYear}</span>
              <button className="drp-nav-btn" onClick={nextMonth}>›</button>
            </div>
            <div className="drp-days-header">
              {DAYS.map(d => <span key={d} className="drp-day-name">{d}</span>)}
            </div>
            <div className="drp-grid">
              {cells.map((day, i) => day
                ? (
                  <button
                    key={i}
                    className={[
                      'drp-day',
                      isFrom(day)    ? 'is-from'  : '',
                      isTo(day)      ? 'is-to'    : '',
                      isInRange(day) ? 'in-range' : '',
                      isToday(day)   ? 'is-today' : '',
                    ].filter(Boolean).join(' ')}
                    onClick={() => handleDayClick(day)}
                    onMouseEnter={() => step === 'to' && setHovered(day)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    {day}
                  </button>
                )
                : <span key={i} className="drp-day-empty" />
              )}
            </div>
          </div>

          <div className="drp-divider" />

          <div className="drp-times">
            <div className="drp-time-group">
              <label>From (HH : MM)</label>
              <TimeInput value={fromTime} onChange={setFromTime} />
              {fromDay && (
                <div className="drp-time-preview">
                  {pad(fromDay.day)}/{pad(fromDay.month+1)}/{fromDay.year} {fromTime}
                </div>
              )}
            </div>
            <div className="drp-time-group">
              <label>To (HH : MM)</label>
              <TimeInput value={toTime} onChange={setToTime} />
              {toDay && (
                <div className="drp-time-preview">
                  {pad(toDay.day)}/{pad(toDay.month+1)}/{toDay.year} {toTime}
                </div>
              )}
            </div>
          </div>

          <div className="drp-actions">
            <button className="drp-btn-clear" onClick={handleClear}>Clear</button>
            <button className="drp-btn-apply" onClick={handleApply} disabled={!canApply}>
              Apply
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
