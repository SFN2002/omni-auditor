import { Search, Bell, Plus } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { Link } from 'react-router';
import { useState } from 'react';

export default function Header() {
  const pageTitle = useAppStore((s) => s.pageTitle);
  const notifications = useAppStore((s) => s.notifications);
  const unreadCount = useAppStore((s) => s.unreadCount);
  const markRead = useAppStore((s) => s.markRead);
  const [showNotifs, setShowNotifs] = useState(false);

  return (
    <header className="fixed top-0 left-0 right-0 h-16 bg-bg-elevated border-b border-border-default z-50 flex items-center justify-between px-6 lg:pl-[276px]">
      <h1 className="text-lg font-semibold text-text-primary ml-10 lg:ml-0">{pageTitle}</h1>

      <div className="flex items-center gap-4">
        {/* Search */}
        <div className="hidden md:flex items-center bg-bg-surface border border-border-default rounded-md px-3 py-2 w-64">
          <Search className="w-4 h-4 text-text-tertiary mr-2" />
          <input
            type="text"
            placeholder="Search projects, scans..."
            className="bg-transparent text-sm text-text-primary placeholder:text-text-tertiary outline-none w-full"
          />
        </div>

        {/* Notifications */}
        <div className="relative">
          <button
            onClick={() => setShowNotifs(!showNotifs)}
            className="relative p-2 rounded-md hover:bg-bg-surface transition-colors"
          >
            <Bell className="w-5 h-5 text-text-secondary" />
            {unreadCount > 0 && (
              <span className="absolute top-0.5 right-0.5 w-4 h-4 bg-sev-critical text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                {unreadCount}
              </span>
            )}
          </button>

          {showNotifs && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowNotifs(false)} />
              <div className="absolute right-0 top-full mt-2 w-80 bg-bg-surface border border-border-default rounded-lg shadow-xl z-50 overflow-hidden">
                <div className="px-4 py-3 border-b border-border-default">
                  <span className="text-sm font-semibold text-text-primary">Notifications</span>
                </div>
                <div className="max-h-80 overflow-y-auto">
                  {notifications.map((n) => (
                    <button
                      key={n.id}
                      onClick={() => { markRead(n.id); }}
                      className={`w-full text-left px-4 py-3 border-b border-border-default last:border-0 hover:bg-bg-surface-hover transition-colors ${!n.read ? 'bg-accent-primary/5' : ''}`}
                    >
                      <div className="text-sm font-medium text-text-primary">{n.title}</div>
                      <div className="text-xs text-text-secondary mt-0.5">{n.message}</div>
                      <div className="text-xs text-text-tertiary mt-1">
                        {new Date(n.created_at).toLocaleTimeString()}
                      </div>
                    </button>
                  ))}
                </div>
                <div className="px-4 py-2 border-t border-border-default text-center">
                  <Link
                    to="/settings"
                    onClick={() => setShowNotifs(false)}
                    className="text-xs text-accent-primary hover:underline"
                  >
                    Notification Settings
                  </Link>
                </div>
              </div>
            </>
          )}
        </div>

        {/* New Scan button */}
        <Link
          to="/scans"
          className="hidden md:inline-flex items-center gap-2 px-4 py-2 bg-accent-primary text-white text-sm font-semibold rounded-md hover:bg-accent-primary-hover hover:-translate-y-px hover:shadow-lg hover:shadow-accent-primary/25 active:scale-[0.98] transition-all"
        >
          <Plus className="w-4 h-4" />
          New Scan
        </Link>
      </div>
    </header>
  );
}
