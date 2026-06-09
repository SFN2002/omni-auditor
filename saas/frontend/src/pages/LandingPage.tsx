import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Shield, ShieldAlert, Zap, BarChart3, ChevronRight, Github, Twitter, Linkedin, Check, ExternalLink } from 'lucide-react';
import { Link } from 'react-router';

function AnimatedCounter({ target, suffix = '', duration = 1200 }: { target: number; suffix?: string; duration?: number }) {
  const [value, setValue] = useState(0);
  const ref = useRef<HTMLSpanElement>(null);
  const [seen, setSeen] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) setSeen(true); }, { threshold: 0.3 });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!seen) return;
    let frame: number;
    const start = performance.now();
    const animate = (now: number) => {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(Math.round(target * eased * 10) / 10);
      if (p < 1) frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [seen, target, duration]);

  return <span ref={ref}>{value.toLocaleString()}{suffix}</span>;
}

export default function LandingPage() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 100);
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const words = 'Find Vulnerabilities Before They Find You'.split(' ');

  return (
    <div className="min-h-screen bg-bg-deep">
      {/* Navigation */}
      <nav className={`fixed top-0 w-full h-16 z-50 flex items-center justify-between px-6 transition-all duration-300 ${scrolled ? 'bg-bg-deep/80 backdrop-blur-lg border-b border-border-default' : 'bg-transparent'}`}>
        <div className="flex items-center gap-2">
          <Shield className="w-6 h-6 text-accent-primary" />
          <span className="text-lg font-bold text-text-primary">Omni-Auditor</span>
        </div>
        <div className="hidden md:flex items-center gap-8 text-sm font-medium text-text-secondary">
          <a href="#features" className="hover:text-text-primary transition-colors">Features</a>
          <a href="#how-it-works" className="hover:text-text-primary transition-colors">How it Works</a>
          <a href="#pricing" className="hover:text-text-primary transition-colors">Pricing</a>
          <Link to="/dashboard" className="text-text-primary hover:text-text-primary/80">Sign In</Link>
        </div>
        <Link to="/dashboard" className="px-4 py-2 border border-accent-primary text-accent-primary text-sm font-medium rounded-md hover:bg-accent-primary hover:text-white transition-all">
          Get Started
        </Link>
      </nav>

      {/* Hero */}
      <section className="relative min-h-screen flex items-center justify-center overflow-hidden">
        {/* Animated gradient blobs */}
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
          <div className="absolute -top-40 -left-40 w-[500px] h-[500px] rounded-full bg-accent-primary/[0.08] blur-[100px] animate-[pulse_8s_ease-in-out_infinite]" />
          <div className="absolute -bottom-40 -right-40 w-[600px] h-[600px] rounded-full bg-accent-secondary/[0.06] blur-[120px] animate-[pulse_10s_ease-in-out_infinite_1s]" />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] h-[400px] rounded-full bg-sev-critical/[0.04] blur-[80px] animate-[pulse_12s_ease-in-out_infinite_2s]" />
          {/* Grid overlay */}
          <div className="absolute inset-0" style={{
            backgroundImage: 'linear-gradient(rgba(51,65,85,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(51,65,85,0.15) 1px, transparent 1px)',
            backgroundSize: '40px 40px',
          }} />
        </div>

        <div className="relative z-10 max-w-4xl mx-auto text-center px-6 pt-20">
          <motion.h1 className="text-4xl md:text-5xl lg:text-6xl font-bold text-text-primary leading-[1.1] tracking-tight">
            {words.map((word, i) => (
              <motion.span
                key={i}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.6, delay: i * 0.08, ease: [0, 0, 0.2, 1] }}
                className="inline-block mr-[0.3em]"
              >
                {word}
              </motion.span>
            ))}
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.4 }}
            className="mt-6 text-lg text-text-secondary max-w-2xl mx-auto leading-relaxed"
          >
            Omni-Auditor is a Python static analysis security engine that scans your code,
            detects OWASP Top 10 risks, and delivers actionable remediation guidance — all in your CI/CD pipeline.
          </motion.p>

          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.4, delay: 0.7 }}
            className="mt-8 flex items-center justify-center gap-4"
          >
            <Link
              to="/dashboard"
              className="px-8 py-3.5 bg-accent-primary text-white text-base font-semibold rounded-md hover:-translate-y-0.5 hover:shadow-[0_8px_24px_rgba(59,130,246,0.3)] active:scale-[0.98] transition-all"
            >
              Start Free Scan
            </Link>
            <a href="#features" className="text-text-secondary hover:text-text-primary font-medium transition-colors flex items-center gap-1 group">
              View Documentation
              <ExternalLink className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </a>
          </motion.div>

          {/* Terminal mockup */}
          <motion.div
            initial={{ opacity: 0, y: 30, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.7, delay: 0.9 }}
            className="mt-12 max-w-2xl mx-auto rounded-t-lg overflow-hidden shadow-[0_20px_60px_rgba(59,130,246,0.08)]"
          >
            <div className="bg-bg-elevated px-4 py-3 flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500" />
              <div className="w-3 h-3 rounded-full bg-yellow-500" />
              <div className="w-3 h-3 rounded-full bg-green-500" />
              <span className="ml-3 text-xs text-text-tertiary font-mono">omni-auditor</span>
            </div>
            <div className="bg-[#0d1117] p-5 font-mono text-sm text-left">
              <div className="text-text-secondary">$ <span className="text-text-primary">omni-audit scan --target ./src</span></div>
              <div className="mt-1.5 text-text-secondary">&rarr; Scanning 1,247 files... <span className="text-green-400">Done (2.3s)</span></div>
              <div className="mt-1.5">
                &rarr; <span className="text-red-400">3 Critical</span> | <span className="text-orange-400">7 High</span> | <span className="text-yellow-400">12 Medium</span> | <span className="text-blue-400">4 Low</span>
              </div>
              <div className="mt-1.5 text-text-secondary">
                &rarr; Risk Score: <span className="text-orange-400 font-semibold">6.8/10 — ELEVATED</span>
              </div>
              <div className="mt-1.5 text-text-secondary">&rarr; Report: <span className="text-accent-primary">./omni-audit-report.html</span></div>
            </div>
          </motion.div>
        </div>
      </section>

      {/* Trust Bar */}
      <section className="bg-bg-elevated border-y border-border-default py-8">
        <div className="max-w-6xl mx-auto px-6 flex flex-wrap justify-around gap-8">
          {[
            { value: 10, suffix: 'M+', label: 'Lines Analyzed' },
            { value: 2400, suffix: '+', label: 'Vulnerabilities Found' },
            { value: 99.2, suffix: '%', label: 'Detection Accuracy', isDecimal: true },
            { value: 3, prefix: '< ', suffix: 's', label: 'Average Scan Time' },
          ].map((stat) => (
            <div key={stat.label} className="text-center">
              <div className="text-3xl font-bold text-accent-primary">
                {stat.prefix}
                <AnimatedCounter target={stat.value} suffix={stat.suffix} />
              </div>
              <div className="text-xs text-text-tertiary uppercase tracking-[0.08em] mt-1">{stat.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section id="features" className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-center mb-12"
          >
            <h2 className="text-3xl md:text-4xl font-semibold text-text-primary">
              Built for Security-First Engineering Teams
            </h2>
            <p className="mt-3 text-text-secondary text-base max-w-xl mx-auto">
              Deep static analysis that understands Python's unique security landscape
            </p>
          </motion.div>

          <div className="grid md:grid-cols-3 gap-6">
            {[
              { icon: ShieldAlert, color: 'text-red-400', bg: 'bg-red-500/10', title: 'OWASP Top 10 Coverage', desc: 'Detects SQL injection, XSS, command injection, path traversal, insecure deserialization, weak cryptography, and more — mapped directly to OWASP categories.' },
              { icon: Zap, color: 'text-accent-primary', bg: 'bg-accent-primary/10', title: 'CI/CD Native', desc: 'Integrates directly into GitHub Actions, GitLab CI, Jenkins, and Azure DevOps. Fails builds on configurable severity thresholds. No infrastructure to manage.' },
              { icon: BarChart3, color: 'text-accent-secondary', bg: 'bg-accent-secondary/10', title: 'Actionable Intelligence', desc: 'Every finding includes the exact file, line number, vulnerable code snippet, severity justification, and a recommended fix with code examples.' },
            ].map((feat, i) => (
              <motion.div
                key={feat.title}
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1, duration: 0.5 }}
                className="card p-8 rounded-lg"
              >
                <div className={`w-12 h-12 rounded-lg ${feat.bg} flex items-center justify-center`}>
                  <feat.icon className={`w-6 h-6 ${feat.color}`} />
                </div>
                <h3 className="mt-4 text-lg font-semibold text-text-primary">{feat.title}</h3>
                <p className="mt-2 text-sm text-text-secondary leading-relaxed">{feat.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="bg-bg-elevated py-20 px-6">
        <div className="max-w-4xl mx-auto">
          <motion.h2
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-3xl md:text-4xl font-semibold text-text-primary text-center mb-12"
          >
            How Omni-Auditor Works
          </motion.h2>

          <div className="flex flex-col md:flex-row items-center justify-center gap-8 md:gap-4">
            {[
              { num: '1', title: 'Connect Your Repository', desc: 'Link your GitHub, GitLab, or Bitbucket repo in seconds. Omni-Auditor automatically discovers Python files and dependencies.' },
              { num: '2', title: 'Automated Scanning', desc: 'Every pull request triggers a full static analysis. 40+ security checks run against your code, identifying vulnerabilities in real time.' },
              { num: '3', title: 'Fix with Confidence', desc: 'Review detailed findings with code snippets, severity ratings, and remediation guidance. Export SARIF for integration with other tools.' },
            ].map((step, i) => (
              <div key={step.num} className="flex items-center gap-4">
                <motion.div
                  initial={{ opacity: 0, scale: 0.8, x: -20 }}
                  whileInView={{ opacity: 1, scale: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: i * 0.15, duration: 0.5 }}
                  className="flex flex-col items-center text-center max-w-[280px]"
                >
                  <div className="w-12 h-12 rounded-full bg-accent-primary flex items-center justify-center text-white font-bold text-xl">
                    {step.num}
                  </div>
                  <h3 className="mt-4 text-lg font-semibold text-text-primary">{step.title}</h3>
                  <p className="mt-2 text-sm text-text-secondary leading-relaxed">{step.desc}</p>
                </motion.div>
                {i < 2 && <ChevronRight className="hidden md:block w-6 h-6 text-text-tertiary -mt-8" />}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Dashboard Preview */}
      <section className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-center mb-12"
          >
            <h2 className="text-3xl md:text-4xl font-semibold text-text-primary">Your Security Posture, Visualized</h2>
            <p className="mt-3 text-text-secondary">A command center for your codebase security</p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 40, scale: 0.95 }}
            whileInView={{ opacity: 1, y: 0, scale: 1 }}
            viewport={{ once: true, amount: 0.15 }}
            transition={{ duration: 0.8 }}
            className="rounded-xl border border-border-default overflow-hidden shadow-[0_24px_80px_rgba(0,0,0,0.4)]"
          >
            {/* Mock header */}
            <div className="bg-bg-elevated px-4 py-3 flex items-center gap-3 border-b border-border-default">
              <div className="flex items-center gap-1.5">
                <div className="w-3 h-3 rounded-full bg-red-500" />
                <div className="w-3 h-3 rounded-full bg-yellow-500" />
                <div className="w-3 h-3 rounded-full bg-green-500" />
              </div>
              <span className="text-sm text-text-secondary ml-4">Dashboard — Omni-Auditor</span>
            </div>
            {/* Mock content */}
            <div className="bg-bg-base p-6">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                {[
                  { label: 'Total Projects', value: '12', sub: '+2 this month' },
                  { label: 'Total Scans', value: '156', sub: 'Last scan 2m ago' },
                  { label: 'Open Findings', value: '47', sub: '↓ 12% from last week' },
                  { label: 'Avg Risk Score', value: '4.2', sub: 'Low Risk' },
                ].map((m) => (
                  <div key={m.label} className="bg-bg-surface border border-border-default rounded-lg p-4">
                    <div className="text-xs uppercase tracking-wider text-text-tertiary">{m.label}</div>
                    <div className="text-2xl font-bold text-text-primary mt-1">{m.value}</div>
                    <div className="text-xs text-green-400 mt-1">{m.sub}</div>
                  </div>
                ))}
              </div>
              <div className="grid lg:grid-cols-2 gap-4">
                <div className="bg-bg-surface border border-border-default rounded-lg p-4">
                  <div className="text-sm font-medium text-text-primary mb-4">Severity Distribution</div>
                  <div className="flex items-center gap-6">
                    <div className="w-24 h-24 rounded-full border-[12px] border-sev-critical/30 border-t-sev-critical border-r-sev-high border-b-sev-medium border-l-sev-low mx-auto" />
                    <div className="flex flex-col gap-2 text-xs">
                      {[{ l: 'Critical', v: 3, c: 'text-red-400' }, { l: 'High', v: 8, c: 'text-orange-400' }, { l: 'Medium', v: 14, c: 'text-yellow-400' }, { l: 'Low', v: 22, c: 'text-blue-400' }].map(s => (
                      <div key={s.l} className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${s.c.replace('text', 'bg')}`} />
                        <span className="text-text-secondary">{s.l}: <span className={s.c}>{s.v}</span></span>
                      </div>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="bg-bg-surface border border-border-default rounded-lg p-4">
                  <div className="text-sm font-medium text-text-primary mb-4">Recent Scans</div>
                  <div className="space-y-2">
                    {[
                      { p: 'payment-service', s: 'Complete', f: '12', r: '6.8' },
                      { p: 'user-auth-api', s: 'Complete', f: '5', r: '3.2' },
                      { p: 'data-processor', s: 'Running', f: '—', r: '—' },
                    ].map((row) => (
                      <div key={row.p} className="flex items-center justify-between text-xs py-1.5 border-b border-border-default last:border-0">
                        <span className="text-text-primary">{row.p}</span>
                        <span className="text-green-400">{row.s}</span>
                        <span className="text-text-secondary">{row.f}</span>
                        <span className="text-text-secondary">{row.r}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="bg-bg-deep py-20 px-6">
        <div className="max-w-5xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-center mb-12"
          >
            <h2 className="text-3xl md:text-4xl font-semibold text-text-primary">Simple, Transparent Pricing</h2>
            <p className="mt-3 text-text-secondary">Start free. Scale as your team grows.</p>
          </motion.div>

          <div className="grid md:grid-cols-3 gap-6">
            {[
              {
                name: 'Starter', price: '$0', period: '/month',
                desc: 'For individual developers and open source projects',
                features: ['Unlimited public repos', '1 private repo', '100 scans/month', 'Basic severity reports', 'Community support'],
                cta: 'Get Started Free', primary: false,
              },
              {
                name: 'Team', price: '$49', period: '/month',
                desc: 'For engineering teams shipping secure code',
                features: ['Unlimited repos', '1,000 scans/month', 'Advanced risk scoring', 'CI/CD integrations', 'SARIF export', 'GitHub PR checks', 'Email support'],
                cta: 'Start 14-Day Trial', primary: true, featured: true,
              },
              {
                name: 'Enterprise', price: 'Custom', period: '',
                desc: 'For organizations with advanced security requirements',
                features: ['Everything in Team', 'Unlimited scans', 'SSO / SAML', 'Custom policies', 'Dedicated support', 'SLA guarantee', 'On-premise option'],
                cta: 'Contact Sales', primary: false,
              },
            ].map((plan, i) => (
              <motion.div
                key={plan.name}
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1, duration: 0.5 }}
                className={`relative card rounded-lg p-8 ${plan.featured ? 'border-accent-primary shadow-[0_0_0_1px_#3B82F6,0_8px_32px_rgba(59,130,246,0.1)]' : ''}`}
              >
                {plan.featured && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-accent-primary text-white text-xs font-semibold rounded-sm">
                    FEATURED
                  </span>
                )}
                <h3 className="text-xl font-semibold text-text-primary">{plan.name}</h3>
                <div className="mt-2 flex items-baseline gap-1">
                  <span className="text-4xl font-bold text-text-primary">{plan.price}</span>
                  <span className="text-text-secondary">{plan.period}</span>
                </div>
                <p className="mt-2 text-sm text-text-secondary">{plan.desc}</p>
                <div className="my-5 border-t border-border-default" />
                <ul className="space-y-3">
                  {plan.features.map((f) => (
                    <li key={f} className="flex items-center gap-2 text-sm text-text-secondary">
                      <Check className="w-4 h-4 text-green-400 flex-shrink-0" />
                      {f}
                    </li>
                  ))}
                </ul>
                <Link
                  to="/dashboard"
                  className={`mt-6 block w-full text-center py-2.5 rounded-md text-sm font-semibold transition-all ${plan.primary
                    ? 'bg-accent-primary text-white hover:bg-accent-primary-hover hover:-translate-y-px hover:shadow-lg hover:shadow-accent-primary/25'
                    : 'border border-border-default text-text-primary hover:bg-bg-surface hover:border-border-hover'
                    }`}
                >
                  {plan.cta}
                </Link>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA + Footer */}
      <section className="py-20 px-6">
        <div className="max-w-2xl mx-auto text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            <h2 className="text-3xl md:text-4xl font-bold text-text-primary">Secure Your Python Code Today</h2>
            <p className="mt-4 text-text-secondary max-w-lg mx-auto">
              Join 500+ engineering teams that trust Omni-Auditor to catch vulnerabilities before they reach production.
            </p>
            <Link
              to="/dashboard"
              className="inline-block mt-8 px-10 py-4 bg-accent-primary text-white text-lg font-semibold rounded-md hover:bg-accent-primary-hover hover:-translate-y-0.5 hover:shadow-xl active:scale-[0.98] transition-all"
            >
              Start Free Scan
            </Link>
          </motion.div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border-default py-8 px-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm text-text-tertiary">
            <Shield className="w-5 h-5 text-accent-primary" />
            <span>&copy; 2024 Omni-Auditor. All rights reserved.</span>
          </div>
          <div className="flex items-center gap-6 text-sm text-text-tertiary">
            <a href="#" className="hover:text-text-secondary transition-colors">Privacy Policy</a>
            <a href="#" className="hover:text-text-secondary transition-colors">Terms of Service</a>
            <a href="#" className="hover:text-text-secondary transition-colors">Security</a>
          </div>
          <div className="flex items-center gap-4">
            <a href="#" className="text-text-tertiary hover:text-text-secondary transition-colors"><Github className="w-5 h-5" /></a>
            <a href="#" className="text-text-tertiary hover:text-text-secondary transition-colors"><Twitter className="w-5 h-5" /></a>
            <a href="#" className="text-text-tertiary hover:text-text-secondary transition-colors"><Linkedin className="w-5 h-5" /></a>
          </div>
        </div>
      </footer>
    </div>
  );
}
