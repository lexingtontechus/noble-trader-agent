import { useAuth } from "@/lib/auth";
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
      const e = err as { response?: { data?: { error?: string }; status?: number } };
      if (e.response?.status === 401) {
        setError(e.response?.data?.error || "Invalid username or password");
      } else {
        setError("Login failed — is the FastAPI backend running on :8080?");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <Card title="Sign in" className="w-full max-w-md">
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
              className="input input-bordered w-full"
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
          <button
            type="submit"
            className="btn btn-primary w-full"
            disabled={isSubmitting}
          >
            {isSubmitting ? "Signing in…" : "Sign in"}
          </button>
          <p className="text-xs opacity-50 mt-2">
            Credentials are set via <code className="text-primary">HERMES_ADMIN_USERNAME</code> and{" "}
            <code className="text-primary">HERMES_ADMIN_PASSWORD</code> in the
            backend's <code className="text-primary">.env</code> file.
          </p>
        </form>
      </Card>
    </div>
  );
}
