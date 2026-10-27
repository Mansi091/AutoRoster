import React from "react";
import { Bus } from "lucide-react";
import { SignIn } from "@clerk/react";

export default function AuthModal() {
  return (
    <main className="modal-overlay auth-required-overlay">
      <div className="auth-gate-layout">
        <div className="auth-brand-lockup">
          <span className="auth-brand-mark"><Bus size={22} strokeWidth={2.4} /></span>
          <span>AutoRoster</span>
        </div>
        <div className="clerk-signin-shell" aria-label="Supervisor sign in">
          <SignIn
            routing="hash"
            forceRedirectUrl="/"
            withSignUp
            appearance={{
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
