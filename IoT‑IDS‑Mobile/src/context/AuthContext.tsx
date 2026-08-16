/**
 * 认证上下文 — 管理登录状态，逻辑对齐原网页 frontend/src/contexts/AuthContext.tsx
 * 仅账号名为 "admin" 的用户拥有管理员权限。
 */
import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
} from 'react';
import { api } from '../api';
import { initClient, clearSession } from '../api/client';
import type { User } from '../types';

interface AuthState {
  authenticated: boolean;
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<{ success: boolean; message: string }>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // 启动时恢复会话并校验
  useEffect(() => {
    (async () => {
      try {
        await initClient();
        const d = await api.me();
        if (d.authenticated && d.user) setUser(d.user);
      } catch {
        // 后端不可达或未登录，忽略
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    try {
      const data = await api.login(username, password);
      if (data.success && data.user) setUser(data.user);
      return { success: data.success, message: data.message || '登录成功' };
    } catch (e: any) {
      return { success: false, message: e?.message || '登录失败，请检查后端服务' };
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // ignore
    }
    clearSession();
    setUser(null);
  }, []);

  // 不要信任客户端角色字段，只有 username === 'admin' 才是管理员（与后端一致）
  const isAdmin = user?.username === 'admin';

  return (
    <AuthContext.Provider
      value={{ authenticated: !!user, user, loading, isAdmin, login, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
