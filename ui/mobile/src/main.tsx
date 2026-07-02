import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { installGlobalDebugMode } from './lib/debug.ts'
import { ConnectionGate } from './components/mobile/ConnectionGate.tsx'
import './mobile.css'

installGlobalDebugMode()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConnectionGate>
      <App />
    </ConnectionGate>
  </StrictMode>,
)
