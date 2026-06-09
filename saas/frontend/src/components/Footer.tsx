export default function Footer() {
  return (
    <footer className="border-t border-border-default py-6 mt-12">
      <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-text-tertiary">
        <span>&copy; 2024 Omni-Auditor. All rights reserved.</span>
        <div className="flex items-center gap-6">
          <a href="#" className="hover:text-text-secondary transition-colors">Privacy Policy</a>
          <a href="#" className="hover:text-text-secondary transition-colors">Terms of Service</a>
          <a href="#" className="hover:text-text-secondary transition-colors">Security</a>
        </div>
      </div>
    </footer>
  );
}
