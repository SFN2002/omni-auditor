import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface Notification {
  id: string;
  title: string;
  message: string;
  read: boolean;
  created_at: string;
}

interface AppState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  notifications: Notification[];
  unreadCount: number;
  addNotification: (n: Omit<Notification, 'id' | 'read' | 'created_at'>) => void;
  markRead: (id: string) => void;
  pageTitle: string;
  setPageTitle: (title: string) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      notifications: [
        {
          id: '1',
          title: 'Scan completed',
          message: 'payment-service scan completed with 12 findings',
          read: false,
          created_at: new Date(Date.now() - 120000).toISOString(),
        },
        {
          id: '2',
          title: 'Critical finding detected',
          message: 'SQL Injection found in payments/views.py',
          read: false,
          created_at: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          id: '3',
          title: 'Baseline improved',
          message: 'web-api project risk score improved to 0.24',
          read: true,
          created_at: new Date(Date.now() - 86400000).toISOString(),
        },
      ],
      unreadCount: 2,
      addNotification: (n) =>
        set((s) => {
          const notif: Notification = {
            ...n,
            id: Math.random().toString(36).slice(2),
            read: false,
            created_at: new Date().toISOString(),
          };
          return {
            notifications: [notif, ...s.notifications],
            unreadCount: s.unreadCount + 1,
          };
        }),
      markRead: (id) =>
        set((s) => ({
          notifications: s.notifications.map((n) =>
            n.id === id ? { ...n, read: true } : n
          ),
          unreadCount: Math.max(0, s.unreadCount - (s.notifications.find((n) => n.id === id && !n.read) ? 1 : 0)),
        })),
      pageTitle: 'Dashboard',
      setPageTitle: (title) => set({ pageTitle: title }),
    }),
    {
      name: 'omni-auditor-store',
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed }),
    }
  )
);
