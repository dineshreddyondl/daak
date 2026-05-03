import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './styles.css'

const GOOGLE_MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY

function loadGoogleMaps() {
  return new Promise((resolve, reject) => {
    if (window.google?.maps) {
      resolve()
      return
    }
    if (!GOOGLE_MAPS_API_KEY) {
      reject(new Error(
        "VITE_GOOGLE_MAPS_API_KEY is not set. " +
        "Create web/.env with: VITE_GOOGLE_MAPS_API_KEY=your_key"
      ))
      return
    }
    const script = document.createElement('script')
    script.src = `https://maps.googleapis.com/maps/api/js?key=${GOOGLE_MAPS_API_KEY}&libraries=places`
    script.async = true
    script.onload = resolve
    script.onerror = () => reject(new Error("Google Maps failed to load"))
    document.head.appendChild(script)
  })
}

loadGoogleMaps().then(() => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  )
}).catch(err => {
  document.getElementById('root').innerHTML =
    `<div style="padding:40px;font-family:sans-serif;">
       <h2 style="color:#dc2626">Failed to load Google Maps</h2>
       <p>${err.message}</p>
     </div>`
})