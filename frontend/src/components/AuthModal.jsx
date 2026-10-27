import React from "react";
import { Bus } from "lucide-react";
import { SignIn } from "@clerk/react";

export default function AuthModal() {
  return (
    <main className="auth-required-overlay">
      <div className="auth-gate-layout">
        <div className="auth-brand-lockup">
          <span className="auth-brand-mark"><Bus size={22} strokeWidth={2.4} /></span>
          <span>AutoRoster</span>
        </div>
        <p className="auth-tagline">Turn raw employee data into optimized bus rosters. Sign in as a supervisor to continue.</p>
        <div className="clerk-signin-shell" aria-label="Supervisor sign in">
          <SignIn
            routing="hash"
            forceRedirectUrl="/"
            withSignUp
            appearance={{
              variables: {
                colorPrimary: "#5B6EE1",
                colorText: "#1F2A44",
                colorTextSecondary: "#7A87A3",
                colorBackground: "#FFFFFF",
                borderRadius: "0.75rem",
                fontFamily: "Inter, system-ui, sans-serif",
              },
              elements: {
                rootBox: "clerk-signin-root",
                cardBox: "clerk-signin-cardbox",
                card: "clerk-signin-card",
                footer: "clerk-signin-footer",
                headerTitle: "clerk-signin-title",
                headerSubtitle: "clerk-signin-subtitle",
                formButtonPrimary: "clerk-signin-primary",
                footerActionLink: "clerk-signin-link",
              },
            }}
          />
        </div>
      </div>
    </main>
  );
}
