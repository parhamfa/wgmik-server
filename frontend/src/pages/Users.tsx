import React, { useEffect, useState } from 'react';
import { fetchJson } from '../api';
import { Link } from 'react-router-dom';

interface User {
    id: number;
    username: string;
    is_admin: boolean;
    created_at: string;
}

export default function Users() {
    const [users, setUsers] = useState<User[]>([]);
    const [newUsername, setNewUsername] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    const loadUsers = async () => {
        try {
            const data = await fetchJson('/api/users');
            setUsers(data);
        } catch (e) {
            console.error(e);
        }
    };

    useEffect(() => {
        loadUsers();
    }, []);

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setSuccess('');
        try {
            await fetchJson('/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: newUsername, password: newPassword }),
            });
            setSuccess(`User ${newUsername} created.`);
            setNewUsername('');
            setNewPassword('');
            loadUsers();
        } catch (err: any) {
            setError(err.message || 'Failed to create user');
        }
    };

    const handleDelete = async (id: number) => {
        if (!confirm('Are you sure you want to delete this user?')) return;
        try {
            await fetchJson(`/api/users/${id}`, { method: 'DELETE' });
            loadUsers();
        } catch (err: any) {
            alert(err.message || 'Failed to delete user');
        }
    };

    return (
        <div className="min-h-screen bg-gray-50 pt-20 pb-12 px-4 sm:px-6 lg:px-8">
            <div className="max-w-4xl mx-auto">
                <div className="flex justify-between items-center mb-6">
                    <h1 className="text-2xl font-bold text-gray-900">User Management (Accounting)</h1>
                    <Link to="/" className="text-indigo-600 hover:text-indigo-900">
                        &larr; Back to Dashboard
                    </Link>
                </div>

                {/* Create User Form */}
                <div className="bg-white shadow sm:rounded-lg mb-8 p-6">
                    <h2 className="text-lg font-medium text-gray-900 mb-4">Add New Admin</h2>
                    <form onSubmit={handleCreate} className="space-y-4 sm:space-y-0 sm:flex sm:gap-4">
                        <div className="flex-1">
                            <input
                                type="text"
                                placeholder="Username"
                                className="shadow-sm focus:ring-indigo-500 focus:border-indigo-500 block w-full sm:text-sm border-gray-300 rounded-md p-2 border"
                                value={newUsername}
                                onChange={(e) => setNewUsername(e.target.value)}
                                required
                            />
                        </div>
                        <div className="flex-1">
                            <input
                                type="password"
                                placeholder="Password"
                                className="shadow-sm focus:ring-indigo-500 focus:border-indigo-500 block w-full sm:text-sm border-gray-300 rounded-md p-2 border"
                                value={newPassword}
                                onChange={(e) => setNewPassword(e.target.value)}
                                required
                            />
                        </div>
                        <button
                            type="submit"
                            className="inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700"
                        >
                            Add User
                        </button>
                    </form>
                    {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
                    {success && <p className="mt-2 text-sm text-green-600">{success}</p>}
                </div>

                {/* User List */}
                <div className="bg-white shadow overflow-hidden sm:rounded-lg">
                    <ul className="divide-y divide-gray-200">
                        {users.map((u) => (
                            <li key={u.id} className="px-6 py-4 flex items-center justify-between">
                                <div>
                                    <p className="text-sm font-medium text-gray-900">{u.username}</p>
                                    <p className="text-xs text-gray-500">Created: {new Date(u.created_at).toLocaleDateString()}</p>
                                </div>
                                <div>
                                    <button
                                        onClick={() => handleDelete(u.id)}
                                        className="text-red-600 hover:text-red-900 text-sm font-medium"
                                    >
                                        Delete
                                    </button>
                                </div>
                            </li>
                        ))}
                        {users.length === 0 && (
                            <li className="px-6 py-4 text-gray-500 text-center">No users found?</li>
                        )}
                    </ul>
                </div>
            </div>
        </div>
    );
}
