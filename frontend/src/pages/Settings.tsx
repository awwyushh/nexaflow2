import { useEffect, useState } from 'react';
import { Download, LogOut, Save, ShieldCheck } from 'lucide-react';
import { api, notifyDataUpdated } from '../utils';
import type { DemoUser } from '../utils';
import { updateBusinessProfile } from '../services/auth';

type SettingsProps = { user: DemoUser; onLogout: () => void; onProfileUpdated: (user: DemoUser) => void };
export default function Settings({ user, onLogout, onProfileUpdated }: SettingsProps) {
  const [businessName, setBusinessName] = useState(user.businessName);
  const [email, setEmail] = useState(user.email);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState({ lead_time_days: 3, safety_stock_percent: 20, critical_threshold_days: 3, reorder_threshold_days: 7, message_tone: 'friendly', auto_approve: false });

  useEffect(() => {
    setBusinessName(user.businessName);
    setEmail(user.email);
  }, [user.businessName, user.email]);

  useEffect(() => {
    api.get('/settings').then((saved) => {
      if (!saved || typeof saved !== 'object') return;
      setSettings({
        lead_time_days: Number(saved.lead_time_days ?? 3),
        safety_stock_percent: Number(saved.safety_stock_percent ?? 20),
        critical_threshold_days: Number(saved.critical_threshold_days ?? 3),
        reorder_threshold_days: Number(saved.reorder_threshold_days ?? 7),
        message_tone: saved.message_tone === 'formal' ? 'formal' : 'friendly',
        auto_approve: String(saved.auto_approve ?? 'false').toLowerCase() === 'true',
      });
    }).catch(() => undefined);
  }, []);

  const save = async () => {
    setSaving(true);
    setMessage('');
    try {
      const updated = await updateBusinessProfile({ businessName, email });
      onProfileUpdated({ businessName: updated.business_name, email: updated.email });
      try {
        await api.put('/settings', settings);
        notifyDataUpdated();
      } catch { /* backend settings remain optional */ }
      setMessage(email.trim().toLowerCase() !== user.email.toLowerCase() ? 'Saved. Your new email address is ready to use.' : 'Profile and preferences saved.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to save your profile.');
    } finally {
      setSaving(false);
    }
  };
  const exportData = () => { const blob = new Blob([JSON.stringify({ profile: { businessName, email }, settings }, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'nexaflow-business-data.json'; anchor.click(); URL.revokeObjectURL(url); };
  return <div className="page-container"><div className="page-heading"><div><span className="eyebrow">WORKSPACE SETTINGS</span><h1>Profile & preferences</h1><p>Keep your business details and automation preferences up to date.</p></div><button className="primary-button" onClick={save} disabled={saving}><Save size={16} />{saving ? 'Saving...' : 'Save changes'}</button></div><div className="settings-grid"><section className="card settings-card"><div className="settings-title"><div className="settings-icon"><ShieldCheck size={19} /></div><div><h2>Business profile</h2><p>Shown across your workspace and campaign activity.</p></div></div><label className="field-label">Business name<input value={businessName} onChange={(e) => setBusinessName(e.target.value)} /></label><label className="field-label">Registered email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label><p className="muted" style={{ marginTop: 4 }}>Updating your email takes effect when you click Save changes.</p><div className="settings-divider" /><button className="secondary-button" onClick={exportData}><Download size={16} />Export data</button></section><section className="card settings-card"><div className="settings-title"><div className="settings-icon"><Save size={19} /></div><div><h2>Automation preferences</h2><p>Set the signals that shape your daily workflow.</p></div></div>{[['lead_time_days','Lead time (days)'],['safety_stock_percent','Safety stock (%)'],['critical_threshold_days','Critical threshold (days)'],['reorder_threshold_days','Reorder threshold (days)']].map(([key, label]) => <label className="field-label" key={key}>{label}<input type="number" min={0} value={settings[key as keyof typeof settings] as number} onChange={(e) => setSettings({ ...settings, [key]: Math.max(0, Number(e.target.value) || 0) })} /></label>)}<label className="field-label">Message tone<select value={settings.message_tone} onChange={(e) => setSettings({ ...settings, message_tone: e.target.value })}><option value="friendly">Friendly</option><option value="formal">Formal</option></select></label><label className="toggle-row"><input type="checkbox" checked={settings.auto_approve} onChange={(e) => setSettings({ ...settings, auto_approve: e.target.checked })} /><span><strong>Auto-approve campaigns</strong><small>Send restock campaigns without manual review.</small></span></label></section></div>{message && <div className="save-message">{message}</div>}<button className="logout-button" onClick={onLogout}><LogOut size={16} />Logout from this workspace</button></div>;
}
