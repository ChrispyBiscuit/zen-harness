import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { useApp } from "./lib/store"
;(window as any).__lwsSelect = (id: string) => useApp.getState().select(id)
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
