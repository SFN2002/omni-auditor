import { NavLink } from 'react-router';
import { Shield, LayoutDashboard, FolderGit, Activity, Settings, ChevronDown, Menu, X } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { useState } from 'react';

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/projects', label: 'Projects', icon: FolderGit },
  { to: '/scans', label: 'Scans', icon: Activity },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export default function Navbar() {
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <>
      {/* Mobile hamburger */}
      <button
        onClick={() => setMobileOpen(!mobileOpen)}
        className="lg:hidden fixed top-3 left-4 z-[60] p-2 rounded-md bg-bg-surface border border-border-default text-text-primary"
      >
        {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 z-[55] bg-black/50"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed left-0 top-0 h-screen z-[56] bg-bg-deep border-r border-border-default transition-all duration-200
          ${mobileOpen ? 'translate-x-0' : '-translate-x-full'} lg:translate-x-0
          ${sidebarCollapsed ? 'lg:w-[72px]' : 'lg:w-[260px]'}
          w-[260px]
        `}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 px-5 h-16 border-b border-border-default">
          <Shield className="w-7 h-7 text-accent-primary flex-shrink-0" />
          <span className={`text-lg font-bold text-text-primary whitespace-nowrap transition-opacity ${sidebarCollapsed ? 'lg:opacity-0 lg:w-0' : 'opacity-100'}`}>
            Omni-Auditor
          </span>
        </div>

        {/* Nav items */}
        <nav className="flex flex-col gap-1 p-3 mt-2">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `
                  flex items-center gap-3 px-3 py-2.5 rounded-md text-sm font-medium transition-all duration-200
                  ${isActive
                    ? 'bg-accent-primary/10 text-text-primary border-l-[3px] border-accent-primary'
                    : 'text-text-secondary hover:bg-bg-surface hover:text-text-primary border-l-[3px] border-transparent'
                  }
                `}
              >
                <Icon className="w-5 h-5 flex-shrink-0" />
                <span className={`whitespace-nowrap transition-opacity ${sidebarCollapsed ? 'lg:opacity-0 lg:w-0 lg:hidden' : 'opacity-100'}`}>
                  {item.label}
                </span>
              </NavLink>
            );
          })}
        </nav>

        {/* User profile */}
        <div className="absolute bottom-0 left-0 right-0 border-t border-border-default p-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-accent-primary/20 flex items-center justify-center text-accent-primary text-sm font-semibold flex-shrink-0">
              SC
            </div>
            <div className={`transition-opacity ${sidebarCollapsed ? 'lg:opacity-0 lg:hidden' : 'opacity-100'}`}>
              <div className="text-sm font-medium text-text-primary">Sarah Chen</div>
              <div className="text-xs text-text-tertiary">sarah@acme.com</div>
            </div>
            <ChevronDown className={`w-4 h-4 text-text-tertiary ml-auto transition-opacity ${sidebarCollapsed ? 'lg:hidden' : ''}`} />
          </div>
        </div>
      </aside>
    </>
  );
}
