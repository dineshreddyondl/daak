import React, { useEffect, useRef, useState } from 'react'

const HUB_COLORS = ['#dc2626', '#9333ea', '#0891b2', '#ca8a04', '#15803d', '#be185d']

const API_BASE = import.meta.env.VITE_API_URL || ''

async function api(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  if (res.headers.get('content-type')?.includes('application/json')) {
    return res.json()
  }
  return res
}

function computeBounds(pincodes) {
  if (!pincodes.length) return null
  let minLat = Infinity, maxLat = -Infinity
  let minLng = Infinity, maxLng = -Infinity
  for (const p of pincodes) {
    if (p.centroid_lat == null || p.centroid_lng == null) continue
    if (p.centroid_lat < minLat) minLat = p.centroid_lat
    if (p.centroid_lat > maxLat) maxLat = p.centroid_lat
    if (p.centroid_lng < minLng) minLng = p.centroid_lng
    if (p.centroid_lng > maxLng) maxLng = p.centroid_lng
  }
  if (!isFinite(minLat)) return null
  return { sw: { lat: minLat, lng: minLng }, ne: { lat: maxLat, lng: maxLng } }
}

function formatTimeAgo(iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  const diff = (Date.now() - t) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}


// ============================================================================
// BUILDER VIEW
// ============================================================================

function BuilderView({ openServiceability, prefilled, onPrefilledHandled }) {
  const [states, setStates] = useState([])
  const [districts, setDistricts] = useState([])
  const [subDistricts, setSubDistricts] = useState([])
  const [villages, setVillages] = useState([])

  const [state, setState] = useState('')
  const [district, setDistrict] = useState('')
  const [subDistrict, setSubDistrict] = useState('')
  const [village, setVillage] = useState('')

  const [stats, setStats] = useState(null)
  const [pincodes, setPincodes] = useState([])
  const [hubs, setHubs] = useState([])

  const [mode, setMode] = useState('area')
  const [hubName, setHubName] = useState('')
  const [hubLat, setHubLat] = useState('')
  const [hubLng, setHubLng] = useState('')
  const [radius, setRadius] = useState(25)
  const [status, setStatus] = useState('Pick a district to begin')
  const [statusType, setStatusType] = useState('')
  const [zoomLevel, setZoomLevel] = useState(5)
  const [draftSaved, setDraftSaved] = useState(false)

  const mapRef = useRef(null)
  const mapInstance = useRef(null)
  const polygonLayers = useRef([])
  const labelLayers = useRef([])
  const hubLayers = useRef({})
  const pendingMarker = useRef(null)
  const previewCircle = useRef(null)
  const autocompleteRef = useRef(null)
  const searchInputRef = useRef(null)
  const districtBoundsRef = useRef(null)
  const centroidMarker = useRef(null)

  // Pre-select state/district/sub_district/village from Search or Serviceability
  useEffect(() => {
    if (prefilled?.state) setState(prefilled.state)
    if (prefilled?.district) setDistrict(prefilled.district)
    if (prefilled?.sub_district) {
      // Defer slightly so the sub_districts list has loaded
      setTimeout(() => setSubDistrict(prefilled.sub_district), 100)
    }
    if (prefilled?.village) {
      setMode('area')
      setTimeout(() => setVillage(prefilled.village), 200)
    }
    if (prefilled) onPrefilledHandled?.()
  }, [prefilled])

  useEffect(() => {
    api('/api/states').then(setStates).catch(e => setStatusErr(e.message))
  }, [])

  useEffect(() => {
    if (!state) { setDistricts([]); return }
    api(`/api/districts?state=${encodeURIComponent(state)}`)
      .then(setDistricts).catch(e => setStatusErr(e.message))
  }, [state])

  useEffect(() => {
    if (!state || !district) {
      setStats(null); setPincodes([]); setHubs([])
      districtBoundsRef.current = null
      return
    }
    Promise.all([
      api(`/api/district/stats?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}`),
      api(`/api/district/pincodes?district=${encodeURIComponent(district)}`),
      api(`/api/district/hubs?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}`),
    ]).then(([s, pc, h]) => {
      setStats(s)
      setPincodes(pc)
      setHubs(h.map(hub => ({
        id: hub.id, name: hub.hub_name,
        lat: hub.hub_lat, lng: hub.hub_lng,
        radius_km: hub.radius_km,
        pincodes: hub.pincodes || [],
      })))
      districtBoundsRef.current = computeBounds(pc)
      setStatusOk(`Loaded ${pc.length} pincodes for ${district}.`)
      fitMapToBounds(pc)
    }).catch(e => setStatusErr(e.message))
  }, [state, district])

  // Fetch the district HQ centroid (geocoded once, stored in DB) and place a
  // permanent marker on the Builder map. Independent of the pincode load.
  useEffect(() => {
    // Clear any existing centroid marker first
    if (centroidMarker.current) {
      centroidMarker.current.setMap(null)
      centroidMarker.current = null
    }
    if (!state || !district || !mapInstance.current) return

    let cancelled = false
    api(`/api/district_centroid?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}`)
      .then(c => {
        if (cancelled || !mapInstance.current) return
        if (centroidMarker.current) {
          centroidMarker.current.setMap(null)
          centroidMarker.current = null
        }
        centroidMarker.current = new window.google.maps.Marker({
          position: { lat: c.lat, lng: c.lng },
          map: mapInstance.current,
          icon: {
            path: 'M 0,-9 2.6,-2.8 9.5,-2.8 4,1.1 6.1,7.5 0,3.6 -6.1,7.5 -4,1.1 -9.5,-2.8 -2.6,-2.8 z',
            fillColor: '#7c3aed',
            fillOpacity: 1,
            strokeColor: '#ffffff',
            strokeWeight: 1.5,
            scale: 1.2,
          },
          title: `District HQ: ${c.district}, ${c.state}\n${c.formatted_address || ''}`,
          zIndex: 3000,
        })
      })
      .catch(() => { /* 404 means no centroid stored — silent */ })

    return () => { cancelled = true }
  }, [state, district])

  useEffect(() => {
    if (!state || !district) { setSubDistricts([]); return }
    api(`/api/sub_districts?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}`)
      .then(setSubDistricts).catch(e => setStatusErr(e.message))
  }, [state, district])

  useEffect(() => {
    if (!state || !district || !subDistrict) { setVillages([]); return }
    api(`/api/villages?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}&sub_district=${encodeURIComponent(subDistrict)}`)
      .then(setVillages).catch(e => setStatusErr(e.message))
  }, [state, district, subDistrict])

  useEffect(() => {
    if (mapInstance.current) return
    mapInstance.current = new window.google.maps.Map(mapRef.current, {
      center: { lat: 20.5, lng: 78.9 }, zoom: 5,
      mapTypeControl: true, streetViewControl: false, fullscreenControl: true,
    })
    mapInstance.current.addListener('click', e => {
      if (!district) {
        setStatusErr('Select a district first')
        return
      }
      fillFromCoords(e.latLng.lat(), e.latLng.lng())
    })
    mapInstance.current.addListener('zoom_changed', () => {
      setZoomLevel(mapInstance.current.getZoom())
    })
  }, [])

  useEffect(() => {
    if (!window.google?.maps?.places || !searchInputRef.current || mode !== 'search') return

    if (autocompleteRef.current) {
      window.google.maps.event.clearInstanceListeners(autocompleteRef.current)
      autocompleteRef.current = null
    }

    const options = {
      fields: ['geometry', 'name', 'formatted_address'],
      componentRestrictions: { country: 'in' },
    }
    const bb = districtBoundsRef.current
    if (bb) {
      const bounds = new window.google.maps.LatLngBounds(
        { lat: bb.sw.lat, lng: bb.sw.lng },
        { lat: bb.ne.lat, lng: bb.ne.lng },
      )
      options.bounds = bounds
      options.strictBounds = true
    }

    const ac = new window.google.maps.places.Autocomplete(searchInputRef.current, options)
    ac.addListener('place_changed', () => {
      const place = ac.getPlace()
      if (!place || !place.geometry) {
        setStatusErr(bb
          ? `No location matching "${searchInputRef.current.value}" in ${district}.`
          : 'No location found.')
        return
      }
      const loc = place.geometry.location
      fillFromCoords(loc.lat(), loc.lng(), place.name || place.formatted_address)
      mapInstance.current.panTo(loc)
      mapInstance.current.setZoom(14)
    })

    autocompleteRef.current = ac
  }, [district, mode, pincodes])

  function getCoverageMap() {
    const m = new Map()
    hubs.forEach(h => {
      h.pincodes.forEach(p => {
        if (!m.has(p.pincode)) {
          m.set(p.pincode, {
            id: p.id, is_excluded: p.is_excluded,
            hub_id: h.id, distance_km: p.distance_km,
          })
        }
      })
    })
    return m
  }

  async function toggleExclude(pincode) {
    const covMap = getCoverageMap()
    const cov = covMap.get(pincode)
    if (!cov) return
    const newExcluded = !cov.is_excluded
    try {
      await api(`/api/coverage/${cov.id}/toggle`, {
        method: 'POST',
        body: JSON.stringify({ is_excluded: newExcluded }),
      })
      setHubs(prev => prev.map(h => ({
        ...h,
        pincodes: h.pincodes.map(p =>
          p.pincode === pincode ? { ...p, is_excluded: newExcluded } : p
        ),
      })))
      setStatusOk(newExcluded ? `Excluded ${pincode}` : `Re-included ${pincode}`)
    } catch (e) {
      setStatusErr(`Toggle failed: ${e.message}`)
    }
  }

  useEffect(() => {
    if (!mapInstance.current) return

    polygonLayers.current.forEach(l => l.setMap(null))
    polygonLayers.current = []
    labelLayers.current.forEach(l => l.setMap(null))
    labelLayers.current = []

    const covMap = getCoverageMap()

    if (district) {
      pincodes.forEach(p => {
        const cov = covMap.get(p.pincode)
        const isCovered = cov && !cov.is_excluded
        const isExcluded = cov && cov.is_excluded

        let strokeColor, fillColor, fillOpacity, strokeOpacity, weight = 1
        if (isCovered) {
          strokeColor = '#1e40af'; fillColor = '#10b981'; fillOpacity = 0.35; strokeOpacity = 0.9
        } else if (isExcluded) {
          strokeColor = '#dc2626'; fillColor = '#9ca3af'; fillOpacity = 0.35; strokeOpacity = 0.9; weight = 2
        } else {
          strokeColor = '#1e40af'; fillColor = '#3b82f6'; fillOpacity = 0.15; strokeOpacity = 0.9
        }

        const boundary = p.boundary_geojson
        if (boundary && boundary.type === 'Polygon') {
          const paths = boundary.coordinates[0].map(([lng, lat]) => ({ lat, lng }))
          const poly = new window.google.maps.Polygon({
            paths, strokeColor, strokeWeight: weight, strokeOpacity, fillColor, fillOpacity,
            map: mapInstance.current, clickable: !!cov,
          })
          if (cov) {
            poly.addListener('click', e => {
              const buttonText = isExcluded ? 'Re-include in coverage' : 'Exclude from coverage'
              const buttonColor = isExcluded ? '#059669' : '#dc2626'
              const stateLabel = isExcluded
                ? `<span style="color:#dc2626;font-weight:700;">EXCLUDED</span>`
                : `<span style="color:#059669;font-weight:700;">COVERED</span>`
              const html = `
                <div style="font-family:inherit;padding:4px;">
                  <div style="font-size:14px;font-weight:700;">${p.pincode}</div>
                  <div style="color:#6b7280;font-size:12px;">${p.city || '—'}</div>
                  <div style="font-size:11px;margin:6px 0;">
                    ${cov.distance_km} km from hub • ${stateLabel}
                  </div>
                  <button id="toggle-${p.pincode}" style="
                    padding:6px 10px;background:${buttonColor};color:white;
                    border:none;border-radius:4px;cursor:pointer;font-size:12px;
                    font-weight:600;width:100%;">${buttonText}</button>
                </div>
              `
              const info = new window.google.maps.InfoWindow({ content: html })
              info.setPosition(e.latLng)
              info.open(mapInstance.current)
              window.google.maps.event.addListenerOnce(info, 'domready', () => {
                const btn = document.getElementById(`toggle-${p.pincode}`)
                if (btn) {
                  btn.onclick = () => { info.close(); toggleExclude(p.pincode) }
                }
              })
            })
          }
          polygonLayers.current.push(poly)
        } else if (p.centroid_lat && p.centroid_lng) {
          const dotColor = isCovered ? '#10b981' : isExcluded ? '#9ca3af' : '#3b82f6'
          const m = new window.google.maps.Marker({
            position: { lat: p.centroid_lat, lng: p.centroid_lng },
            map: mapInstance.current,
            icon: {
              path: window.google.maps.SymbolPath.CIRCLE, scale: 5,
              fillColor: dotColor, fillOpacity: 0.7,
              strokeColor: '#1e40af', strokeWeight: 1,
            },
            title: `${p.pincode} ${p.city || ''}${cov ? (isExcluded ? ' (excluded)' : ' (covered)') : ''}`,
            clickable: !!cov,
          })
          if (cov) m.addListener('click', () => toggleExclude(p.pincode))
          polygonLayers.current.push(m)
        }

        if (cov && p.centroid_lat && p.centroid_lng) {
          const labelColor = isExcluded ? '#dc2626' : '#064e3b'
          const labelText = isExcluded ? `${p.pincode} ✗` : String(p.pincode)
          const label = new window.google.maps.Marker({
            position: { lat: p.centroid_lat, lng: p.centroid_lng },
            map: mapInstance.current,
            icon: { path: 'M 0,0 0,0', strokeOpacity: 0, fillOpacity: 0, scale: 1 },
            label: { text: labelText, color: labelColor, fontSize: '11px', fontWeight: '700' },
            clickable: false, zIndex: 1000,
          })
          labelLayers.current.push(label)
        }
      })
    }
  }, [pincodes, hubs, district])

  useEffect(() => {
    if (!labelLayers.current.length) return
    const visible = zoomLevel >= 11
    labelLayers.current.forEach(l => l.setVisible(visible))
  }, [zoomLevel])

  useEffect(() => {
    if (!mapInstance.current) return
    Object.values(hubLayers.current).forEach(l => {
      l.marker.setMap(null); l.circle.setMap(null)
    })
    hubLayers.current = {}
    hubs.forEach((h, i) => {
      const color = HUB_COLORS[i % HUB_COLORS.length]
      const marker = new window.google.maps.Marker({
        position: { lat: h.lat, lng: h.lng }, map: mapInstance.current,
        label: { text: String(i + 1), color: 'white', fontSize: '12px', fontWeight: '700' },
        title: `${h.name} (${h.pincodes.length} pincodes)`, zIndex: 2000,
      })
      const circle = new window.google.maps.Circle({
        strokeColor: color, strokeWeight: 2,
        fillColor: color, fillOpacity: 0.1,
        map: mapInstance.current,
        center: { lat: h.lat, lng: h.lng }, radius: h.radius_km * 1000, clickable: false,
      })
      hubLayers.current[h.id] = { marker, circle }
    })
  }, [hubs])

  useEffect(() => {
    if (!mapInstance.current) return
    if (previewCircle.current) { previewCircle.current.setMap(null); previewCircle.current = null }
    const lat = parseFloat(hubLat), lng = parseFloat(hubLng), r = parseFloat(radius)
    if (!isNaN(lat) && !isNaN(lng) && !isNaN(r) && r > 0) {
      previewCircle.current = new window.google.maps.Circle({
        strokeColor: '#dc2626', strokeWeight: 2, strokeOpacity: 0.9,
        fillColor: '#dc2626', fillOpacity: 0.08,
        map: mapInstance.current, center: { lat, lng }, radius: r * 1000, clickable: false,
      })
    }
  }, [hubLat, hubLng, radius])

  function fitMapToBounds(pcs) {
    if (!mapInstance.current || !pcs.length) return
    const bounds = new window.google.maps.LatLngBounds()
    pcs.forEach(p => {
      if (p.centroid_lat && p.centroid_lng) bounds.extend({ lat: p.centroid_lat, lng: p.centroid_lng })
    })
    if (!bounds.isEmpty()) mapInstance.current.fitBounds(bounds)
  }

  function fillFromCoords(lat, lng, name) {
    setHubLat(lat.toFixed(6))
    setHubLng(lng.toFixed(6))
    if (name && !hubName.trim()) setHubName(name)
    if (pendingMarker.current) pendingMarker.current.setMap(null)
    pendingMarker.current = new window.google.maps.Marker({
      position: { lat, lng }, map: mapInstance.current,
      icon: {
        path: window.google.maps.SymbolPath.CIRCLE, scale: 8,
        fillColor: '#dc2626', fillOpacity: 0.9, strokeColor: '#fff', strokeWeight: 2,
      },
      title: 'Pending hub',
    })
    setStatusOk(`Pending: ${lat.toFixed(4)}, ${lng.toFixed(4)} — adjust radius and click 'Add Hub'`)
  }

  async function geocodeAndFill(query, label) {
    const geocoder = new window.google.maps.Geocoder()
    geocoder.geocode({ address: query }, (results, st) => {
      if (st === 'OK' && results[0]) {
        const loc = results[0].geometry.location
        mapInstance.current.panTo(loc); mapInstance.current.setZoom(13)
        fillFromCoords(loc.lat(), loc.lng())
        setStatusOk(`${label} ${query} — lat/long auto-filled`)
      } else {
        setStatusErr(`Could not find '${query}' (${st})`)
      }
    })
  }

  useEffect(() => {
    if (mode === 'area' && subDistrict && !village) {
      geocodeAndFill([subDistrict, district, 'India'].filter(Boolean).join(', '), 'Sub-district:')
    }
  }, [subDistrict, mode])

  useEffect(() => {
    if (mode === 'area' && village) {
      geocodeAndFill([village, subDistrict, district, 'India'].filter(Boolean).join(', '), 'Village:')
    }
  }, [village, mode])

  async function addHub() {
    if (!hubLat || !hubLng) { setStatusErr('Click on map or search a place first'); return }
    const lat = parseFloat(hubLat), lng = parseFloat(hubLng)
    if (isNaN(lat) || isNaN(lng)) { setStatusErr('Invalid lat/lng'); return }
    const name = hubName.trim() || `Hub ${hubs.length + 1}`
    setStatusOk('Saving hub…')
    try {
      const res = await api(`/api/district/${encodeURIComponent(state)}/${encodeURIComponent(district)}/hubs`, {
        method: 'POST',
        body: JSON.stringify({
          name, lat, lng,
          radius_km: parseFloat(radius),
          placement_method: mode,
          sub_district: subDistrict || null,
          village: village || null,
        }),
      })
      const fresh = await api(`/api/district/hubs?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}`)
      setHubs(fresh.map(h => ({
        id: h.id, name: h.hub_name,
        lat: h.hub_lat, lng: h.hub_lng,
        radius_km: h.radius_km,
        pincodes: h.pincodes || [],
      })))
      setHubName(''); setHubLat(''); setHubLng('')
      if (searchInputRef.current) searchInputRef.current.value = ''
      if (pendingMarker.current) { pendingMarker.current.setMap(null); pendingMarker.current = null }
      if (previewCircle.current) { previewCircle.current.setMap(null); previewCircle.current = null }
      setStatusOk(`Added '${name}' — found ${res.pincodes_found} pincodes`)
    } catch (e) {
      setStatusErr(`Add failed: ${e.message}`)
    }
  }

  async function deleteHub(id) {
    try {
      await api(`/api/hub/${id}`, { method: 'DELETE' })
      setHubs(hubs.filter(h => h.id !== id))
      setStatusOk('Hub deleted')
    } catch (e) {
      setStatusErr(`Delete failed: ${e.message}`)
    }
  }

  function saveAsDraft() {
    setDraftSaved(true)
    setStatusOk(`✓ Draft saved · ${hubs.length} hubs · ${uniquePincodes.size} pincodes`)
    setTimeout(() => setDraftSaved(false), 2000)
  }

  async function finalize() {
    if (!confirm(`Finalize ${district}? After this, no more hubs can be added or removed.`)) return
    try {
      await api(`/api/district/${encodeURIComponent(state)}/${encodeURIComponent(district)}/finalize`, {
        method: 'POST',
        body: JSON.stringify({ finalized_by: 'admin' }),
      })
      setStatusOk(`Finalized ${district}`)
      const s = await api(`/api/district/stats?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}`)
      setStats(s)
    } catch (e) {
      setStatusErr(`Finalize failed: ${e.message}`)
    }
  }

  function setStatusOk(msg) { setStatus(msg); setStatusType('success') }
  function setStatusErr(msg) { setStatus(msg); setStatusType('error') }

  const totalRows = hubs.reduce((sum, h) => sum + h.pincodes.filter(p => !p.is_excluded).length, 0)
  const uniquePincodes = new Set()
  hubs.forEach(h => h.pincodes.forEach(p => { if (!p.is_excluded) uniquePincodes.add(p.pincode) }))
  const excludedCount = hubs.reduce((sum, h) => sum + h.pincodes.filter(p => p.is_excluded).length, 0)
  const isFinalized = stats?.status === 'FINALIZED'

  return (
    <>
      <div className={`status-bar ${statusType}`}>{status}</div>
      <div className="layout">
        <aside className="sidebar">
          <h2>1. District</h2>
          <label>State</label>
          <select value={state} onChange={e => { setState(e.target.value); setDistrict('') }}>
            <option value="">— Choose —</option>
            {states.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <label>District (required)</label>
          <select value={district} onChange={e => setDistrict(e.target.value)} disabled={!state}>
            <option value="">— Choose —</option>
            {districts.map(d => <option key={d} value={d}>{d}</option>)}
          </select>

          {stats && (
            <>
              <h2>2. District Stats</h2>
              <div className="stat-grid">
                <div className="stat"><div className="label">Pincodes</div><div className="value">{stats.pincodes}</div></div>
                <div className="stat"><div className="label">With polygon</div><div className="value">{stats.pincodes_with_polygon}</div></div>
                <div className="stat"><div className="label">Sub-districts</div><div className="value">{stats.sub_districts}</div></div>
                <div className="stat"><div className="label">Villages</div><div className="value">{stats.villages}</div></div>
              </div>
              {isFinalized && (
                <div className="finalized-banner">
                  ✅ Finalized — read-only. Use the Serviceability page to download.
                </div>
              )}
            </>
          )}

          {district && !isFinalized && (
            <>
              <h2>3. Position the Hub</h2>
              <div className="mode-toggle">
                <button className={mode === 'area' ? 'active' : ''} onClick={() => setMode('area')}>📍 Choose Manually (Sub-district)</button>
                <button className={mode === 'search' ? 'active' : ''} onClick={() => setMode('search')}>🔍 Use Google Maps</button>
              </div>

              {mode === 'search' ? (
                <>
                  <label>Search a place (within {district})</label>
                  <input ref={searchInputRef} type="text"
                         placeholder={`e.g., railway station in ${district}`} />
                  <div className="muted">Suggestions limited to {district} only. Or click on the map.</div>
                </>
              ) : (
                <>
                  <label>Sub-district</label>
                  <select value={subDistrict} onChange={e => { setSubDistrict(e.target.value); setVillage('') }}>
                    <option value="">— Choose —</option>
                    {subDistricts.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <label>Village (optional)</label>
                  <select value={village} onChange={e => setVillage(e.target.value)} disabled={!subDistrict}>
                    <option value="">— Choose —</option>
                    {villages.map(v => <option key={v} value={v}>{v}</option>)}
                  </select>
                  <div className="muted">Lat/long auto-filled from selection. Click elsewhere on map to override.</div>
                </>
              )}

              <h2>4. Virtual Hub Details</h2>
              <label>Hub Name</label>
              <input type="text" value={hubName} onChange={e => setHubName(e.target.value)}
                     placeholder="e.g., Center Hub" />
              <div className="row2">
                <div>
                  <label>Latitude</label>
                  <input type="number" step="0.000001" value={hubLat}
                         onChange={e => setHubLat(e.target.value)} />
                </div>
                <div>
                  <label>Longitude</label>
                  <input type="number" step="0.000001" value={hubLng}
                         onChange={e => setHubLng(e.target.value)} />
                </div>
              </div>
              <label>Radius (km) — preview shown live on map</label>
              <input type="number" step="0.5" min="1" max="200" value={radius}
                     onChange={e => setRadius(e.target.value)} />
              <button className="primary" onClick={addHub} disabled={!hubLat || !hubLng}>
                Add Hub & Find Pincodes
              </button>
            </>
          )}

          {hubs.length > 0 && (
            <>
              <h2>5. Hubs ({hubs.length})</h2>
              {hubs.map((h, i) => {
                const active = h.pincodes.filter(p => !p.is_excluded).length
                const excluded = h.pincodes.length - active
                return (
                  <div key={h.id} className="hub-card" style={{ borderLeftColor: HUB_COLORS[i % HUB_COLORS.length] }}>
                    <div className="hub-title">
                      <span>#{i + 1} {h.name}</span>
                      {!isFinalized && <button className="remove" onClick={() => deleteHub(h.id)}>remove</button>}
                    </div>
                    <div className="hub-meta">
                      {h.lat.toFixed(4)}, {h.lng.toFixed(4)} • r={h.radius_km} km •
                      <b> {active}</b> active{excluded > 0 ? ` (${excluded} excluded)` : ''}
                    </div>
                  </div>
                )
              })}
              <h2>6. Coverage Summary</h2>
              <div className="stat-grid">
                <div className="stat"><div className="label">Active rows</div><div className="value">{totalRows}</div></div>
                <div className="stat"><div className="label">Unique pincodes</div><div className="value">{uniquePincodes.size}</div></div>
              </div>
              {excludedCount > 0 && (
                <div className="muted" style={{ marginTop: '4px' }}>
                  {excludedCount} pincode{excludedCount > 1 ? 's' : ''} excluded (click to re-include).
                </div>
              )}

              {!isFinalized && (
                <>
                  <div className="autosave-indicator">
                    <span className="autosave-dot" />
                    Auto-saved · {hubs.length} hub{hubs.length === 1 ? '' : 's'} · {uniquePincodes.size} pincode{uniquePincodes.size === 1 ? '' : 's'}
                  </div>
                  <button className={draftSaved ? 'saved' : 'secondary'} onClick={saveAsDraft}>
                    {draftSaved ? '✓ Draft saved' : '💾 Save as Draft'}
                  </button>
                  <button className="warning" onClick={finalize}>
                    🔒 Finalize district
                  </button>
                </>
              )}
              {isFinalized && (
                <div className="muted" style={{ marginTop: '8px' }}>
                  This district is locked. Visit the Serviceability page to download data.
                </div>
              )}
            </>
          )}
        </aside>
        <div className="map-wrap">
          <div ref={mapRef} className="map" />
        </div>
      </div>
    </>
  )
}


// ============================================================================
// SERVICEABILITY VIEW
// ============================================================================

function ServiceabilityView({ jumpToBuilder }) {
  const [overview, setOverview] = useState([])
  const [summary, setSummary] = useState(null)
  const [filterState, setFilterState] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [search, setSearch] = useState('')
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function loadOverview() {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams()
      if (filterState) params.append('state', filterState)
      if (filterStatus) params.append('status', filterStatus)
      if (search) params.append('search', search)
      const [list, sum] = await Promise.all([
        api(`/api/serviceability/overview?${params.toString()}`),
        api('/api/serviceability/summary'),
      ])
      setOverview(list)
      setSummary(sum)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadOverview() }, [filterState, filterStatus])

  useEffect(() => {
    const t = setTimeout(() => loadOverview(), 300)
    return () => clearTimeout(t)
  }, [search])

  async function openDetail(state, district) {
    setLoading(true)
    setError('')
    try {
      const d = await api(`/api/serviceability/district?state=${encodeURIComponent(state)}&district=${encodeURIComponent(district)}`)
      setDetail({ state, district, ...d })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function downloadDistrict(district) {
    window.location.href = `${API_BASE}/api/export/district?district=${encodeURIComponent(district)}`
  }
  function downloadDrafts()    { window.location.href = `${API_BASE}/api/export/drafts` }
  function downloadFinalized() { window.location.href = `${API_BASE}/api/export/finalized` }

  async function deleteDistrict(d) {
    const isFinalized = d.status === 'FINALIZED'
    const finalizedNote = isFinalized
      ? '\n\n⚠ This district is FINALIZED. Deleting will allow it to be re-built from scratch in the Builder.'
      : ''
    const msg =
      `Delete ${d.district}, ${d.state}?\n\n` +
      `This will remove ${d.hub_count} hub${d.hub_count === 1 ? '' : 's'} and ` +
      `${(d.active_pincodes || 0) + (d.excluded_pincodes || 0)} pincode coverage row${(d.active_pincodes + d.excluded_pincodes) === 1 ? '' : 's'}.\n\n` +
      `Master data (pincodes, villages, polygons) is NOT affected.${finalizedNote}\n\n` +
      `This action cannot be undone.`

    if (!confirm(msg)) return

    try {
      const res = await api(
        `/api/district/${encodeURIComponent(d.state)}/${encodeURIComponent(d.district)}`,
        { method: 'DELETE' }
      )
      // Refresh the table
      await loadOverview()
    } catch (e) {
      setError(`Delete failed: ${e.message}`)
    }
  }

  const totalDistricts = summary?.total_districts ?? 0
  const draftDistricts = summary?.draft_districts ?? 0
  const finalDistricts = summary?.finalized_districts ?? 0
  const totalHubs      = summary?.total_hubs ?? 0
  const activeRows     = summary?.active_coverage_rows ?? 0
  const excludedRows   = summary?.excluded_coverage_rows ?? 0

  const stateOptions = Array.from(new Set(overview.map(d => d.state))).sort()

  if (detail) {
    return <ServiceabilityDetail detail={detail} onBack={() => setDetail(null)}
                                  jumpToBuilder={jumpToBuilder}
                                  onDownload={() => downloadDistrict(detail.district)} />
  }

  return (
    <main className="serv-page">
      <div className="serv-header">
        <div>
          <div className="serv-title">Serviceability Overview</div>
          <div className="serv-sub">All districts that have been worked on. DRAFT districts can still be edited from the Builder. FINALIZED districts are locked.</div>
        </div>
      </div>

      {error && <div className="serv-error">{error}</div>}

      <div className="serv-stats">
        <div className="serv-stat-card highlight">
          <div className="label">Districts</div>
          <div className="value">{totalDistricts}</div>
          <div className="delta">{draftDistricts} draft · {finalDistricts} finalized</div>
        </div>
        <div className="serv-stat-card">
          <div className="label">Total hubs</div>
          <div className="value">{totalHubs}</div>
          <div className="delta">{totalDistricts > 0 ? `avg ${(totalHubs / totalDistricts).toFixed(1)} / district` : '—'}</div>
        </div>
        <div className="serv-stat-card">
          <div className="label">Active rows</div>
          <div className="value">{activeRows}</div>
          <div className="delta">across all hubs</div>
        </div>
        <div className="serv-stat-card warn">
          <div className="label">Excluded</div>
          <div className="value">{excludedRows}</div>
          <div className="delta">manually removed by ops</div>
        </div>
      </div>

      <div className="filter-bar">
        <select value={filterState} onChange={e => setFilterState(e.target.value)}>
          <option value="">All states</option>
          {stateOptions.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="DRAFT">Draft</option>
          <option value="FINALIZED">Finalized</option>
        </select>
        <input type="text" className="search" placeholder="Search district name…"
               value={search} onChange={e => setSearch(e.target.value)} />
        <button onClick={loadOverview}>↻ Refresh</button>
      </div>

      <div className="serv-table-wrap">
        {loading ? (
          <div className="serv-empty">Loading…</div>
        ) : overview.length === 0 ? (
          <div className="serv-empty">
            <div className="ico">📭</div>
            No districts have been worked on yet. Go to the Builder to add your first hub.
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>State</th>
                <th>District</th>
                <th>Status</th>
                <th className="right">Hubs</th>
                <th className="right">Pincodes (excluded)</th>
                <th>Last updated</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {overview.map(d => (
                <tr key={`${d.state}|${d.district}`}>
                  <td>{d.state}</td>
                  <td className="district-name" onClick={() => openDetail(d.state, d.district)}>
                    {d.district}
                  </td>
                  <td>
                    <span className={`pill ${d.status === 'FINALIZED' ? 'final' : 'draft'}`}>
                      <span className="dot" />
                      {d.status === 'FINALIZED' ? 'Finalized' : 'Draft'}
                    </span>
                  </td>
                  <td className="right">{d.hub_count}</td>
                  <td className="right">
                    {d.active_pincodes}
                    {d.excluded_pincodes > 0 && (
                      <span style={{ color: '#dc2626' }}> ({d.excluded_pincodes})</span>
                    )}
                  </td>
                  <td>{formatTimeAgo(d.updated_at || d.finalized_at)}</td>
                  <td>
                    <div className="row-actions">
                      <button className="action-primary" onClick={() => openDetail(d.state, d.district)}>View</button>
                      <button className="action-export" onClick={() => downloadDistrict(d.district)}>📥</button>
                      <button className="action-delete" onClick={() => deleteDistrict(d)} title="Delete district">🗑</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="bulk-bar">
        <button className="bulk-draft" onClick={downloadDrafts} disabled={draftDistricts === 0}>
          📥 Download all DRAFT districts
          <span className="desc">{draftDistricts} district{draftDistricts === 1 ? '' : 's'} · one tab per district</span>
        </button>
        <button className="bulk-final" onClick={downloadFinalized} disabled={finalDistricts === 0}>
          📥 Download all FINALIZED districts
          <span className="desc">{finalDistricts} district{finalDistricts === 1 ? '' : 's'} · one tab per district</span>
        </button>
      </div>
    </main>
  )
}


function ServiceabilityDetail({ detail, onBack, jumpToBuilder, onDownload }) {
  const { state, district, status, hubs, coverage } = detail
  const isFinalized = status?.status === 'FINALIZED'
  const activeCount = coverage.filter(c => !c.is_excluded).length
  const excludedCount = coverage.filter(c => c.is_excluded).length

  return (
    <main className="serv-page">
      <span className="serv-back" onClick={onBack}>← Back to Serviceability</span>

      <div className="serv-detail-header">
        <div className="serv-title">{district}, {state}</div>
        <span className={`pill ${isFinalized ? 'final' : 'draft'}`}>
          <span className="dot" />
          {isFinalized ? 'Finalized' : 'Draft'}
        </span>
      </div>
      <div className="serv-sub">
        {hubs.length} hub{hubs.length === 1 ? '' : 's'} ·
        {' '}{activeCount} active pincode{activeCount === 1 ? '' : 's'}
        {excludedCount > 0 ? ` (${excludedCount} excluded)` : ''}
        {status?.finalized_at && ` · Finalized ${formatTimeAgo(status.finalized_at)} by ${status.finalized_by}`}
      </div>

      <div className="serv-detail-grid">
        <div>
          <h3>Hubs ({hubs.length})</h3>
          {hubs.map((h, i) => (
            <div key={h.id} className="serv-hub-card" style={{ borderLeftColor: HUB_COLORS[i % HUB_COLORS.length] }}>
              <div className="serv-hub-name">#{i + 1} {h.hub_name}</div>
              <div className="serv-hub-meta">
                {h.hub_lat.toFixed(4)}, {h.hub_lng.toFixed(4)} · r={h.radius_km} km
              </div>
            </div>
          ))}
        </div>
        <div>
          <h3>Coverage ({coverage.length})</h3>
          <div className="serv-coverage-table">
            <table>
              <thead>
                <tr>
                  <th>Pincode</th><th>City</th><th>Hub</th><th className="right">km</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {coverage.map(c => (
                  <tr key={c.id} className={c.is_excluded ? 'excluded-row' : ''}>
                    <td><b>{c.pincode}</b></td>
                    <td>{c.city || '—'}</td>
                    <td>{c.hub_name}</td>
                    <td className="right">{c.distance_km}</td>
                    <td>
                      {c.is_excluded
                        ? <span className="excluded-pill">Excluded</span>
                        : <span className="active-pill">Active</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="serv-detail-actions">
        {!isFinalized && (
          <button className="action-primary"
                  onClick={() => jumpToBuilder({ state, district })}>
            ↩ Continue editing in Builder
          </button>
        )}
        <button className="action-export" onClick={onDownload}>
          📥 Download this district (Excel)
        </button>
        <button className="action-secondary" onClick={onBack}>Close</button>
      </div>
    </main>
  )
}


// ============================================================================
// SEARCH VIEW
// ============================================================================

function SearchView({ jumpToBuilder }) {
  const [subTab, setSubTab] = useState('single')

  return (
    <main className="search-page">
      <div className="search-subtabs">
        <button className={`search-subtab ${subTab === 'single' ? 'active' : ''}`}
                onClick={() => setSubTab('single')}>
          Single Search
        </button>
        <button className={`search-subtab ${subTab === 'bulk' ? 'active' : ''}`}
                onClick={() => setSubTab('bulk')}>
          Bulk Lookup
        </button>
      </div>

      {subTab === 'single'
        ? <SingleSearch jumpToBuilder={jumpToBuilder} />
        : <BulkLookup />
      }
    </main>
  )
}

// ─── SingleSearch sub-view ────────────────────────────────────────────────────
function SingleSearch({ jumpToBuilder }) {
  const [activeType, setActiveType] = useState('all')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [resultsHeader, setResultsHeader] = useState('Type to search')
  const [selected, setSelected] = useState(null)
  const [loading, setLoading] = useState(false)
  const debounceRef = useRef(null)

  // Run debounced search whenever query or filter changes
  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      const q = query.trim()
      if (!q) {
        setResults([])
        setResultsHeader('Type to search')
        return
      }
      setLoading(true)
      try {
        const params = new URLSearchParams({ q, limit: '30' })
        if (activeType !== 'all') params.set('types', activeType)
        const data = await api(`/api/search?${params}`)
        setResults(data.results || [])
        setResultsHeader(`${data.count} result${data.count === 1 ? '' : 's'} for "${data.query}"`)
      } catch (e) {
        setResults([])
        setResultsHeader(`Error: ${e.message}`)
      } finally {
        setLoading(false)
      }
    }, 250)
    return () => clearTimeout(debounceRef.current)
  }, [query, activeType])

  return (
    <>
      <div className="s-search-bar">
        <div className="s-pills">
          {[
            { v: 'all', l: 'All' },
            { v: 'district', l: 'District' },
            { v: 'sub_district', l: 'Sub-district' },
            { v: 'village', l: 'Village' },
            { v: 'pincode', l: 'Pincode' },
          ].map(t => (
            <button key={t.v}
                    className={`s-pill ${activeType === t.v ? 'active' : ''}`}
                    onClick={() => setActiveType(t.v)}>
              {t.l}
            </button>
          ))}
        </div>
        <div className="s-input-wrap">
          <span className="s-icon">⌕</span>
          <input
            type="text" className="s-input"
            placeholder="Type a district, sub-district, village, or pincode…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            autoFocus
          />
          <div className={`s-loader ${loading ? 'show' : ''}`} />
        </div>
      </div>

      <div className="s-grid">
        <div className="s-results">
          <div className="s-results-header">{resultsHeader}</div>
          <div className="s-results-list">
            {results.length === 0 ? (
              <div className="s-empty">
                <div className="big">⌕</div>
                <div>{query ? 'No matches.' : 'Search across districts, sub-districts, villages, and pincodes.'}</div>
              </div>
            ) : (
              results.map((r, idx) => (
                <div key={idx}
                     className={`s-result ${selected === r ? 'selected' : ''}`}
                     onClick={() => setSelected(r)}>
                  <span className={`s-result-tag s-tag-${r.entity_type}`}>
                    {r.entity_type === 'sub_district' ? 'Sub-dist' : r.entity_type}
                  </span>
                  <div className="s-result-body">
                    <div className={`s-result-name ${r.entity_type === 'pincode' ? 'mono' : ''}`}>
                      {r.name}
                    </div>
                    <div className="s-result-path">{r.parent_path || r.state || ''}</div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="s-detail">
          {selected ? (
            <DetailPanel result={selected}
                         onSelect={setSelected}
                         jumpToBuilder={jumpToBuilder} />
          ) : (
            <div className="s-detail-empty">
              <div className="big">📍</div>
              <div>Click a search result to see details and location.</div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// ─── DetailPanel — fields, drilldown, map preview, Open in Builder ────────────
function DetailPanel({ result, onSelect, jumpToBuilder }) {
  const isPincode = result.entity_type === 'pincode'
  const cityFromMeta = result.metadata?.city || null
  const hasLatLng = result.centroid_lat != null && result.centroid_lng != null

  // Map ref + lifecycle
  const mapRef = useRef(null)
  const mapInstance = useRef(null)
  const markerRef = useRef(null)
  const polygonRef = useRef(null)

  // Per-district polygon cache so we don't refetch when clicking within same district
  const polygonCacheRef = useRef(new Map())

  useEffect(() => {
    if (!hasLatLng || !mapRef.current) return

    // Init or recenter map
    if (!mapInstance.current) {
      mapInstance.current = new window.google.maps.Map(mapRef.current, {
        center: { lat: result.centroid_lat, lng: result.centroid_lng },
        zoom: 12,
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: true,
      })
    } else {
      mapInstance.current.setCenter({ lat: result.centroid_lat, lng: result.centroid_lng })
      mapInstance.current.setZoom(12)
    }

    // Clear previous overlays
    if (markerRef.current) { markerRef.current.setMap(null); markerRef.current = null }
    if (polygonRef.current) { polygonRef.current.setMap(null); polygonRef.current = null }

    // Always place the centroid dot
    markerRef.current = new window.google.maps.Marker({
      position: { lat: result.centroid_lat, lng: result.centroid_lng },
      map: mapInstance.current,
      icon: {
        path: window.google.maps.SymbolPath.CIRCLE, scale: 8,
        fillColor: '#10b981', fillOpacity: 0.9,
        strokeColor: '#065f46', strokeWeight: 2,
      },
      title: result.name,
      zIndex: 1000,
    })

    // For pincode results, also draw the polygon (fetched lazily)
    if (isPincode && result.district) {
      let cancelled = false
      const cache = polygonCacheRef.current
      const districtKey = `${result.state}|${result.district}`

      const fetchPincodes = cache.has(districtKey)
        ? Promise.resolve(cache.get(districtKey))
        : api(`/api/district/pincodes?district=${encodeURIComponent(result.district)}`)
            .then(rows => { cache.set(districtKey, rows); return rows })

      fetchPincodes.then(rows => {
        if (cancelled) return
        const match = rows.find(r => String(r.pincode) === String(result.pincode))
        if (!match || !match.boundary_geojson) return

        const boundary = match.boundary_geojson
        if (boundary.type !== 'Polygon') return

        const paths = boundary.coordinates[0].map(([lng, lat]) => ({ lat, lng }))
        polygonRef.current = new window.google.maps.Polygon({
          paths,
          strokeColor: '#065f46',
          strokeWeight: 2,
          strokeOpacity: 0.9,
          fillColor: '#10b981',
          fillOpacity: 0.25,
          map: mapInstance.current,
          clickable: false,
          zIndex: 100,
        })

        // Fit map to polygon bounds for a clean view
        const bounds = new window.google.maps.LatLngBounds()
        paths.forEach(p => bounds.extend(p))
        mapInstance.current.fitBounds(bounds)
      }).catch(() => { /* swallow — dot is enough fallback */ })

      return () => { cancelled = true }
    }
  }, [result.centroid_lat, result.centroid_lng, result.pincode, result.district, isPincode])

  // Reset map state when leaving pincode results
  useEffect(() => {
    if (!hasLatLng) {
      mapInstance.current = null
      if (markerRef.current) { markerRef.current.setMap(null); markerRef.current = null }
      if (polygonRef.current) { polygonRef.current.setMap(null); polygonRef.current = null }
    }
  }, [hasLatLng])

  function handleOpenInBuilder() {
    const prefill = {
      state: result.state,
      district: result.district,
      sub_district: result.sub_district || undefined,
      village: result.entity_type === 'village' ? result.name : undefined,
    }
    jumpToBuilder(prefill)
  }

  return (
    <>
      <div className="s-detail-header">
        <div className="s-detail-header-text">
          <div className={`s-detail-title ${isPincode ? 'mono' : ''}`}>{result.name}</div>
          <div className="s-detail-meta">
            <span className={`s-result-tag s-tag-${result.entity_type}`} style={{ marginRight: 8 }}>
              {result.entity_type === 'sub_district' ? 'Sub-dist' : result.entity_type}
            </span>
            {result.parent_path || result.state || ''}
          </div>
        </div>
        {result.entity_type !== 'pincode' && result.state && result.district && (
          <button className="s-action-btn" onClick={handleOpenInBuilder}>
            Open in Builder →
          </button>
        )}
      </div>

      <div className="s-detail-body">
        <FieldGrid result={result} cityFromMeta={cityFromMeta} />

        {result.entity_type === 'district' && (
          <DistrictDrilldown result={result} onSelect={onSelect} />
        )}
        {result.entity_type === 'sub_district' && (
          <SubDistrictDrilldown result={result} onSelect={onSelect} />
        )}

        <div className="s-map-section">
          <div className="s-map-label">Location</div>
          {hasLatLng ? (
            <div ref={mapRef} className="s-map" />
          ) : (
            <div className="s-map-empty">
              <div>📍</div>
              <div>Map preview only available for pincode results</div>
              <div style={{ fontSize: 11, opacity: 0.7 }}>
                (districts/villages don't carry coordinates in the search index)
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function FieldGrid({ result, cityFromMeta }) {
  const fields = []
  const fmt = (v) => v ?? '—'

  if (result.entity_type === 'district') {
    fields.push(['State', fmt(result.state)], ['District', fmt(result.district)])
  } else if (result.entity_type === 'sub_district') {
    fields.push(['State', fmt(result.state)], ['District', fmt(result.district)],
                ['Sub-district', fmt(result.sub_district)])
  } else if (result.entity_type === 'village') {
    fields.push(['State', fmt(result.state)], ['District', fmt(result.district)],
                ['Sub-district', fmt(result.sub_district)], ['Village', result.name])
  } else if (result.entity_type === 'pincode') {
    fields.push(
      ['Pincode', result.pincode, 'mono'],
      ['City', fmt(cityFromMeta)],
      ['District', fmt(result.district)],
      ['State', fmt(result.state)],
      ['Centroid',
        result.centroid_lat
          ? `${result.centroid_lat.toFixed(4)}, ${result.centroid_lng.toFixed(4)}`
          : 'missing',
        'mono'],
    )
  }

  return (
    <div className="s-fields">
      {fields.map(([label, val, mod], i) => (
        <div className="s-field" key={i}>
          <div className="s-field-label">{label}</div>
          <div className={`s-field-value ${mod || ''} ${val === '—' || val === 'missing' ? 'muted' : ''}`}>
            {val}
          </div>
        </div>
      ))}
    </div>
  )
}

function DistrictDrilldown({ result, onSelect }) {
  const [data, setData] = useState(null)
  const [openSec, setOpenSec] = useState({})

  useEffect(() => {
    let cancelled = false
    setData(null)
    Promise.all([
      api(`/api/sub_districts?state=${encodeURIComponent(result.state)}&district=${encodeURIComponent(result.district)}`),
      api(`/api/villages?state=${encodeURIComponent(result.state)}&district=${encodeURIComponent(result.district)}`),
      api(`/api/district/pincodes?district=${encodeURIComponent(result.district)}`),
    ]).then(([subs, vills, pins]) => {
      if (!cancelled) setData({ subs, vills, pins })
    }).catch(() => { if (!cancelled) setData({ subs: [], vills: [], pins: [] }) })
    return () => { cancelled = true }
  }, [result.state, result.district])

  if (!data) return <div className="s-drill-empty">Loading children…</div>

  const { subs, vills, pins } = data

  function selectSub(sd) {
    onSelect({
      entity_type: 'sub_district',
      name: sd, state: result.state, district: result.district,
      sub_district: sd, pincode: null,
      centroid_lat: null, centroid_lng: null, metadata: {},
      parent_path: `${result.state} · ${result.district}`,
    })
  }

  async function selectVillageFlat(v) {
    // Look up full record via search to get sub_district
    try {
      const params = new URLSearchParams({ q: v, types: 'village', limit: '20' })
      const data = await api(`/api/search?${params}`)
      const match = (data.results || []).find(x =>
        x.name === v && x.district === result.district && x.state === result.state)
      if (match) { onSelect(match); return }
    } catch {}
    onSelect({
      entity_type: 'village',
      name: v, state: result.state, district: result.district,
      sub_district: null, pincode: null,
      centroid_lat: null, centroid_lng: null, metadata: {},
      parent_path: `${result.state} · ${result.district}`,
    })
  }

  function selectPincode(p) {
    onSelect({
      entity_type: 'pincode',
      name: String(p.pincode), state: p.state || result.state,
      district: p.district || result.district, sub_district: null,
      pincode: String(p.pincode),
      centroid_lat: p.centroid_lat ?? null,
      centroid_lng: p.centroid_lng ?? null,
      metadata: p.city ? { city: p.city } : {},
      parent_path: `${p.state || result.state} · ${p.district || result.district}`,
    })
  }

  const toggle = key => setOpenSec(s => ({ ...s, [key]: !s[key] }))

  return (
    <>
      <div className="s-statbar">
        <div className="s-statcard"><div className="num">{subs.length}</div><div className="lbl">Sub-districts</div></div>
        <div className="s-statcard"><div className="num">{vills.length}</div><div className="lbl">Villages</div></div>
        <div className="s-statcard"><div className="num">{pins.length}</div><div className="lbl">Pincodes</div></div>
      </div>

      <DrillSection
        title="Sub-districts" count={subs.length}
        open={!!openSec.sub} onToggle={() => toggle('sub')}>
        {subs.length === 0
          ? <div className="s-drill-empty">No sub-districts.</div>
          : <div className="s-chip-grid">
              {subs.map(sd => <span key={sd} className="s-chip" onClick={() => selectSub(sd)}>{sd}</span>)}
            </div>}
      </DrillSection>

      <DrillSection
        title="Villages" count={vills.length}
        open={!!openSec.vill} onToggle={() => toggle('vill')}>
        {vills.length === 0
          ? <div className="s-drill-empty">No villages.</div>
          : <div className="s-chip-grid">
              {vills.map(v => <span key={v} className="s-chip" onClick={() => selectVillageFlat(v)}>{v}</span>)}
            </div>}
      </DrillSection>

      <DrillSection
        title="Pincodes" count={pins.length}
        open={!!openSec.pin} onToggle={() => toggle('pin')}>
        {pins.length === 0
          ? <div className="s-drill-empty">No pincodes.</div>
          : <div className="s-chip-grid">
              {pins.map(p => (
                <span key={p.pincode} className="s-chip mono pincode" onClick={() => selectPincode(p)}>
                  {p.pincode}
                </span>
              ))}
            </div>}
      </DrillSection>
    </>
  )
}

function SubDistrictDrilldown({ result, onSelect }) {
  const [villages, setVillages] = useState(null)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    let cancelled = false
    setVillages(null)
    api(`/api/villages?state=${encodeURIComponent(result.state)}&district=${encodeURIComponent(result.district)}&sub_district=${encodeURIComponent(result.sub_district)}`)
      .then(v => { if (!cancelled) setVillages(v) })
      .catch(() => { if (!cancelled) setVillages([]) })
    return () => { cancelled = true }
  }, [result.state, result.district, result.sub_district])

  if (villages == null) return <div className="s-drill-empty">Loading villages…</div>

  function selectVillage(v) {
    onSelect({
      entity_type: 'village',
      name: v, state: result.state, district: result.district,
      sub_district: result.sub_district, pincode: null,
      centroid_lat: null, centroid_lng: null, metadata: {},
      parent_path: `${result.state} · ${result.district} · ${result.sub_district}`,
    })
  }

  return (
    <>
      <div className="s-statbar">
        <div className="s-statcard">
          <div className="num">{villages.length}</div>
          <div className="lbl">Villages</div>
        </div>
      </div>
      <DrillSection
        title="Villages" count={villages.length}
        open={open} onToggle={() => setOpen(!open)}>
        {villages.length === 0
          ? <div className="s-drill-empty">No villages.</div>
          : <div className="s-chip-grid">
              {villages.map(v => <span key={v} className="s-chip" onClick={() => selectVillage(v)}>{v}</span>)}
            </div>}
      </DrillSection>
    </>
  )
}

function DrillSection({ title, count, open, onToggle, children }) {
  return (
    <div className="s-drill">
      <div className={`s-drill-header ${open ? 'open' : ''}`} onClick={onToggle}>
        <span className="s-drill-chev">▸</span>
        <span className="s-drill-title">{title}</span>
        <span className="s-drill-count">{count}</span>
      </div>
      <div className={`s-drill-body ${open ? 'open' : ''}`}>{children}</div>
    </div>
  )
}

// ─── BulkLookup sub-view ──────────────────────────────────────────────────────
const BULK_MAX = 500

function BulkLookup() {
  const [text, setText] = useState('')
  const [typeOverride, setTypeOverride] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [openAlts, setOpenAlts] = useState({})
  const [error, setError] = useState('')

  const names = text
    .split(/[\n,;]+/)
    .map(s => s.trim())
    .filter(Boolean)

  const counterClass =
    names.length > BULK_MAX ? 'err' :
    names.length > BULK_MAX * 0.8 ? 'warn' : ''

  async function runLookup() {
    if (names.length === 0 || names.length > BULK_MAX) return
    setLoading(true)
    setError('')
    setOpenAlts({})
    try {
      const data = await api('/api/search/bulk', {
        method: 'POST',
        body: JSON.stringify({ names, type_override: typeOverride || null }),
      })
      setResults(data)
    } catch (e) {
      setError(e.message)
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  async function downloadXlsx() {
    setExporting(true)
    try {
      const res = await fetch(`${API_BASE}/api/search/bulk/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ names, type_override: typeOverride || null }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      a.href = url
      a.download = `daak_bulk_lookup_${ts}.xlsx`
      document.body.appendChild(a); a.click(); document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      alert(`Export failed: ${e.message}`)
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <div className="bulk-input">
        <div className="bulk-input-row">
          <div>
            <div className="bulk-field-label" style={{ marginBottom: 6 }}>
              Names (one per line, max {BULK_MAX})
            </div>
            <textarea
              className="bulk-textarea"
              placeholder={'Visakhapatnam\nHyderabad\nAnandapuram\n530001\nYendada\n…'}
              value={text}
              onChange={e => setText(e.target.value)}
            />
          </div>
          <div className="bulk-side">
            <div className="bulk-field">
              <span className="bulk-field-label">Type override</span>
              <select className="bulk-select" value={typeOverride}
                      onChange={e => setTypeOverride(e.target.value)}>
                <option value="">Auto-detect</option>
                <option value="district">District</option>
                <option value="sub_district">Sub-district</option>
                <option value="village">Village</option>
                <option value="pincode">Pincode</option>
              </select>
            </div>
            <div className="bulk-field">
              <span className="bulk-field-label">Count</span>
              <span className={`bulk-counter ${counterClass}`}>{names.length} / {BULK_MAX}</span>
            </div>
          </div>
        </div>
        <div className="bulk-actions">
          <button className="primary"
                  onClick={runLookup}
                  disabled={loading || names.length === 0 || names.length > BULK_MAX}>
            {loading ? 'Looking up…' : 'Lookup'}
          </button>
        </div>
      </div>

      {error && <div className="serv-error">{error}</div>}

      {results && (
        <div className="bulk-results">
          <div className="bulk-results-header">
            <div className="bulk-results-title">Results</div>
            <div className="bulk-summary">
              <span>Total: {results.count}</span>
              <span className="ok">Matched: {results.matched}</span>
              <span className="na">NA: {results.not_found}</span>
            </div>
            <button className="bulk-export-btn"
                    onClick={downloadXlsx}
                    disabled={exporting}>
              {exporting ? 'Generating…' : 'Download as XLSX'}
            </button>
          </div>
          <div className="bulk-table-wrap">
            <table className="bulk-table">
              <thead>
                <tr>
                  <th>#</th><th>Input</th><th>Confidence</th><th>Type</th>
                  <th>Matched Name</th><th>State</th><th>District</th>
                  <th>Sub-district</th><th>Pincode</th><th>Alternatives</th>
                </tr>
              </thead>
              <tbody>
                {results.results.map((r, idx) => {
                  const bm = r.best_match || {}
                  const isNa = r.status === 'not_found'
                  const altCount = (r.alternatives || []).length
                  const altOpen = !!openAlts[idx]
                  const cell = (val) => (val === 'NA' || val == null)
                    ? <td className="col-na">NA</td>
                    : <td>{val}</td>
                  return (
                    <React.Fragment key={idx}>
                      <tr className={isNa ? 'row-na' : ''}>
                        <td>{idx + 1}</td>
                        <td>{r.input}</td>
                        <td><span className={`conf-pill conf-${r.confidence}`}>{r.confidence}</span></td>
                        <td>{bm.entity_type === 'sub_district' ? 'Sub-dist' : (bm.entity_type || 'NA')}</td>
                        <td>{bm.name || 'NA'}</td>
                        {cell(bm.state)}
                        {cell(bm.district)}
                        {cell(bm.sub_district)}
                        {cell(bm.pincode)}
                        <td>
                          {altCount > 0
                            ? <button className="alt-btn"
                                      onClick={() => setOpenAlts(s => ({ ...s, [idx]: !s[idx] }))}>
                                {altCount} alt
                              </button>
                            : <span style={{ color: 'var(--text-faint)' }}>—</span>}
                        </td>
                      </tr>
                      {altOpen && (
                        <tr className="alt-row">
                          <td colSpan={10}>
                            <div className="alt-list">
                              {r.alternatives.map((a, i) => (
                                <div key={i} className="alt-list-item">
                                  <span className={`s-result-tag s-tag-${a.entity_type}`}>
                                    {a.entity_type === 'sub_district' ? 'Sub-dist' : a.entity_type}
                                  </span>
                                  <span><strong>{a.name}</strong></span>
                                  <span style={{ color: 'var(--text-faint)' }}>·</span>
                                  <span>{a.state || '—'}</span>
                                  <span style={{ color: 'var(--text-faint)' }}>·</span>
                                  <span>{a.district || '—'}</span>
                                  {a.sub_district && (<>
                                    <span style={{ color: 'var(--text-faint)' }}>·</span>
                                    <span>{a.sub_district}</span>
                                  </>)}
                                  {a.pincode && (<>
                                    <span style={{ color: 'var(--text-faint)' }}>·</span>
                                    <span style={{ fontFamily: 'monospace' }}>{a.pincode}</span>
                                  </>)}
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}


// ============================================================================
// LANES VIEW — district-to-district lane identification (v2)
// Single autocomplete origin · flat list · expandable to sub-district + village
// ============================================================================

function LanesView({ jumpToBuilder }) {
  const [origins, setOrigins] = useState([])           // all districts with centroids
  const [originsLoaded, setOriginsLoaded] = useState(false)
  const [originPicked, setOriginPicked] = useState(null) // { state, district, lat, lng }

  // Combo dropdown state
  const [comboOpen, setComboOpen] = useState(false)
  const [comboQuery, setComboQuery] = useState('')

  const [maxKm, setMaxKm] = useState(800)
  const [minKm, setMinKm] = useState(150)
  const [data, setData] = useState(null)               // /api/lanes/from-district result
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')             // results filter input
  const [exporting, setExporting] = useState(false)
  const [expanded, setExpanded] = useState({})         // 'state|district' -> bool
  const [subData, setSubData] = useState({})           // 'state|district' -> { subs:[], loading }
  const [subExpanded, setSubExpanded] = useState({})   // 'state|district|sub' -> bool
  const [villageData, setVillageData] = useState({})   // 'state|district|sub' -> { villages:[], loading }
  const [showMap, setShowMap] = useState(false)        // map toggle

  const comboRef = useRef(null)
  const comboInputRef = useRef(null)
  const mapRef = useRef(null)
  const mapInstance = useRef(null)
  const mapOverlays = useRef([])

  // Load origins list once
  useEffect(() => {
    api('/api/lanes/origins')
      .then(rows => { setOrigins(rows); setOriginsLoaded(true) })
      .catch(e => setError(`Failed to load districts: ${e.message}`))
  }, [])

  // Combo dropdown items.
  // - When the search box is empty: first 30 districts + a "more" tail line
  // - When the user types: ranked filter (district startsWith → contains → state contains)
  // We compute matches over the whole list so the meta count is accurate.
  const COMBO_VISIBLE_WHEN_EMPTY = 30
  const comboData = (() => {
    const q = comboQuery.trim().toLowerCase()
    if (!q) {
      return {
        matches: origins.slice(0, COMBO_VISIBLE_WHEN_EMPTY),
        totalMatched: origins.length,
        truncated: origins.length > COMBO_VISIBLE_WHEN_EMPTY,
        query: '',
      }
    }
    const out = []
    for (const o of origins) {
      const dl = (o.district || '').toLowerCase()
      const sl = (o.state || '').toLowerCase()
      if (dl.startsWith(q)) out.push({ ...o, _rank: 0 })
      else if (dl.includes(q)) out.push({ ...o, _rank: 1 })
      else if (sl.includes(q)) out.push({ ...o, _rank: 2 })
    }
    out.sort((a, b) => a._rank - b._rank || a.district.localeCompare(b.district))
    return {
      matches: out,
      totalMatched: out.length,
      truncated: false,
      query: q,
    }
  })()

  function pickOrigin(o) {
    setOriginPicked({ state: o.state, district: o.district })
    setComboQuery('')
    setComboOpen(false)
  }

  function clearOrigin() {
    setOriginPicked(null)
    setComboQuery('')
    // keep open for re-pick
  }

  // Close on click-outside or Escape
  useEffect(() => {
    function onDocClick(e) {
      if (comboRef.current && !comboRef.current.contains(e.target)) {
        setComboOpen(false)
      }
    }
    function onKey(e) {
      if (e.key === 'Escape') setComboOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  // Auto-focus the search input when the dropdown opens
  useEffect(() => {
    if (comboOpen && comboInputRef.current) {
      comboInputRef.current.focus()
    }
  }, [comboOpen])

  // Render district name with matched substring highlighted
  function highlight(text, query) {
    if (!query) return text
    const lower = text.toLowerCase()
    const idx = lower.indexOf(query)
    if (idx < 0) return text
    return (
      <>
        {text.slice(0, idx)}
        <mark className="combo-hl">{text.slice(idx, idx + query.length)}</mark>
        {text.slice(idx + query.length)}
      </>
    )
  }

  // Map: render origin pin + destination dots + lines when map is shown and data ready
  useEffect(() => {
    if (!showMap || !data || !mapRef.current) return

    if (!mapInstance.current) {
      mapInstance.current = new window.google.maps.Map(mapRef.current, {
        center: { lat: data.origin.lat, lng: data.origin.lng },
        zoom: 6,
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: true,
      })
    }
    const map = mapInstance.current

    // Clear previous overlays
    mapOverlays.current.forEach(o => o.setMap(null))
    mapOverlays.current = []

    const origin = { lat: data.origin.lat, lng: data.origin.lng }

    // Origin: red pin, larger
    mapOverlays.current.push(new window.google.maps.Marker({
      position: origin,
      map,
      icon: {
        path: window.google.maps.SymbolPath.CIRCLE, scale: 10,
        fillColor: '#dc2626', fillOpacity: 0.95,
        strokeColor: '#fff', strokeWeight: 2,
      },
      title: `Origin: ${data.origin.district}, ${data.origin.state}`,
      zIndex: 2000,
    }))

    // Color by distance bucket
    function colorFor(distKm) {
      if (distKm <= 100) return '#10b981'   // green
      if (distKm <= 250) return '#3b82f6'   // blue
      if (distKm <= 500) return '#f59e0b'   // amber
      return '#ef4444'                       // red
    }

    const bounds = new window.google.maps.LatLngBounds()
    bounds.extend(origin)

    for (const d of data.destinations) {
      const dest = { lat: d.lat, lng: d.lng }
      bounds.extend(dest)
      const color = colorFor(d.distance_km)

      mapOverlays.current.push(new window.google.maps.Polyline({
        path: [origin, dest],
        strokeColor: color, strokeWeight: 1.5, strokeOpacity: 0.4,
        map, clickable: false,
      }))
      mapOverlays.current.push(new window.google.maps.Marker({
        position: dest,
        map,
        icon: {
          path: window.google.maps.SymbolPath.CIRCLE, scale: 5,
          fillColor: color, fillOpacity: 0.9,
          strokeColor: '#fff', strokeWeight: 1.5,
        },
        title: `${d.district}, ${d.state} — ${d.distance_km} km`,
        zIndex: 1000,
      }))
    }

    if (!bounds.isEmpty()) map.fitBounds(bounds)
  }, [showMap, data])

  // Cleanup overlays when leaving the tab
  useEffect(() => {
    return () => {
      mapOverlays.current.forEach(o => o.setMap(null))
      mapOverlays.current = []
    }
  }, [])

  async function runLookup() {
    if (!originPicked) {
      setError('Pick an origin district first.')
      return
    }
    setLoading(true)
    setError('')
    setData(null)
    setExpanded({}); setSubData({}); setSubExpanded({}); setVillageData({})
    setFilter('')
    try {
      const params = new URLSearchParams({
        state: originPicked.state,
        district: originPicked.district,
        min_km: String(minKm),
        max_km: String(maxKm),
      })
      const res = await api(`/api/lanes/from-district?${params}`)
      setData(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function downloadXlsx() {
    if (!originPicked) return
    setExporting(true)
    try {
      const params = new URLSearchParams({
        state: originPicked.state,
        district: originPicked.district,
        min_km: String(minKm),
        max_km: String(maxKm),
      })
      const res = await fetch(`${API_BASE}/api/lanes/from-district/export?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const safe = originPicked.district.replace(/[^a-zA-Z0-9]/g, '_')
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      a.href = url
      a.download = `daak_lanes_from_${safe}_${ts}.xlsx`
      document.body.appendChild(a); a.click(); document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      alert(`Export failed: ${e.message}`)
    } finally {
      setExporting(false)
    }
  }

  function keyOf(d) { return `${d.state}|${d.district}` }
  function subKeyOf(d, sub) { return `${d.state}|${d.district}|${sub}` }

  async function toggleDistrict(d) {
    const k = keyOf(d)
    const willOpen = !expanded[k]
    setExpanded(s => ({ ...s, [k]: willOpen }))
    if (willOpen && !subData[k]) {
      setSubData(s => ({ ...s, [k]: { loading: true, subs: [] } }))
      try {
        const subs = await api(
          `/api/sub_districts?state=${encodeURIComponent(d.state)}&district=${encodeURIComponent(d.district)}`
        )
        setSubData(s => ({ ...s, [k]: { loading: false, subs } }))
      } catch (e) {
        setSubData(s => ({ ...s, [k]: { loading: false, subs: [], error: e.message } }))
      }
    }
  }

  async function toggleSubDistrict(d, sub) {
    const sk = subKeyOf(d, sub)
    const willOpen = !subExpanded[sk]
    setSubExpanded(s => ({ ...s, [sk]: willOpen }))
    if (willOpen && !villageData[sk]) {
      setVillageData(s => ({ ...s, [sk]: { loading: true, villages: [] } }))
      try {
        const villages = await api(
          `/api/villages?state=${encodeURIComponent(d.state)}` +
          `&district=${encodeURIComponent(d.district)}` +
          `&sub_district=${encodeURIComponent(sub)}`
        )
        setVillageData(s => ({ ...s, [sk]: { loading: false, villages } }))
      } catch (e) {
        setVillageData(s => ({ ...s, [sk]: { loading: false, villages: [], error: e.message } }))
      }
    }
  }

  // Filter destinations by user's text — match on district name, sub-district names
  // (only if expanded/loaded), or village names (only if expanded/loaded).
  // We match destinations where ANY level contains the filter substring.
  const destinations = data?.destinations || []
  const filterLower = filter.trim().toLowerCase()

  function rowMatchesFilter(d) {
    if (!filterLower) return true
    if (d.district.toLowerCase().includes(filterLower)) return true
    if (d.state.toLowerCase().includes(filterLower)) return true
    const k = keyOf(d)
    const subs = (subData[k]?.subs) || []
    if (subs.some(s => s.toLowerCase().includes(filterLower))) return true
    for (const sub of subs) {
      const sk = subKeyOf(d, sub)
      const villages = (villageData[sk]?.villages) || []
      if (villages.some(v => v.toLowerCase().includes(filterLower))) return true
    }
    return false
  }

  const visibleDests = destinations.filter(rowMatchesFilter)

  return (
    <main className="lanes-page">
      <div className="serv-header">
        <div className="serv-title">Lane identifier</div>
        <div className="serv-sub">
          Pick an origin district. See all destinations within range, sorted by
          straight-line distance. Click any destination to expand sub-districts
          and villages.
        </div>
      </div>

      {error && <div className="serv-error">{error}</div>}

      <div className="lanes-controls">
        <div className="combo" ref={comboRef}>
          <button type="button"
                  className={`combo-field ${originPicked ? 'has-value' : ''}`}
                  onClick={() => setComboOpen(o => !o)}
                  disabled={!originsLoaded}>
            <span className="combo-icon">⌕</span>
            <span className="combo-value">
              {originPicked
                ? <>
                    <b>{originPicked.district}</b>
                    <span className="combo-value-state"> · {originPicked.state}</span>
                  </>
                : (originsLoaded ? 'Select origin district' : 'Loading districts…')}
            </span>
            {originPicked && (
              <span className="combo-clear"
                    onClick={(e) => { e.stopPropagation(); clearOrigin() }}
                    title="Clear">×</span>
            )}
            <span className={`combo-chev ${comboOpen ? 'open' : ''}`}>▾</span>
          </button>

          {comboOpen && (
            <div className="combo-panel">
              <div className="combo-search">
                <span className="combo-search-icon">⌕</span>
                <input
                  ref={comboInputRef}
                  className="combo-search-input"
                  placeholder={`Type to search ${origins.length} districts…`}
                  value={comboQuery}
                  onChange={e => setComboQuery(e.target.value)}
                />
                {comboQuery && (
                  <span className="combo-search-clear"
                        onClick={() => setComboQuery('')}
                        title="Clear search">×</span>
                )}
              </div>
              <div className="combo-meta">
                {comboQuery
                  ? (comboData.totalMatched === 0
                      ? 'No matches'
                      : `${comboData.totalMatched} match${comboData.totalMatched === 1 ? '' : 'es'}`)
                  : `All ${origins.length} districts · type to filter`}
              </div>
              <div className="combo-list">
                {comboData.matches.length === 0 ? (
                  <div className="combo-empty">No districts match "{comboQuery}"</div>
                ) : (
                  <>
                    {comboData.matches.map(o => (
                      <div key={`${o.state}|${o.district}`}
                           className="combo-opt"
                           onMouseDown={() => pickOrigin(o)}>
                        <span className="combo-opt-d">
                          {highlight(o.district, comboData.query)}
                        </span>
                        <span className="combo-opt-s">
                          {highlight(o.state, comboData.query)}
                        </span>
                      </div>
                    ))}
                    {comboData.truncated && (
                      <div className="combo-tail">
                        … {origins.length - COMBO_VISIBLE_WHEN_EMPTY} more, type to filter
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}
        </div>

        <span className="lanes-control-label">Min</span>
        <input type="number" className="lanes-maxkm"
               min="0" max="3000" step="50"
               value={minKm}
               onChange={e => setMinKm(Math.max(0, parseInt(e.target.value, 10) || 0))} />
        <span className="lanes-control-label">Max</span>
        <input type="number" className="lanes-maxkm"
               min="50" max="3000" step="50"
               value={maxKm}
               onChange={e => setMaxKm(parseInt(e.target.value, 10) || 800)} />
        <span className="lanes-control-label">km</span>
        <button className="lanes-go-btn"
                onClick={runLookup}
                disabled={!originPicked || loading || minKm > maxKm}>
          {loading ? 'Loading…' : 'Find lanes'}
        </button>
      </div>

      {!data && !loading && (
        <div className="serv-empty">
          <div className="ico">🛣️</div>
          Pick a district to begin.
        </div>
      )}

      {data && (
        <>
          <div className="serv-stats" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
            <div className="serv-stat-card highlight">
              <div className="label">Origin</div>
              <div className="value" style={{ fontSize: 16 }}>{data.origin.district}</div>
              <div className="delta">{data.origin.state}</div>
            </div>
            <div className="serv-stat-card">
              <div className="label">Reachable</div>
              <div className="value">{data.total}</div>
              <div className="delta">
                {(data.min_km != null && data.min_km > 0)
                  ? `between ${data.min_km} and ${data.max_km} km`
                  : `within ${data.max_km} km`}
              </div>
            </div>
            <div className="serv-stat-card warn">
              <div className="label">Method</div>
              <div className="value" style={{ fontSize: 14 }}>Haversine</div>
              <div className="delta">road km ≈ × {data.notes.road_multiplier}</div>
            </div>
          </div>

          <div className="lanes-toolbar">
            <div className="lanes-filter-wrap">
              <span className="lanes-filter-icon">⌕</span>
              <input
                type="text"
                className="lanes-filter-input"
                placeholder="Filter results · district, state, sub-district, village…"
                value={filter}
                onChange={e => setFilter(e.target.value)}
              />
            </div>
            <div className="lanes-count">
              Showing <b>{visibleDests.length}</b> of {destinations.length}
            </div>
            <button className={`lanes-map-toggle ${showMap ? 'active' : ''}`}
                    onClick={() => setShowMap(s => !s)}
                    title="Toggle map view">
              {showMap ? '✓ Map' : '◯ Map'}
            </button>
            <button className="lanes-download-btn"
                    onClick={downloadXlsx}
                    disabled={exporting || destinations.length === 0}>
              {exporting ? 'Exporting…' : '↓ Download'}
            </button>
          </div>

          {showMap && (
            <div className="lanes-map-panel">
              <div ref={mapRef} className="lanes-map-canvas" />
            </div>
          )}

          <div className="lanes-list">
            <div className="lanes-list-head">
              <div></div>
              <div>District</div>
              <div>State</div>
              <div>Coverage</div>
              <div className="right">km</div>
              <div className="right">~road km</div>
            </div>

            {visibleDests.length === 0 ? (
              <div className="lanes-empty-state">
                No destinations match "{filter}".
                {filter && (
                  <div style={{ marginTop: 6, fontSize: 11 }}>
                    Tip: sub-district / village names match only when their parent
                    row is expanded (data is loaded lazily).
                  </div>
                )}
              </div>
            ) : visibleDests.map(d => {
              const k = keyOf(d)
              const isOpen = !!expanded[k]
              const subs = (subData[k]?.subs) || []
              const subsLoading = subData[k]?.loading
              const sdc = d.sub_district_count || 0
              const vc  = d.village_count || 0
              const hasChildren = sdc > 0 || vc > 0
              return (
                <div key={k} className={`lane-row ${isOpen ? 'expanded' : ''}`}>
                  <div className="lane-row-body" onClick={() => toggleDistrict(d)}>
                    <span className="lane-chev">▸</span>
                    <div className="lane-district">{d.district}</div>
                    <div className="lane-state">{d.state}</div>
                    <div className="lane-counts">
                      {hasChildren ? (
                        <>
                          <span className="lane-count-num">{sdc.toLocaleString()}</span>
                          <span className="lane-count-lbl">sub-dist</span>
                          <span className="lane-count-sep">·</span>
                          <span className="lane-count-num">{vc.toLocaleString()}</span>
                          <span className="lane-count-lbl">villages</span>
                          {!isOpen && <span className="lane-count-hint">click to view</span>}
                        </>
                      ) : (
                        <span className="lane-count-empty">no village data</span>
                      )}
                    </div>
                    <div className="lane-km right">{d.distance_km}</div>
                    <div className="lane-km right muted">{d.estimated_road_km}</div>
                  </div>
                  {isOpen && (
                    <div className="lane-drawer">
                      {subsLoading ? (
                        <div className="lane-drawer-empty">Loading sub-districts…</div>
                      ) : subs.length === 0 ? (
                        <div className="lane-drawer-empty">No sub-districts found.</div>
                      ) : (
                        <>
                          <div className="lane-drawer-label">{subs.length} sub-district{subs.length === 1 ? '' : 's'}</div>
                          {subs.map(sub => {
                            const sk = subKeyOf(d, sub)
                            const subOpen = !!subExpanded[sk]
                            const vd = villageData[sk]
                            return (
                              <div key={sk} className="sub-card">
                                <div className="sub-card-head" onClick={() => toggleSubDistrict(d, sub)}>
                                  <span className={`sub-chev ${subOpen ? 'open' : ''}`}>▸</span>
                                  <div className="sub-name">{sub}</div>
                                  {vd?.villages && (
                                    <div className="sub-meta">{vd.villages.length} villages</div>
                                  )}
                                </div>
                                {subOpen && (
                                  <div className="sub-villages">
                                    {vd?.loading ? (
                                      <span className="muted">Loading villages…</span>
                                    ) : (vd?.villages || []).length === 0 ? (
                                      <span className="muted">No villages.</span>
                                    ) : (
                                      vd.villages.join(', ')
                                    )}
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </main>
  )
}



// ============================================================================
// ROOT APP — header + tab routing + theme
// ============================================================================

function getInitialTheme() {
  try {
    const saved = localStorage.getItem('daak.theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {}
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState('builder')
  const [builderPrefill, setBuilderPrefill] = useState(null)
  const [theme, setTheme] = useState(getInitialTheme)

  // Apply theme to <html> and persist
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('daak.theme', theme) } catch {}
  }, [theme])

  function jumpToBuilder(prefill) {
    setBuilderPrefill(prefill)
    setActiveTab('builder')
  }

  return (
    <div className="app">
      <header>
        <div className="brand"
             onClick={() => setActiveTab('builder')}
             role="button"
             tabIndex={0}
             title="Go to Builder"
             style={{ cursor: 'pointer' }}>
          <img src="/ondl-logo.png" alt="ONDL" className="brand-logo" />
          <h1>DAAK</h1>
          <span className="badge">Beta</span>
        </div>
        <div className="tabs">
          <div className={`tab ${activeTab === 'builder' ? 'active' : ''}`}
               onClick={() => setActiveTab('builder')}>
            Builder
          </div>
          <div className={`tab ${activeTab === 'serv' ? 'active' : ''}`}
               onClick={() => setActiveTab('serv')}>
            Serviceability
          </div>
          <div className={`tab ${activeTab === 'search' ? 'active' : ''}`}
               onClick={() => setActiveTab('search')}>
            Search
          </div>
          <div className={`tab ${activeTab === 'lanes' ? 'active' : ''}`}
               onClick={() => setActiveTab('lanes')}>
            Lanes
          </div>
        </div>
        <div className="spacer" />
        <button
          className="theme-toggle"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
          {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
        </button>
      </header>

      {activeTab === 'builder' && (
        <BuilderView
          prefilled={builderPrefill}
          onPrefilledHandled={() => setBuilderPrefill(null)}
        />
      )}
      {activeTab === 'serv' && <ServiceabilityView jumpToBuilder={jumpToBuilder} />}
      {activeTab === 'search' && <SearchView jumpToBuilder={jumpToBuilder} />}
      {activeTab === 'lanes' && <LanesView jumpToBuilder={jumpToBuilder} />}
    </div>
  )
}