import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useMemo, useState } from 'react';
import { Bell, ChevronDown, LayoutGrid, LogOut, Moon, Package, Plus, Search, Settings as SettingsIcon, Sun, UserRound, X, Upload, Paperclip, CheckCircle2 } from 'lucide-react';
import clsx from 'clsx';
import { ApiHttpError, api, notifyDataUpdated, seedCustomers } from '../utils';
import type { DemoUser } from '../utils';

const navigation = [{ section: 'WORKSPACE', items: [{ name: 'Overview', path: '/overview', icon: LayoutGrid }, { name: 'Inventory', path: '/inventory', icon: Package }, { name: 'Forecasts', path: '/forecasts', icon: LayoutGrid }] }, { section: 'ENGAGEMENT', items: [{ name: 'Customers', path: '/customers', icon: UserRound }, { name: 'Campaigns', path: '/campaigns', icon: Plus }, { name: 'WhatsApp messages', path: '/whatsapp-messages', icon: Bell }] }];

type LayoutProps = { user: DemoUser; onLogout: () => void; theme: 'light' | 'dark'; setTheme: (theme: 'light' | 'dark') => void };

function CampaignModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [customers, setCustomers] = useState<any[]>(seedCustomers);
  const [selected, setSelected] = useState<number[]>([]);
  const [message, setMessage] = useState('Your favourites are back in stock. Shop before they are gone.');
  const [file, setFile] = useState<File | null>(null);
  const [launching, setLaunching] = useState(false);
  const [success, setSuccess] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  useEffect(() => {
    if (open) {
      setErrorMsg('');
      api.get('/customers').then((data) => setCustomers(data.length ? data : seedCustomers)).catch(() => setCustomers(seedCustomers));
    }
  }, [open]);

  if (!open) return null;
  const toggle = (id: number) => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const launch = async () => {
    if (!message.trim() || selected.length === 0) return;
    setLaunching(true);
    setErrorMsg('');
    try {
      await api.post('/campaigns', { message, customer_ids: selected, attachment: file?.name });
      notifyDataUpdated();
      setSuccess(true);
      setTimeout(() => { setSuccess(false); setSelected([]); setFile(null); onClose(); }, 1500);
    } catch (error) {
      if (error instanceof ApiHttpError) {
        setErrorMsg(error.message);
      } else {
        setSuccess(true);
        setTimeout(() => { setSuccess(false); onClose(); }, 1500);
      }
    } finally {
      setLaunching(false);
    }
  };

  return <div className="modal-backdrop" onMouseDown={onClose}><div className="modal-card campaign-modal" onMouseDown={(e) => e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">CAMPAIGN STUDIO</span><h2>Create campaign</h2><p>Craft a focused message and choose exactly who receives it.</p></div><button className="icon-button" onClick={onClose}><X size={18} /></button></div>{success ? <div className="success-state"><CheckCircle2 size={52} /><h3>Campaign launched</h3><p>Your message is queued for {selected.length} selected customer{selected.length === 1 ? '' : 's'}.</p></div> : <><label className="field-label">Message to convey<textarea rows={4} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Write a short, useful message..." /></label><label className="upload-zone"><Upload size={20} /><span>{file ? file.name : 'Attach media or files'}</span><small>Images, PDF, CSV or XLSX · optional</small><input type="file" accept="image/*,.pdf,.csv,.xlsx,.xls" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label><div className="recipient-head"><div><strong>Select recipients</strong><span>{selected.length} selected</span></div><button className="text-button" onClick={() => setSelected(selected.length === customers.length ? [] : customers.map((customer) => customer.id))}>{selected.length === customers.length ? 'Clear all' : 'Select all'}</button></div><div className="recipient-list">{customers.slice(0, 200).map((customer) => <label className="recipient-row" key={customer.id}><input type="checkbox" checked={selected.includes(customer.id)} onChange={() => toggle(customer.id)} /><span className="avatar avatar-small">{customer.name.split(' ').map((word: string) => word[0]).join('').slice(0, 2)}</span><span className="recipient-copy"><strong>{customer.name}</strong><small>{customer.phone}</small></span><span className="recipient-tag">{customer.favorite_category || 'Customer'}</span></label>)}</div>{errorMsg && <div className="auth-notice" style={{ marginTop: 12 }}>{errorMsg}</div>}<div className="modal-actions"><button className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={launching || !message.trim() || selected.length === 0} onClick={launch}>{launching ? 'Launching...' : 'Launch campaign'}<Paperclip size={16} /></button></div></>}</div></div>;
}

export default function Layout({ user, onLogout, theme, setTheme }: LayoutProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [sidebarProfileOpen, setSidebarProfileOpen] = useState(false);
  const [topbarProfileOpen, setTopbarProfileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [campaignOpen, setCampaignOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState('');

  useEffect(() => { const handler = () => setCampaignOpen(true); window.addEventListener('nexaflow:open-campaign', handler); return () => window.removeEventListener('nexaflow:open-campaign', handler); }, []);
  useEffect(() => { setSidebarProfileOpen(false); setTopbarProfileOpen(false); setNotificationsOpen(false); }, [location.pathname]);

  const handleImportInventory = async () => {
    if (!importFile) return;
    setImporting(true);
    setImportStatus('');
    try {
      const formData = new FormData();
      formData.append('file', importFile);
      const headerLine = (await importFile.slice(0, 512).text()).split(/\r?\n/)[0]?.toLowerCase() || '';
      const isSalesCsv = headerLine.includes('date') && headerLine.includes('quantity');
      const endpoint = isSalesCsv ? '/upload/sales' : '/upload/inventory';
      const res = await api.upload(endpoint, formData);
      notifyDataUpdated();
      setImportStatus(isSalesCsv ? `Imported ${res.rows_added} sales rows.` : `Imported inventory: ${res.added} added, ${res.updated} updated.`);
      setTimeout(() => { setImportOpen(false); setImportFile(null); setImportStatus(''); }, 1200);
    } catch (error) {
      setImportStatus(error instanceof Error ? error.message : 'Unable to import file. Please upload a valid CSV.');
    } finally {
      setImporting(false);
    }
  };

  const searchResults = useMemo(() => [...navigation.flatMap((section) => section.items), { name: 'Settings', path: '/settings', icon: SettingsIcon }].filter((item) => search && item.name.toLowerCase().includes(search.toLowerCase())), [search]);
  const initials = (user.businessName || 'NF').split(' ').map((word) => word[0]).join('').slice(0, 2).toUpperCase();

  return <div className="app-shell"><aside className="sidebar"><div className="brand"><div className="brand-mark">N</div><span>NexaFlow</span></div><nav className="sidebar-nav">{navigation.map((section) => <div className="nav-section" key={section.section}><span className="nav-label">{section.section}</span>{section.items.map((item) => { const Icon = item.icon; return <Link key={item.path} to={item.path} className={clsx('nav-item', location.pathname === item.path && 'active')}><Icon size={18} /><span>{item.name}</span></Link>; })}</div>)}</nav><div className="sidebar-bottom"><Link to="/settings" className={clsx('nav-item', location.pathname === '/settings' && 'active')}><SettingsIcon size={18} /><span>Settings</span></Link><button className="sidebar-profile" onClick={() => { setSidebarProfileOpen(!sidebarProfileOpen); setTopbarProfileOpen(false); }}><div className="avatar avatar-small">{initials}</div><div><strong>{user.businessName}</strong><span>Business owner</span></div><ChevronDown size={15} /></button>{sidebarProfileOpen && <div className="profile-menu sidebar-menu"><button onClick={() => { navigate('/settings'); setSidebarProfileOpen(false); }}><UserRound size={16} />Profile</button><button onClick={onLogout}><LogOut size={16} />Logout</button></div>}</div></aside><main className="main-shell"><header className="topbar"><div className="search-wrap"><Search size={17} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search pages, customers, campaigns..." />{search && <button onClick={() => setSearch('')}><X size={15} /></button>}{searchResults.length > 0 && <div className="search-results">{searchResults.map((result) => { const ResultIcon = result.icon; return <button key={result.path} onClick={() => { navigate(result.path); setSearch(''); }}><ResultIcon size={16} />{result.name}</button>; })}</div>}</div><div className="topbar-actions"><button className="circle-button" title="Toggle theme" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}</button><button className="circle-button notification-button" title="Notifications" onClick={() => { setNotificationsOpen(!notificationsOpen); setTopbarProfileOpen(false); }}><Bell size={18} /><span /></button><button className="profile-trigger" onClick={() => { setTopbarProfileOpen(!topbarProfileOpen); setSidebarProfileOpen(false); setNotificationsOpen(false); }}><div className="avatar avatar-small">{initials}</div><ChevronDown size={15} /></button>{notificationsOpen && <div className="notification-panel"><div className="panel-title"><strong>Notifications</strong><span>Alerts &amp; activity</span></div><button type="button" className="notification-item" style={{ width: '100%', textAlign: 'left', background: 'transparent', borderLeft: 0, borderRight: 0, borderBottom: 0 }} onClick={() => { navigate('/inventory'); setNotificationsOpen(false); }}><span className="notification-dot amber" /><div><strong>Restock alerts</strong><p>Review low-stock SKUs in Inventory.</p></div></button><button type="button" className="notification-item" style={{ width: '100%', textAlign: 'left', background: 'transparent', borderLeft: 0, borderRight: 0, borderBottom: 0 }} onClick={() => { navigate('/campaigns'); setNotificationsOpen(false); }}><span className="notification-dot green" /><div><strong>Campaigns</strong><p>Check draft and sent campaigns.</p></div></button></div>}{topbarProfileOpen && <div className="profile-menu top-menu"><button onClick={() => { navigate('/settings'); setTopbarProfileOpen(false); }}><UserRound size={16} />Profile</button><button onClick={onLogout}><LogOut size={16} />Logout</button></div>}</div></header><div className="content-scroll"><Outlet context={{ user, onCreateCampaign: () => setCampaignOpen(true), onConnectInventory: () => { setImportStatus(''); setImportOpen(true); } }} /></div><footer className="app-footer"><span>© 2026 NexaFlow</span><span>Inventory intelligence for modern retail</span></footer></main><CampaignModal open={campaignOpen} onClose={() => setCampaignOpen(false)} />{importOpen && <div className="modal-backdrop" onMouseDown={() => setImportOpen(false)}><div className="modal-card import-modal" onMouseDown={(e) => e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">INVENTORY CONNECT</span><h2>Connect your inventory</h2><p>Import a CSV file to update inventory or sales records.</p></div><button className="icon-button" onClick={() => setImportOpen(false)}><X size={18} /></button></div><label className="upload-zone upload-zone-large"><Upload size={28} /><strong>{importFile ? importFile.name : 'Choose a CSV file'}</strong><small>Inventory CSV (sku, name, category, price, current_stock) or Sales CSV</small><input type="file" accept=".csv" onChange={(e) => { setImportFile(e.target.files?.[0] || null); setImportStatus(''); }} /></label>{importStatus && <div className="auth-notice">{importStatus}</div>}<div className="modal-actions"><button className="secondary-button" onClick={() => setImportOpen(false)}>Cancel</button><button className="primary-button" disabled={!importFile || importing} onClick={handleImportInventory}>{importing ? 'Importing...' : 'Import inventory'}</button></div></div></div>}</div>;
}
