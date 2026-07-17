import { useAuth } from "@/lib/auth-simple";
import { useState } from "react";
import { Card } from "@/components/layout/Card";

/** Login form — POSTs to /auth/login, server sets a session cookie. */
export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(username, password);
    } catch (err: unknown) {
      const e = err as {
        response?: { data?: { error?: string }; status?: number };
      };
      if (e.response?.status === 401) {
        setError(e.response?.data?.error || "Invalid username or password");
      } else {
        setError("Login failed — please check your credentials");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <h1 className="uppercase">Noble Trader</h1>
      <div className="card card-border bg-base-100 w-96">
        <div className="card-body">
          <Card title="Sign In" className="w-full max-w-md border-t-amber-400">
            <form onSubmit={handleSubmit} className="space-y-3">
              <div className="form-control">
                <label className="label">
                  <span className="label-text">Username</span>
                </label>
                <input
                  type="text"
                  className="input input-bordered w-full"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  required
                />
              </div>
              <div className="form-control">
                <label className="label">
                  <span className="label-text">Password</span>
                </label>
                <input
                  type="password"
                  className="input input-bordered w-full "
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  autoFocus
                  required
                />
              </div>
              {error && (
                <div className="alert alert-error text-sm py-2">{error}</div>
              )}
              <div className="card-actions justify-end">
                <button
                  type="submit"
                  className="btn btn-primary w-full"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Signing in…" : "Sign in"}
                </button>
              </div>
              <p className="text-xs opacity-50 mt-2">
                Use any username and password to login (mock authentication)
              </p>
            </form>
          </Card>
        </div>
      </div>
    </div>
  );
}
