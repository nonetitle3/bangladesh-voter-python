import React, { useEffect, useState } from 'react';

const API = (process.env.REACT_APP_API_URL || 'https://bangladesh-voter-python.onrender.com/api').replace(/\/$/, '');
const BASE = '/bangladesh-voter-python';

function getToken() {
  return localStorage.getItem('admin_token') || '';
}

function go(path) {
  window.history.pushState({}, '', `${BASE}${path}`);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

async function readJson(response) {
  const text = await response.text();
  try { return text ? JSON.parse(text) : {}; }
  catch { throw new Error(`Server returned non-JSON response (${response.status})`); }
}

function Login({ onLogin }) {
  const [u, setU] = useState('');
  const [p, setP] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async e => {
    e.preventDefault(); setLoading(true); setErr('');
    try {
      const r = await fetch(`${API}/admin/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: u.trim(), password: p })
      });
      const d = await readJson(r);
      if (!r.ok) throw new Error(d.detail || d.message || 'Login failed');
      const token = d.token || d.access_token;
      if (!token) throw new Error('Login succeeded but no authentication token was returned.');
      localStorage.setItem('admin_token', token);
      onLogin(token);
    } catch (e) { setErr(e.message || 'Login failed'); }
    finally { setLoading(false); }
  };

  return <main className="container" style={{maxWidth:520, margin:'60px auto'}}>
    <section className="panel">
      <h2>🔐 Admin Login</h2>
      {err && <div className="error">⚠️ {err}</div>}
      <form onSubmit={submit}>
        <div className="field"><label>Username</label><input value={u} onChange={e=>setU(e.target.value)} required autoComplete="username" /></div>
        <div className="field" style={{marginTop:12}}><label>Password</label><input type="password" value={p} onChange={e=>setP(e.target.value)} required autoComplete="current-password" /></div>
        <button className="primary" style={{marginTop:16,width:'100%'}} disabled={loading}>{loading ? 'Login হচ্ছে...' : 'Login'}</button>
      </form>
    </section>
  </main>;
}

function Search({ token }) {
  const filters = ['name','father_name','mother_name','voter_id','district','upazila','union_name','ward','occupation','gender'];
  const labels = {name:'নাম',father_name:'পিতার নাম',mother_name:'মাতার নাম',voter_id:'NID / Voter ID',district:'জেলা',upazila:'উপজেলা',union_name:'ইউনিয়ন',ward:'ওয়ার্ড',occupation:'পেশা',gender:'লিঙ্গ'};
  const empty = () => Object.fromEntries(filters.map(x => [x, '']));
  const [f,setF] = useState(empty()), [rows,setRows] = useState([]), [total,setTotal] = useState(0), [loading,setLoading] = useState(false), [searched,setSearched] = useState(false), [err,setErr] = useState('');

  const search = async () => {
    if (!token) { setErr('Authentication required. Please login again.'); return; }
    setLoading(true); setErr('');
    try {
      const q = new URLSearchParams(); Object.entries(f).forEach(([k,v]) => v && q.set(k,v)); q.set('page_size','100');
      const r = await fetch(`${API}/voter-search/search?${q.toString()}`, {headers:{Authorization:`Bearer ${token}`}});
      const d = await readJson(r);
      if (r.status === 401) { localStorage.removeItem('admin_token'); throw new Error('Session expired. Please login again.'); }
      if (!r.ok) throw new Error(d.detail || d.message || 'Search failed');
      const records = Array.isArray(d) ? d : (d.records || d.results || d.items || []);
      setRows(records); setTotal(Number(d.total_count ?? d.total ?? records.length) || 0); setSearched(true);
    } catch(e) { setErr(e.message || 'Search failed'); }
    finally { setLoading(false); }
  };

  const reset = () => { setF(empty()); setRows([]); setTotal(0); setSearched(false); setErr(''); };

  const exportFile = async type => {
    if (!token) { setErr('Authentication required.'); return; }
    try {
      const q = new URLSearchParams(); Object.entries(f).forEach(([k,v]) => v && q.set(k,v));
      const r = await fetch(`${API}/voter-search/export/${type}?${q.toString()}`, {headers:{Authorization:`Bearer ${token}`}});
      if (!r.ok) { const d = await readJson(r); throw new Error(d.detail || d.message || `Export failed (${r.status})`); }
      const blob = await r.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a');
      a.href = url; a.download = `voter-search.${type === 'xlsx' ? 'xlsx' : 'csv'}`; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch(e) { setErr(e.message || 'Export failed'); }
  };

  return <section className="panel">
    <h2>🔎 ভোটার তথ্য অনুসন্ধান</h2><p>আপলোড করা PDF থেকে extracted voter records খুঁজুন।</p>
    {err && <div className="error">⚠️ {err}</div>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(210px,1fr))',gap:12}}>
      {filters.map(k=><div className="field" key={k}><label>{labels[k]}</label><input value={f[k]} onChange={e=>setF({...f,[k]:e.target.value})} onKeyDown={e=>e.key==='Enter'&&search()} /></div>)}
    </div>
    <div style={{display:'flex',gap:10,marginTop:16,flexWrap:'wrap'}}>
      <button className="primary" onClick={search} disabled={loading}>{loading?'Searching...':'🔎 Search'}</button>
      <button className="secondary" onClick={reset}>Reset</button>
      {searched && <><button className="secondary" onClick={()=>exportFile('csv')}>📥 CSV</button><button className="secondary" onClick={()=>exportFile('xlsx')}>📗 Excel</button></>}
    </div>
    {searched && <div style={{marginTop:18}}><h3>Results: {total}</h3>{rows.length===0?<p>কোনো record পাওয়া যায়নি।</p>:<div style={{overflowX:'auto'}}><table style={{width:'100%',borderCollapse:'collapse'}}><thead><tr><th>#</th><th>NID/Voter ID</th><th>নাম</th><th>পিতার নাম</th><th>মাতার নাম</th><th>জন্মতারিখ</th><th>লিঙ্গ</th><th>ঠিকানা</th><th>জেলা</th><th>উপজেলা</th><th>ইউনিয়ন</th><th>PDF</th><th>Page</th></tr></thead><tbody>{rows.map((r,i)=><tr key={r.id||i}><td>{i+1}</td><td>{r.voter_id||r.nid||'-'}</td><td>{r.name||'-'}</td><td>{r.father_name||'-'}</td><td>{r.mother_name||'-'}</td><td>{r.birth_date||'-'}</td><td>{r.gender||'-'}</td><td>{r.address||'-'}</td><td>{r.district||'-'}</td><td>{r.upazila||'-'}</td><td>{r.union_name||'-'}</td><td>{r.pdf_filename||r.filename||'-'}</td><td>{r.page_number||r.page||'-'}</td></tr>)}</tbody></table></div>}</div>}
  </section>;
}

function Admin({onLogout}) {
  const [token,setToken] = useState(getToken()), [files,setFiles] = useState([]), [docs,setDocs] = useState([]), [message,setMessage] = useState(''), [error,setError] = useState(''), [loading,setLoading] = useState(false);
  const headers = () => token ? {Authorization:`Bearer ${token}`} : {};
  const loadDocs = async () => {
    try { const r=await fetch(`${API}/admin/documents`,{headers:headers()}); const d=await readJson(r); if(r.status===401){logout();throw new Error('Session expired.');} if(!r.ok)throw new Error(d.detail||d.message||'Could not load documents'); setDocs(Array.isArray(d)?d:(d.documents||d.items||[])); }
    catch(e){setError(e.message);}
  };
  useEffect(()=>{if(token)loadDocs();},[token]);
  const logout=()=>{localStorage.removeItem('admin_token');setToken('');setDocs([]);onLogout();};
  const upload=async()=>{
    if(!files.length)return; setLoading(true); setError(''); setMessage('PDF processing চলছে...');
    try { const fd=new FormData(); [...files].forEach(x=>fd.append('files',x)); const r=await fetch(`${API}/admin/upload-pdf`,{method:'POST',headers:headers(),body:fd}); const d=await readJson(r); if(r.status===401){logout();throw new Error('Session expired.');} if(!r.ok)throw new Error(d.detail||d.message||'Upload failed'); setMessage('Upload completed');setFiles([]);const input=document.getElementById('pdfFiles');if(input)input.value='';await loadDocs(); }
    catch(e){setError(e.message);}
    finally{setLoading(false);}
  };
  if(!token)return <Login onLogin={t=>{setToken(t);go('/admin');}}/>;
  return <main className="container">
    <div className="panel"><div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12}}><div><h2>🛠️ Admin Dashboard</h2><p>PDF upload, OCR processing, document management এবং voter search</p></div><button className="secondary" onClick={logout}>Logout</button></div>
      {error&&<div className="error">⚠️ {error}</div>}{message&&<div style={{padding:12,margin:'12px 0',borderRadius:8,background:'#e8f5e9'}}>{message}</div>}
      <div className="field"><label>PDF files</label><input id="pdfFiles" type="file" accept="application/pdf,.pdf" multiple onChange={e=>setFiles(e.target.files)} /></div>
      <button className="primary" style={{marginTop:12}} onClick={upload} disabled={loading||!files.length}>{loading?'Processing...':'📤 Upload PDF'}</button>
    </div>
    <Search token={token}/>
    <section className="panel"><div style={{display:'flex',justifyContent:'space-between'}}><h3>📄 Documents</h3><button className="secondary" onClick={loadDocs}>Refresh</button></div>{docs.length===0?<p>No documents uploaded yet.</p>:<div style={{overflowX:'auto'}}><table style={{width:'100%',borderCollapse:'collapse'}}><thead><tr><th>File</th><th>Status</th><th>Pages</th><th>OCR</th><th>Error details</th></tr></thead><tbody>{docs.map(d=><tr key={d.id}><td>{d.filename}</td><td>{d.status}</td><td>{d.page_count||'-'}</td><td>{d.ocr_used?'Yes':'No'}</td><td style={{maxWidth:500,whiteSpace:'pre-wrap',wordBreak:'break-word'}}>{d.error_msg||'-'}</td></tr>)}</tbody></table></div>}</div>
  </main>;
}

function Home(){return <main className="container"><section className="panel"><h1>🇧🇩 Bangladesh Voter Search</h1><p>Voter information search system</p><button className="primary" onClick={()=>go('/admin')}>{getToken()?'Open Admin Dashboard →':'🔐 Admin Login →'}</button></section></main>;}

export default function App(){
  const [path,setPath]=useState(window.location.pathname);
  useEffect(()=>{const handler=()=>setPath(window.location.pathname);window.addEventListener('popstate',handler);return()=>window.removeEventListener('popstate',handler);},[]);
  const isAdmin=path===BASE || path===`${BASE}/` || path.startsWith(`${BASE}/admin`);
  return isAdmin ? <Admin onLogout={()=>go('/admin')}/> : <Home/>;
}
