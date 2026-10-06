/**
 * Authentication Context — manages login state across the app.
 * Authorization decisions remain server-side; role flags only shape navigation.
 */
import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';

interface User {
  id: number;
  username: string;
  role: string;
}

interface AuthState {
  authenticated: boolean;
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  canAccessMonitor: boolean;
  login: (username: string, password: string) => Promise<{ success: boolean; message: string; role?: string }>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Check existing session on mount.
  useEffect(() => {
    fetch('/api/auth/me', { credentials: 'include' })
      .then((r) => {
        if (!r.ok) return { authenticated: false };
        return r.json();
      })
      .then((d) => {
        if (d.authenticated) setUser(d.user);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const login = async (username: string, password: string) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (data.success) setUser(data.user);
    return { success: data.success, message: data.message, role: data.user?.role };
  };

  const logout = async () => {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
    setUser(null);
  };

  const isAdmin = user?.role === 'admin';
  const canAccessMonitor = user?.role === 'admin' || user?.role === 'operator';

  return (
    <AuthContext.Provider value={{ authenticated: !!user, user, loading, isAdmin, canAccessMonitor, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
