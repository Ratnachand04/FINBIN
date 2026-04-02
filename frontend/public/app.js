const API_BASE = window.location.protocol + "//" + window.location.hostname + ":" + window.location.port + "/api/v1";

let currentCoin = "BTC";
let jwtToken = localStorage.getItem("binfin_jwt");

// View Management
function switchAuthTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.auth-form').forEach(form => form.classList.remove('active'));
    
    event.target.classList.add('active');
    document.getElementById(`${tabName}-form`).classList.add('active');
}

function switchView(viewName) {
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
    
    event.currentTarget.classList.add('active');
    document.getElementById(`view-${viewName}`).classList.add('active');
    
    if (viewName === 'charts') {
        setTimeout(initChartOrUpdate, 100);
    }
    if (viewName === 'settings') loadKeys();
}

// Authentication Logic
async function handleAuth(e, type) {
    e.preventDefault();
    const email = document.getElementById(`${type}-email`).value;
    const password = document.getElementById(`${type}-password`).value;
    
    try {
        if (type === 'login') {
            const params = new URLSearchParams();
            params.append('username', email);
            params.append('password', password);
            
            const res = await fetch(`${API_BASE}/auth/access-token`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: params
            });
            
            if (res.ok) {
                const data = await res.json();
                jwtToken = data.access_token;
                localStorage.setItem("binfin_jwt", jwtToken);
                bootstrapApp();
            } else {
                document.getElementById('login-error').innerText = "Invalid credentials";
            }
        } else {
            const res = await fetch(`${API_BASE}/auth/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });

            if (res.ok) {
                document.getElementById('register-message').innerText = "Success! Please switch to login.";
            } else {
                const data = await res.json();
                document.getElementById('register-message').innerText = data.detail || "Registration failed";
                document.getElementById('register-message').style.color = "var(--danger)";
            }
        }
    } catch (err) {
        console.error("Auth error", err);
    }
}

function logout() {
    localStorage.removeItem("binfin_jwt");
    jwtToken = null;
    document.getElementById('app-container').style.display = 'none';
    document.getElementById('auth-overlay').classList.add('active');
}

// API Helpers
async function secureFetch(path, options = {}) {
    if (!jwtToken) return null;
    const headers = { 
        ...options.headers, 
        'Authorization': `Bearer ${jwtToken}`
    };
    if (!(options.body instanceof FormData) && !headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
    }
    
    try {
        const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
        if (res.status === 401 || res.status === 403) {
            logout();
            return null;
        }
        return res;
    } catch (e) {
        console.error("Fetch failure:", e);
        return null;
    }
}

// Initialization Flow
async function bootstrapApp() {
    if (!jwtToken) {
        document.getElementById('auth-overlay').classList.add('active');
        return;
    }
    
    // Verify token & Get user
    const res = await secureFetch('/auth/me');
    if (!res || !res.ok) {
        logout();
        return;
    }
    const user = await res.json();
    document.getElementById('user-email-display').innerText = user.email;
    
    // UI Transitions
    document.getElementById('auth-overlay').classList.remove('active');
    document.getElementById('app-container').style.display = 'flex';
    
    // Initial fetch loops
    refreshDashboard();
    setInterval(refreshDashboard, 15000);
}

// Dashboard Hydration
async function refreshDashboard() {
    // Check Health
    const healthReq = await secureFetch('/health/');
    const statusEl = document.getElementById('sys-status');
    const indicator = document.querySelector('.status-indicator');
    
    if (healthReq && healthReq.ok) {
        statusEl.innerText = "ONLINE & SYNCED";
        indicator.className = "status-indicator online";
    } else {
        statusEl.innerText = "DEGRADED / OFFLINE";
        indicator.className = "status-indicator offline";
    }

    // Active Signals
    const activeReq = await secureFetch('/signals/active?limit=5');
    if (activeReq && activeReq.ok) {
        const active = await activeReq.json();
        const dataArray = Array.isArray(active) ? active : [];
        document.getElementById('metric-signals').innerText = dataArray.length;
        
        const tbody = document.querySelector('#recent-signals-table tbody');
        tbody.innerHTML = '';
        dataArray.forEach(sig => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${sig.symbol}</strong></td>
                <td style="color: ${sig.signal.toUpperCase() === 'BUY' ? 'var(--success)' : 'var(--danger)'}">${sig.signal}</td>
                <td>${(sig.confidence * 100).toFixed(1)}%</td>
                <td>${new Date(sig.ts).toLocaleTimeString()}</td>
            `;
            tbody.appendChild(tr);
        });
        if(dataArray.length === 0) tbody.innerHTML = "<tr><td colspan='4'>No active signals</td></tr>";
    }

    // Performance (mock calculation based on endpoint)
    const perfReq = await secureFetch('/signals/performance?group_by=coin');
    if (perfReq && perfReq.ok) {
        const p = await perfReq.json();
        if(p && p.rows && p.rows.length>0) {
            document.getElementById('metric-winrate').innerText = (p.rows[0].win_rate * 100).toFixed(1) + "%";
        }
    }
}

// Native Chartting Logic (LightweightCharts)
let chart = null;
let candleSeries = null;

function handleCoinChange() {
    currentCoin = document.getElementById('global-coin-select').value;
    if (document.getElementById('view-charts').classList.contains('active')) {
        initChartOrUpdate();
    }
}

async function initChartOrUpdate() {
    const container = document.getElementById('tvchart');
    if (!container) return;

    if (!chart) {
        chart = LightweightCharts.createChart(container, {
            layout: { backgroundColor: 'transparent', textColor: '#d1d4dc' },
            grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
            timeScale: { timeVisible: true, secondsVisible: false }
        });
        candleSeries = chart.addCandlestickSeries({
            upColor: '#3fb950', downColor: '#f85149',
            borderVisible: false, wickUpColor: '#3fb950', wickDownColor: '#f85149'
        });
        
        // Handle resize inherently
        new ResizeObserver(entries => {
            if(entries.length === 0 || entries[0].target !== container) return;
            const newRect = entries[0].contentRect;
            chart.applyOptions({ height: newRect.height, width: newRect.width });
        }).observe(container);
    }

    // Fetch Custom Data Native
    const res = await secureFetch(`/coins/${currentCoin}/price-history?interval=15m&limit=500`);
    if (res && res.ok) {
        const data = await res.json();
        // Assuming Data array [ {ts, open, high, low, close} ]
        let formattedData = data.map(d => ({
            time: new Date(d.ts).getTime() / 1000, 
            open: parseFloat(d.open), 
            high: parseFloat(d.high), 
            low: parseFloat(d.low), 
            close: parseFloat(d.close)
        })).sort((a,b) => a.time - b.time);
        
        candleSeries.setData(formattedData);
    }
}

// Settings / Keys
async function saveApiKey(e) {
    e.preventDefault();
    const provider = document.getElementById('key-provider').value;
    const api_key = document.getElementById('key-value').value;
    const api_secret = document.getElementById('key-secret').value;
    
    const res = await secureFetch('/keys/', {
        method: 'POST',
        body: JSON.stringify({ provider, api_key, api_secret })
    });
    if (res && res.ok) {
        alert("Key saved securely!");
        document.getElementById('key-value').value = '';
        document.getElementById('key-secret').value = '';
        loadKeys();
    }
}

async function loadKeys() {
    const res = await secureFetch('/keys/');
    if (res && res.ok) {
        const keys = await res.json();
        const list = document.getElementById('keys-list');
        list.innerHTML = '';
        keys.forEach(k => {
            list.innerHTML += `<li style="margin-bottom:8px; padding:8px; background:rgba(0,0,0,0.3); border-radius:6px; display:flex; justify-content:space-between;">
                <span>${k.provider}</span>
                <span class="material-symbols-outlined" style="color:var(--danger); cursor:pointer; font-size:18px;" onclick="deleteKey('${k.provider}')">delete</span>
            </li>`;
        });
    }
}

async function deleteKey(provider) {
    if(confirm(`Remove ${provider} integration?`)){
        const res = await secureFetch(`/keys/${provider}`, { method: 'DELETE' });
        if (res && res.ok) loadKeys();
    }
}

// Model Training Op
async function triggerModelTraining() {
    const term = document.getElementById('training-output');
    term.innerHTML += `<br>> [${new Date().toLocaleTimeString()}] Triggering QLoRA Pipeline via Celery Workers...`;
    
    const res = await secureFetch('/model/train-finance-news', {
        method: 'POST',
        body: JSON.stringify({ symbols: [currentCoin], interval: "1d", max_rows_per_symbol: 5000, sentiment_sample_size: 30 })
    });
    
    if (res && res.ok) {
        term.innerHTML += `<br>> [${new Date().toLocaleTimeString()}] <span style="color:var(--success)">Training accepted successfully. Check backend Celery logs for adapter compilation context.</span>`;
    } else {
        term.innerHTML += `<br>> [${new Date().toLocaleTimeString()}] <span style="color:var(--danger)">Error contacting training node. Ensure worker container is online.</span>`;
    }
}


// Start
window.onload = bootstrapApp;
