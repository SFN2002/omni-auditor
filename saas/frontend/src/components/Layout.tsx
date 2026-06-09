import { Outlet } from 'react-router';
import { useEffect } from 'react';
import Navbar from './Navbar';
import Header from './Header';
import { useAppStore } from '../stores/appStore';
import { useLocation } from 'react-router';

export default function Layout() {
  const setPageTitle = useAppStore((s) => s.setPageTitle);
  const location = useLocation();

  useEffect(() => {
    const path = location.pathname;
    if (path === '/dashboard') setPageTitle('Dashboard');
    else if (path === '/projects') setPageTitle('Projects');
    else if (path.startsWith('/projects/')) setPageTitle('Project Detail');
    else if (path === '/scans') setPageTitle('Scans');
    else if (path.startsWith('/scans/')) setPageTitle('Scan Detail');
    else if (path === '/settings') setPageTitle('Settings');
    else setPageTitle('Omni-Auditor');
  }, [location.pathname, setPageTitle]);

  return (
    <div className="min-h-screen bg-bg-deep">
      <Navbar />
      <Header />
      <main className="lg:ml-[260px] mt-16 p-8 bg-bg-base min-h-[calc(100vh-64px)]">
        <Outlet />
      </main>
    </div>
  );
}
