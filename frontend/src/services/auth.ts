import { isSupabaseConfigured, supabase } from '../lib/supabase';
import { API_BASE } from '../utils';

function ensureSupabaseConfigured() {
  if (!isSupabaseConfigured) {
    throw new Error('Supabase env vars missing in this build (VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY). Set them in Netlify and redeploy with Clear cache.');
  }
}

async function backendAuth<T>(path: string, body: unknown, token?: string): Promise<T> {
  const url = `${API_BASE}/auth${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : 'Network error';
    throw new Error(`${reason}: Cannot reach auth API at ${url}. Check VITE_API_BASE_URL and backend CORS/status.`);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Auth API error (${response.status}) from ${url}.`);
  return payload as T;
}

export type BusinessProfile = {
  id: string;
  email: string;
  business_name: string;
  created_at: string;
  updated_at: string;
};

export async function requestSignupOtp({ businessName, email, password }: { businessName: string; email: string; password: string }) {
  const cleanBusinessName = businessName.trim();
  const cleanEmail = email.trim().toLowerCase();
  if (cleanBusinessName.length < 2) throw new Error('Business name must contain at least 2 characters.');
  if (password.length < 8) throw new Error('Password must contain at least 8 characters.');
  await backendAuth('/request-otp', { email: cleanEmail, business_name: cleanBusinessName, password });
}

export async function verifySignupOtp({ businessName, email, password, code }: { businessName: string; email: string; password: string; code: string }) {
  ensureSupabaseConfigured();
  const cleanEmail = email.trim().toLowerCase();
  await backendAuth('/verify-otp', { email: cleanEmail, code: code.trim(), business_name: businessName.trim(), password });
  const { data, error } = await supabase.auth.signInWithPassword({ email: cleanEmail, password });
  if (error) throw error;
  return data;
}

export async function loginBusinessOwner({ email, password }: { email: string; password: string }) {
  ensureSupabaseConfigured();
  const { data, error } = await supabase.auth.signInWithPassword({ email: email.trim().toLowerCase(), password });
  if (error) throw error;
  return data;
}

export async function logoutBusinessOwner() {
  const { error } = await supabase.auth.signOut();
  if (error) throw error;
}

export async function getCurrentBusinessProfile() {
  const { data: userData, error: userError } = await supabase.auth.getUser();
  if (userError) throw userError;
  if (!userData.user) return null;
  const { data, error } = await supabase.from('business_profiles').select('*').eq('id', userData.user.id).single();
  if (error) throw error;
  return data as BusinessProfile;
}

export async function updateBusinessProfile({ businessName, email }: { businessName: string; email: string }) {
  const cleanBusinessName = businessName.trim();
  const cleanEmail = email.trim().toLowerCase();
  const { data: userData, error: userError } = await supabase.auth.getUser();
  if (userError) throw userError;
  if (!userData.user) throw new Error('You are not logged in.');
  if (cleanBusinessName.length < 2) throw new Error('Business name must contain at least 2 characters.');

  const { error: metadataError } = await supabase.auth.updateUser({ data: { business_name: cleanBusinessName } });
  if (metadataError) throw metadataError;

  if (cleanEmail !== (userData.user.email || '').toLowerCase()) {
    const { data: sessionData } = await supabase.auth.getSession();
    const token = sessionData.session?.access_token;
    if (!token) throw new Error('Sign in again to change your email.');
    await backendAuth('/change-email', { email: cleanEmail }, token);
  }

  const { data, error } = await supabase.from('business_profiles').update({ business_name: cleanBusinessName, email: cleanEmail }).eq('id', userData.user.id).select().single();
  if (error) throw error;
  return data as BusinessProfile;
}

export async function requestPasswordReset(email: string) {
  const { error } = await supabase.auth.resetPasswordForEmail(email.trim().toLowerCase(), { redirectTo: `${window.location.origin}/reset-password` });
  if (error) throw error;
}
