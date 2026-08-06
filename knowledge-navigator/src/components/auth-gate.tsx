import { useEffect, useState, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { captureTokenFromUrl, getAuthStatus, getMe, loginUrl } from "@/lib/auth";

type Gate = "checking" | "open" | "signin";

/**
 * Gates the app behind employee Google sign-in — but ONLY when the backend reports
 * AUTH_ENABLED. When auth is off (default), it renders children immediately, so the
 * current app is unchanged. On first paint it also captures the `?token=` the OAuth
 * callback leaves in the URL.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  // Default "open" so SSR + auth-disabled setups render the app with no flash.
  const [state, setState] = useState<Gate>("open");
  const [domain, setDomain] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    captureTokenFromUrl();
    (async () => {
      const status = await getAuthStatus();
      if (cancelled) return;
      if (!status || !status.enabled) {
        setState("open"); // auth disabled → no gate
        return;
      }
      setDomain(status.allowed_domain);
      const me = await getMe();
      if (!cancelled) setState(me.authenticated ? "open" : "signin");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "signin") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="w-full max-w-sm rounded-xl border bg-card p-8 text-center shadow-sm">
          <Sparkles className="mx-auto h-9 w-9 text-accent" />
          <h1 className="mt-4 text-xl font-semibold">RippleBot</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Sign in with your {domain ? `@${domain}` : "RippleHire"} account to continue.
          </p>
          <a
            href={loginUrl()}
            className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-md border bg-white px-4 py-2 text-sm font-medium text-gray-800 shadow-sm transition-colors hover:bg-gray-50"
          >
            <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
              <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.4 5.4 2.5 13.3l7.8 6.1C12.2 13.3 17.6 9.5 24 9.5z" />
              <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.5 3-2.2 5.5-4.7 7.2l7.3 5.7c4.3-4 6.9-9.9 6.9-17.4z" />
              <path fill="#FBBC05" d="M10.3 28.4c-.5-1.4-.8-2.9-.8-4.4s.3-3 .8-4.4l-7.8-6.1C.9 16.6 0 20.2 0 24s.9 7.4 2.5 10.5l7.8-6.1z" />
              <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.3-5.7c-2 1.4-4.7 2.3-8.6 2.3-6.4 0-11.8-3.8-13.7-9.4l-7.8 6.1C6.4 42.6 14.6 48 24 48z" />
            </svg>
            Sign in with Google
          </a>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
