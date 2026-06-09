import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Building, Camera, Check, Copy, Eye, EyeOff, Plus, Trash2, AlertTriangle, Github, MessageSquare, Ticket, Lock, ExternalLink, CreditCard, Download } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Switch } from '../components/ui/switch';

// ---- General Settings ----
function GeneralSettings() {
  const [orgName, setOrgName] = useState('Acme Corp');
  const [autoScan, setAutoScan] = useState(true);
  const [emailNotifs, setEmailNotifs] = useState(true);
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="max-w-2xl space-y-8">
      {/* Organization Profile */}
      <section>
        <h3 className="text-lg font-semibold text-text-primary mb-4">Organization Profile</h3>
        <div className="flex items-center gap-4 mb-4">
          <div className="relative w-20 h-20 rounded-full bg-bg-elevated border border-border-default flex items-center justify-center group cursor-pointer">
            <Building className="w-8 h-8 text-text-tertiary" />
            <div className="absolute inset-0 rounded-full bg-black/50 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
              <Camera className="w-5 h-5 text-white" />
            </div>
          </div>
          <div>
            <div className="text-sm font-medium text-text-primary">Organization Avatar</div>
            <div className="text-xs text-text-tertiary">Click to upload a new avatar</div>
          </div>
        </div>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Organization Name</label>
            <input type="text" value={orgName} onChange={(e) => setOrgName(e.target.value)} className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary" />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Organization Slug</label>
            <div className="flex items-center gap-2">
              <input type="text" value="acme-corp" readOnly className="flex-1 px-4 py-2.5 bg-bg-elevated border border-border-default rounded-md text-sm font-mono text-text-tertiary outline-none" />
              <button className="p-2.5 rounded-md border border-border-default hover:bg-bg-surface text-text-secondary" onClick={() => navigator.clipboard.writeText('acme-corp')}>
                <Copy className="w-4 h-4" />
              </button>
            </div>
            <p className="text-xs text-text-tertiary mt-1">Used in URLs and API calls</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Description</label>
            <textarea rows={3} placeholder="What does your organization do?" className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent-primary resize-none" />
          </div>
        </div>
      </section>

      {/* Scan Defaults */}
      <section className="border-t border-border-default pt-8">
        <h3 className="text-lg font-semibold text-text-primary mb-4">Default Scan Settings</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Fail builds on severity</label>
            <select className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary">
              <option>Critical</option>
              <option selected>High</option>
              <option>Medium</option>
              <option>Low</option>
              <option>Never</option>
            </select>
            <p className="text-xs text-text-tertiary mt-1">CI/CD checks will fail if findings at or above this level are detected.</p>
          </div>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-text-primary">Auto-scan on PR</div>
              <div className="text-xs text-text-tertiary">Automatically trigger scans on every pull request.</div>
            </div>
            <Switch checked={autoScan} onCheckedChange={setAutoScan} />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Scheduled full scans</label>
            <select className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary">
              <option>Daily</option>
              <option selected>Weekly</option>
              <option>Monthly</option>
              <option>Never</option>
            </select>
          </div>
        </div>
      </section>

      {/* Notifications */}
      <section className="border-t border-border-default pt-8">
        <h3 className="text-lg font-semibold text-text-primary mb-4">Notifications</h3>
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-text-primary">Email alerts for new Critical findings</div>
              <div className="text-xs text-text-tertiary">Receive an email when critical vulnerabilities are detected.</div>
            </div>
            <Switch checked={emailNotifs} onCheckedChange={setEmailNotifs} />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Slack Webhook URL</label>
            <input type="text" placeholder="https://hooks.slack.com/services/..." className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent-primary" />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Weekly digest</label>
            <select className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary">
              <option selected>Monday</option>
              <option>Friday</option>
              <option>Never</option>
            </select>
          </div>
        </div>
      </section>

      {/* Danger Zone */}
      <section className="border-t border-red-500/30 pt-8">
        <h3 className="text-lg font-semibold text-red-400 mb-4">Danger Zone</h3>
        <div className="card border-red-500/30 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-text-primary">Delete Organization</div>
              <div className="text-xs text-text-tertiary">This will permanently delete all data. Cannot be undone.</div>
            </div>
            <button className="px-4 py-2 border border-red-500/50 text-red-400 text-sm font-medium rounded-md hover:bg-red-500/10 transition-colors">
              Delete
            </button>
          </div>
        </div>
      </section>

      <div className="flex items-center gap-3">
        <button onClick={handleSave} className={`px-6 py-2.5 text-sm font-semibold rounded-md transition-all ${saved ? 'bg-green-500 text-white' : 'bg-accent-primary text-white hover:bg-accent-primary-hover'}`}>
          {saved ? 'Saved ✓' : 'Save Changes'}
        </button>
      </div>
    </motion.div>
  );
}

// ---- Members ----
function MembersSettings() {
  const [members, setMembers] = useState([
    { id: '1', name: 'Sarah Chen', email: 'sarah@acme.com', role: 'Owner', projects: 'All projects', active: 'Active now' },
    { id: '2', name: 'Mike Torres', email: 'mike@acme.com', role: 'Admin', projects: '8 projects', active: '5m ago' },
    { id: '3', name: 'Alex Kim', email: 'alex@acme.com', role: 'Member', projects: '5 projects', active: '1h ago' },
    { id: '4', name: 'Jamie Liu', email: 'jamie@acme.com', role: 'Member', projects: '3 projects', active: '2d ago' },
    { id: '5', name: 'Priya Patel', email: 'priya@acme.com', role: 'Viewer', projects: '2 projects', active: '1w ago' },
  ]);
  const [showInvite, setShowInvite] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const roleStyles: Record<string, string> = {
    Owner: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
    Admin: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
    Member: 'bg-gray-500/15 text-gray-300 border-gray-500/30',
    Viewer: 'bg-transparent text-text-tertiary border-gray-500/30 border-dashed',
  };

  const removeMember = (id: string) => {
    setMembers((m) => m.filter((mm) => mm.id !== id));
    setConfirmRemove(null);
  };

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-lg font-semibold text-text-primary">Team Members</h3>
          <p className="text-sm text-text-secondary">{members.length} members</p>
        </div>
        <button onClick={() => setShowInvite(true)} className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white text-sm font-semibold rounded-md hover:bg-accent-primary-hover transition-all">
          <Plus className="w-4 h-4" /> Invite Member
        </button>
      </div>

      {showInvite && (
        <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="card rounded-lg p-6 mb-6 border-accent-primary/30">
          <h4 className="text-base font-semibold text-text-primary mb-4">Invite Team Member</h4>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">Email</label>
              <input type="email" placeholder="colleague@company.com" className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent-primary" />
            </div>
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">Role</label>
              <select className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary">
                <option>Member</option>
                <option>Admin</option>
                <option>Viewer</option>
              </select>
            </div>
            <div className="flex items-center gap-3">
              <button onClick={() => setShowInvite(false)} className="px-4 py-2 bg-accent-primary text-white text-sm font-medium rounded-md">Send Invite</button>
              <button onClick={() => setShowInvite(false)} className="px-4 py-2 border border-border-default text-text-secondary text-sm rounded-md hover:bg-bg-surface">Cancel</button>
            </div>
          </div>
        </motion.div>
      )}

      <div className="card rounded-lg overflow-hidden">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-border-default bg-bg-elevated">
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Member</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Role</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Projects</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Last Active</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary"></th>
            </tr>
          </thead>
          <tbody>
            {members.map((m, i) => (
              <motion.tr
                key={m.id}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: i * 0.03 }}
                className="border-b border-border-default last:border-0 hover:bg-bg-surface-hover transition-colors"
              >
                <td className="py-3 px-4">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-accent-primary/20 flex items-center justify-center text-accent-primary text-xs font-semibold">
                      {m.name.split(' ').map((n) => n[0]).join('')}
                    </div>
                    <div>
                      <div className="text-sm font-medium text-text-primary">{m.name}</div>
                      <div className="text-xs text-text-tertiary">{m.email}</div>
                    </div>
                  </div>
                </td>
                <td className="py-3 px-4">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-sm text-xs font-medium border ${roleStyles[m.role]}`}>{m.role}</span>
                </td>
                <td className="py-3 px-4 text-sm text-text-secondary">{m.projects}</td>
                <td className="py-3 px-4 text-sm text-text-secondary">{m.active}</td>
                <td className="py-3 px-4">
                  {confirmRemove === m.id ? (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-text-tertiary">Are you sure?</span>
                      <button onClick={() => removeMember(m.id)} className="text-xs text-red-400 hover:text-red-300 font-medium">Yes</button>
                      <button onClick={() => setConfirmRemove(null)} className="text-xs text-text-secondary hover:text-text-primary">No</button>
                    </div>
                  ) : (
                    <button onClick={() => setConfirmRemove(m.id)} className="p-1.5 rounded-md hover:bg-red-500/10 text-text-tertiary hover:text-red-400 transition-colors">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </td>
              </motion.tr>
            ))}
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}

// ---- Billing ----
function BillingSettings() {
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="max-w-3xl">
      <div className="grid md:grid-cols-5 gap-6">
        <div className="md:col-span-3 space-y-6">
          {/* Current Plan */}
          <div className="card rounded-lg p-6">
            <div className="flex items-center gap-2 mb-3">
              <span className="px-2.5 py-1 bg-accent-primary text-white text-xs font-semibold rounded-sm">Team Plan</span>
            </div>
            <div className="text-3xl font-bold text-text-primary">$49<span className="text-base font-normal text-text-secondary">/month</span></div>
            <p className="text-sm text-text-secondary mt-1">Renews on Nov 15, 2024</p>

            <div className="mt-4 space-y-3">
              <div>
                <div className="flex items-center justify-between text-xs text-text-secondary mb-1">
                  <span>Scans this month</span>
                  <span>347 / 1,000</span>
                </div>
                <div className="w-full h-2 bg-bg-elevated rounded-full overflow-hidden">
                  <div className="h-full bg-accent-primary rounded-full transition-all" style={{ width: '34.7%' }} />
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between text-xs text-text-secondary mb-1">
                  <span>Projects</span>
                  <span>8 / Unlimited</span>
                </div>
                <div className="w-full h-2 bg-bg-elevated rounded-full overflow-hidden">
                  <div className="h-full bg-green-500 rounded-full transition-all" style={{ width: '100%' }} />
                </div>
              </div>
            </div>

            <div className="mt-5 flex items-center gap-3">
              <button className="px-4 py-2 bg-accent-primary text-white text-sm font-semibold rounded-md hover:bg-accent-primary-hover transition-all">Upgrade Plan</button>
              <button className="text-sm text-red-400 hover:text-red-300 hover:underline">Cancel Subscription</button>
            </div>
          </div>

          {/* Payment Method */}
          <div className="card rounded-lg p-6">
            <h4 className="text-base font-semibold text-text-primary mb-4">Payment Method</h4>
            <div className="flex items-center gap-3 mb-4">
              <CreditCard className="w-8 h-8 text-text-secondary" />
              <div>
                <div className="text-sm text-text-primary font-medium">•••• 4242</div>
                <div className="text-xs text-text-tertiary">Visa &bull; Expires 12/25</div>
              </div>
            </div>
            <button className="px-4 py-2 border border-border-default text-text-primary text-sm rounded-md hover:bg-bg-surface transition-all">Update Payment Method</button>
          </div>
        </div>

        {/* Invoice History */}
        <div className="md:col-span-2 card rounded-lg p-6">
          <h4 className="text-base font-semibold text-text-primary mb-4">Invoice History</h4>
          <div className="space-y-4">
            {[
              { date: 'Oct 15, 2024', amount: '$49.00' },
              { date: 'Sep 15, 2024', amount: '$49.00' },
              { date: 'Aug 15, 2024', amount: '$49.00' },
            ].map((inv) => (
              <div key={inv.date} className="flex items-center justify-between py-2 border-b border-border-default last:border-0">
                <div>
                  <div className="text-sm text-text-primary">{inv.date}</div>
                  <div className="flex items-center gap-1 mt-0.5">
                    <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
                    <span className="text-xs text-green-400">Paid</span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium text-text-primary">{inv.amount}</span>
                  <button className="p-1.5 rounded-md hover:bg-bg-surface text-text-tertiary hover:text-text-primary transition-colors">
                    <Download className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// ---- Integrations ----
function IntegrationsSettings() {
  const integrations = [
    { id: 'github', name: 'GitHub', icon: Github, desc: 'Connect repositories, trigger scans on PRs, post findings as PR comments', status: 'connected', detail: '8 repos', connected: true },
    { id: 'gitlab', name: 'GitLab', icon: GitBranchIcon, desc: 'Mirror GitHub integration for GitLab CI/CD pipelines', status: 'not_connected', detail: '', connected: false },
    { id: 'slack', name: 'Slack', icon: MessageSquare, desc: 'Receive scan notifications and critical finding alerts', status: 'connected', detail: '#security-alerts', connected: true },
    { id: 'jira', name: 'Jira', icon: Ticket, desc: 'Create tickets from findings automatically', status: 'not_connected', detail: '', connected: false },
    { id: 'saml', name: 'SAML SSO', icon: Lock, desc: 'Enable single sign-on for your organization', status: 'enterprise', detail: '', connected: false },
  ];

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="max-w-2xl space-y-4">
      {integrations.map((int, i) => (
        <motion.div
          key={int.id}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.08 }}
          className={`card rounded-lg p-5 flex items-center gap-4 transition-all hover:border-border-hover hover:-translate-y-px ${int.connected ? 'border-l-[3px] border-l-green-500/50' : ''}`}
        >
          <div className="w-12 h-12 rounded-lg bg-bg-elevated flex items-center justify-center flex-shrink-0">
            <int.icon className="w-6 h-6 text-text-secondary" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-text-primary">{int.name}</span>
              {int.connected && (
                <span className="px-2 py-0.5 bg-green-500/15 text-green-400 text-xs font-medium rounded-sm border border-green-500/30">
                  Connected {int.detail && `— ${int.detail}`}
                </span>
              )}
              {!int.connected && int.status === 'not_connected' && (
                <span className="px-2 py-0.5 bg-gray-500/15 text-gray-400 text-xs font-medium rounded-sm border border-gray-500/30">Not connected</span>
              )}
              {int.status === 'enterprise' && (
                <span className="px-2 py-0.5 bg-purple-500/15 text-purple-400 text-xs font-medium rounded-sm border border-purple-500/30">Enterprise</span>
              )}
            </div>
            <p className="text-xs text-text-secondary mt-0.5">{int.desc}</p>
          </div>
          <button className={`px-4 py-2 text-sm font-medium rounded-md transition-all ${int.connected ? 'border border-border-default text-text-primary hover:bg-bg-surface' : int.status === 'enterprise' ? 'border border-accent-secondary text-accent-secondary hover:bg-accent-secondary/10' : 'bg-accent-primary text-white hover:bg-accent-primary-hover'}`}>
            {int.connected ? 'Configure' : int.status === 'enterprise' ? 'Upgrade' : 'Connect'}
          </button>
        </motion.div>
      ))}
    </motion.div>
  );
}

// GitBranch icon wrapper for GitLab
function GitBranchIcon(props: { className?: string }) {
  return (
    <svg className={props.className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  );
}

// ---- API Keys ----
function ApiKeysSettings() {
  const [keys, setKeys] = useState([
    { id: '1', name: 'CI/CD Production', key: 'omni_live_a3f9c2d1e4b5', masked: 'omni_••••••••••a3f9', created: 'Oct 1', by: 'sarah', lastUsed: '2m ago' },
    { id: '2', name: 'Staging Environment', key: 'omni_live_b7e2f8a9c3d4', masked: 'omni_••••••••••b7e2', created: 'Sep 15', by: 'mike', lastUsed: '1h ago' },
    { id: '3', name: 'Local Development', key: 'omni_test_d4c1a8b3e5f6', masked: 'omni_••••••••••d4c1', created: 'Aug 20', by: 'alex', lastUsed: '3d ago' },
  ]);
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState<Record<string, boolean>>({});
  const [showGenerate, setShowGenerate] = useState(false);

  const toggleReveal = (id: string) => setRevealed((r) => ({ ...r, [id]: !r[id] }));
  const copyKey = (id: string, fullKey: string) => {
    navigator.clipboard.writeText(fullKey);
    setCopied((c) => ({ ...c, [id]: true }));
    setTimeout(() => setCopied((c) => ({ ...c, [id]: false })), 2000);
  };
  const revokeKey = (id: string) => setKeys((k) => k.filter((kk) => kk.id !== id));

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-lg font-semibold text-text-primary">API Keys</h3>
        <button onClick={() => setShowGenerate(true)} className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white text-sm font-semibold rounded-md hover:bg-accent-primary-hover transition-all">
          <Plus className="w-4 h-4" /> Generate New Key
        </button>
      </div>

      {/* Security notice */}
      <div className="flex items-start gap-3 p-4 mb-6 rounded-md border border-yellow-500/20 bg-yellow-500/5">
        <AlertTriangle className="w-4 h-4 text-sev-medium flex-shrink-0 mt-0.5" />
        <p className="text-xs text-text-secondary">Keep your API keys secure. Never commit them to version control or expose them in client-side code.</p>
      </div>

      {/* Generate modal */}
      {showGenerate && (
        <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="card rounded-lg p-6 mb-6 border-accent-primary/30">
          <h4 className="text-base font-semibold text-text-primary mb-4">Generate API Key</h4>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">Key Name</label>
              <input type="text" placeholder="e.g., Production Deploy" className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent-primary" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1.5">Expiration</label>
                <select className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary">
                  <option>30 days</option>
                  <option selected>90 days</option>
                  <option>1 year</option>
                  <option>Never</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1.5">Scope</label>
                <select className="w-full px-4 py-2.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary">
                  <option selected>Full Access</option>
                  <option>Read Only</option>
                  <option>Scans Only</option>
                </select>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button onClick={() => setShowGenerate(false)} className="px-4 py-2 bg-accent-primary text-white text-sm font-medium rounded-md">Generate</button>
              <button onClick={() => setShowGenerate(false)} className="px-4 py-2 border border-border-default text-text-secondary text-sm rounded-md hover:bg-bg-surface">Cancel</button>
            </div>
          </div>
        </motion.div>
      )}

      <div className="card rounded-lg overflow-hidden">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-border-default bg-bg-elevated">
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Name</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Key</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Created</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Last Used</th>
              <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary"></th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k, i) => (
              <motion.tr
                key={k.id}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: i * 0.04 }}
                className="border-b border-border-default last:border-0 hover:bg-bg-surface-hover transition-colors"
              >
                <td className="py-3 px-4 text-sm font-medium text-text-primary">{k.name}</td>
                <td className="py-3 px-4">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-text-secondary">
                      {revealed[k.id] ? k.key : k.masked}
                    </span>
                    <button onClick={() => toggleReveal(k.id)} className="p-1 rounded hover:bg-bg-surface text-text-tertiary hover:text-text-primary transition-colors">
                      {revealed[k.id] ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                    <button onClick={() => copyKey(k.id, k.key)} className="p-1 rounded hover:bg-bg-surface text-text-tertiary hover:text-text-primary transition-colors">
                      {copied[k.id] ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </td>
                <td className="py-3 px-4 text-xs text-text-secondary">{k.created} by @{k.by}</td>
                <td className="py-3 px-4 text-xs text-text-secondary">{k.lastUsed}</td>
                <td className="py-3 px-4">
                  <button onClick={() => revokeKey(k.id)} className="text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10 px-2 py-1 rounded transition-colors">
                    Revoke
                  </button>
                </td>
              </motion.tr>
            ))}
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}

// ---- Main Settings Page ----
export default function SettingsPage() {
  return (
    <div className="max-w-4xl">
      <h2 className="text-xl font-semibold text-text-primary mb-6">Settings</h2>

      <Tabs defaultValue="general" className="w-full">
        <TabsList className="w-full justify-start bg-transparent border-b border-border-default rounded-none h-auto p-0 mb-6">
          {[
            { value: 'general', label: 'General' },
            { value: 'members', label: 'Members' },
            { value: 'billing', label: 'Billing' },
            { value: 'integrations', label: 'Integrations' },
            { value: 'api-keys', label: 'API Keys' },
          ].map((tab) => (
            <TabsTrigger
              key={tab.value}
              value={tab.value}
              className="rounded-none border-b-2 border-transparent data-[state=active]:border-accent-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none px-5 py-3 text-sm font-medium text-text-secondary data-[state=active]:text-text-primary hover:text-text-primary transition-colors"
            >
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <AnimatePresence mode="wait">
          <TabsContent value="general" className="mt-0">
            <motion.div key="general" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <GeneralSettings />
            </motion.div>
          </TabsContent>
          <TabsContent value="members" className="mt-0">
            <motion.div key="members" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <MembersSettings />
            </motion.div>
          </TabsContent>
          <TabsContent value="billing" className="mt-0">
            <motion.div key="billing" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <BillingSettings />
            </motion.div>
          </TabsContent>
          <TabsContent value="integrations" className="mt-0">
            <motion.div key="integrations" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <IntegrationsSettings />
            </motion.div>
          </TabsContent>
          <TabsContent value="api-keys" className="mt-0">
            <motion.div key="api-keys" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <ApiKeysSettings />
            </motion.div>
          </TabsContent>
        </AnimatePresence>
      </Tabs>
    </div>
  );
}
