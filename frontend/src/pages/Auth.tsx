import { useState } from 'react';
import { ArrowRight, Check, Eye, EyeOff, LockKeyhole, Mail, Sparkles } from 'lucide-react';
import type { DemoUser } from '../utils';
import { loginBusinessOwner, requestPasswordReset, requestSignupOtp, verifySignupOtp } from '../services/auth';

type AuthProps = { onAuthenticated: (user?: DemoUser) => void };

export default function Auth({ onAuthenticated }: AuthProps) {
  const [mode, setMode] = useState<'login' | 'signup' | 'reset'>('login');
  const [showPassword, setShowPassword] = useState(false);
  const [otpSent, setOtpSent] = useState(false);
  const [canResendConfirmation, setCanResendConfirmation] = useState(false);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');
  const [form, setForm] = useState({ businessName: '', email: '', otp: '', password: '' });
  const update = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setNotice(''); setLoading(true);
    try {
      if (mode === 'reset') {
        await requestPasswordReset(form.email);
        setNotice('Password reset instructions have been sent to your email.');
      } else if (mode === 'signup') {
        if (!otpSent) {
          await requestSignupOtp({ businessName: form.businessName, email: form.email, password: form.password });
          setOtpSent(true);
          setCanResendConfirmation(true);
          setNotice('Verification code sent. Check your email and enter it below.');
        } else {
          await verifySignupOtp({ businessName: form.businessName, email: form.email, password: form.password, code: form.otp });
          onAuthenticated({ businessName: form.businessName.trim(), email: form.email.trim().toLowerCase() });
        }
      } else {
        const data = await loginBusinessOwner({ email: form.email, password: form.password });
        onAuthenticated({ businessName: data.user.user_metadata?.business_name || 'My Business', email: data.user.email || form.email.trim().toLowerCase() });
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Something went wrong. Please try again.');
    }
    finally { setLoading(false); }
  };

  const resendConfirmation = async () => {
    setNotice(''); setLoading(true);
    try {
      await requestSignupOtp({ businessName: form.businessName, email: form.email, password: form.password });
      setNotice('A new verification code has been sent. Check your email.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Something went wrong. Please try again.');
    } finally { setLoading(false); }
  };

  return <div className="auth-shell"><div className="auth-brand"><div className="brand-mark">N</div><span>NexaFlow</span></div><div className="auth-grid"><section className="auth-intro"><div className="eyebrow"><Sparkles size={15} /> RETAIL OPERATIONS, REFINED</div><h1>Turn every restock into a reason to reconnect.</h1><p>One calm workspace for inventory intelligence, customer engagement, and revenue momentum.</p><div className="auth-proof"><div className="proof-icon"><Check size={18} /></div><div><strong>Built for business owners</strong><span>Live inventory signals and campaigns in one place.</span></div></div></section><section className="auth-card"><div className="auth-card-head"><span className="auth-kicker">{mode === 'login' ? 'WELCOME BACK' : mode === 'reset' ? 'RESET PASSWORD' : 'START YOUR WORKSPACE'}</span><h2>{mode === 'login' ? 'Sign in to NexaFlow' : mode === 'reset' ? 'Recover your account' : 'Create your account'}</h2><p>{mode === 'login' ? 'Use your registered business email and password.' : mode === 'reset' ? 'Enter your email to receive a reset link.' : otpSent ? 'Enter the verification code sent to your email.' : 'Create your business workspace securely.'}</p></div><form onSubmit={submit} className="auth-form">{mode === 'signup' && <label>Business name<input value={form.businessName} onChange={(e) => update('businessName', e.target.value)} placeholder="Keystone Market" required disabled={otpSent} /></label>}<label>Email address<div className="input-icon"><Mail size={17} /><input type="email" value={form.email} onChange={(e) => update('email', e.target.value)} placeholder="you@business.com" required disabled={otpSent} /></div></label>{mode === 'signup' && otpSent && <label>Verification code<input value={form.otp} onChange={(e) => update('otp', e.target.value)} placeholder="Enter the code from your email" required /></label>}{mode !== 'reset' && <label>Password<div className="input-icon"><LockKeyhole size={17} /><input type={showPassword ? 'text' : 'password'} value={form.password} onChange={(e) => update('password', e.target.value)} placeholder="At least 8 characters" minLength={8} required disabled={otpSent} /><button type="button" className="password-toggle" onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>}{notice && <div className="auth-notice">{notice}</div>}<button className="primary-button auth-submit" type="submit" disabled={loading}>{loading ? 'Please wait...' : mode === 'login' ? 'Sign in' : mode === 'reset' ? 'Send reset link' : otpSent ? 'Verify email' : 'Create account'}<ArrowRight size={17} /></button></form>{mode === 'signup' && otpSent && <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>{canResendConfirmation && <button className="auth-switch" type="button" onClick={resendConfirmation} disabled={loading}>Resend email</button>}<button className="auth-switch" type="button" onClick={() => { setOtpSent(false); setCanResendConfirmation(false); setNotice(''); }}>Edit details</button></div>}{mode === 'login' && <button className="auth-switch" type="button" style={{ paddingBottom: 0 }} onClick={() => { setMode('reset'); setNotice(''); }}>Forgot your password?</button>}<button className="auth-switch" onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setOtpSent(false); setCanResendConfirmation(false); setNotice(''); }}>{mode === 'login' ? 'New here? Create a business account' : 'Already have an account? Sign in'}</button><small className="auth-footnote">Verification codes are sent from the NexaFlow backend through Brevo. Supabase stores your account and session.</small></section></div></div>;
}
