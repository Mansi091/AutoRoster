import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider } from '@clerk/react'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import { Bus } from 'lucide-react'

const clerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

function ClerkSetupNotice() {
  return (
    <main className="auth-required-overlay">
      <div className="auth-gate-layout">
        <div className="auth-brand-lockup"><span className="auth-brand-mark"><Bus size={22} /></span><span>AutoRoster</span></div>
        <section className="auth-required-card clerk-setup-notice">
          <h1>Connect Clerk to continue</h1>
          <p>Add the Clerk publishable key to <code>frontend/.env</code>, and the Clerk secret key to the project <code>.env</code>. Then restart the frontend and backend.</p>
          <code className="clerk-env-example">VITE_CLERK_PUBLISHABLE_KEY=pk_test_…</code>
          <code className="clerk-env-example">CLERK_SECRET_KEY=sk_test_…</code>
        </section>
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {clerkPublishableKey ? (
      <ClerkProvider publishableKey={clerkPublishableKey}>
        <ErrorBoundary><App /></ErrorBoundary>
      </ClerkProvider>
    ) : <ClerkSetupNotice />}
  </StrictMode>,
)

