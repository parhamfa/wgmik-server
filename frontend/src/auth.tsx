import React, { createContext, useContext, useEffect, useState } from 'react';
import { fetchJson } from './api';

interface User {
    id: number;
    username: string;
    is_admin: boolean;
    created_at: string;
}

interface AuthContextType {
    user: User | null;
    loading: boolean;
    login: (creds: any) => Promise<void>;
    logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>(null!);

export function AuthProvider({ children }: { children: React.ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [loading, setLoading] = useState(true);

    const refreshUser = async () => {
        try {
            const u = await fetchJson('/api/auth/me');
            setUser(u);
        } catch (e) {
            setUser(null);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        refreshUser();
    }, []);

    const login = async (creds: any) => {
        await fetchJson('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(creds),
        });
        await refreshUser();
    };

    const logout = async () => {
        try {
            await fetchJson('/api/auth/logout', { method: 'POST' });
        } finally {
            setUser(null);
            window.location.href = '/login';
        }
    };

    return (
        <AuthContext.Provider value={{ user, loading, login, logout }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    return useContext(AuthContext);
}
