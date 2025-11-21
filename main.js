// STX Orbital - Main JavaScript Module (Tiered Screening)
const Auth = {
    isAuthenticated() { return sessionStorage.getItem('stx_auth') === 'true'; },
    authenticate(code) {
        if (code === 'stx2025') {
            sessionStorage.setItem('stx_auth', 'true');
            return true;
        }
        return false;
    },
    logout() {
        sessionStorage.removeItem('stx_auth');
        window.location.href = 'login.html';
    },
    requireAuth() {
        if (!this.isAuthenticated()) window.location.href = 'login.html';
    },
    redirectIfAuthenticated() {
        if (this.isAuthenticated()) window.location.href = 'dashboard.html';
    }
};

const DashboardPage = {
    init() {
        Auth.requireAuth();
        const logoutBtn = document.getElementById('logoutBtn');
        if (logoutBtn) logoutBtn.addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });
        this.setupFileUpload();
    },

    setupFileUpload() {
        const dropzone = document.getElementById('dropzone');
        const fileInput = document.getElementById('fileInput');
        if (!dropzone || !fileInput) return;

        ['dragenter', 'dragover'].forEach(e => dropzone.addEventListener(e, (evt) => {
            evt.preventDefault(); dropzone.classList.add('dragover');
        }));
        ['dragleave', 'drop'].forEach(e => dropzone.addEventListener(e, (evt) => {
            evt.preventDefault(); dropzone.classList.remove('dragover');
        }));

        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            if (e.dataTransfer.files.length) this.processFile(e.dataTransfer.files[0]);
        });
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) this.processFile(e.target.files[0]);
        });
    },

    processFile(file) {
        this.showProcessing();
        const formData = new FormData();
        formData.append('file', file);
        // Add catalog_limit if needed (default 5000)
        // formData.append('catalog_limit', '10000');

        fetch('/screen', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer stx-authorized-user' },
            body: formData
        })
        .then(r => {
            if (!r.ok) {
                if (r.status === 401) throw new Error("Unauthorized – Enterprise license required");
                return r.json().then(data => { throw new Error(data.error || "Server error") });
            }
            return r.json();
        })
        .then(data => {
            if (data.status === "all_clear") {
                this.showAllClear(data.message, data.screening_stats);
            } else if (data.threats && data.threats.length > 0) {
                this.showThreats(data);
            } else {
                this.showAllClear("No conjunctions found above reporting threshold.");
            }
        })
        .catch(err => this.showError(err.message || "Upload failed"));
    },

    showProcessing() {
        const container = document.getElementById('resultContainer');
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');

        container.style.display = 'block';
        badge.className = 'result-status status-processing';
        badge.textContent = 'ANALYZING';
        title.textContent = 'Running Tiered Conjunction Screening';
        content.innerHTML = `<p>
            Tier 1: Checking manned assets (ISS, Tiangong)<br>
            Tier 2: Checking high-risk objects (decay, unstable)<br>
            Tier 3: Catalog sweep<br>
            <span class="loading"></span>
        </p>`;
    },

    showAllClear(message, stats) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        document.getElementById('recommendationBox').style.display = 'none';

        badge.className = 'result-status status-clear';
        badge.textContent = 'ALL CLEAR';
        title.textContent = 'No Actionable Conjunctions';
        
        let statsHTML = '';
        if (stats) {
            statsHTML = `
                <div style="margin-top:20px; padding:16px; background:var(--surface-light); border-radius:4px; font-size:0.9em;">
                    <strong>Screening Statistics:</strong><br>
                    Manned assets checked: ${stats.manned_checked}<br>
                    High-risk objects checked: ${stats.high_risk_checked}<br>
                    Catalog objects checked: ${stats.catalog_checked}<br>
                    Total time: ${stats.total_time_sec}s
                </div>
            `;
        }
        
        content.innerHTML = `<p style="color:var(--success);font-size:1.1em;">${message}</p>${statsHTML}`;
    },

    showThreats(data) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');
        const recommendationList = document.getElementById('recommendationList');

        // Determine overall risk color
        const hasRed = data.threats.some(t => t.risk_level === 'RED');
        const hasManned = data.threats.some(t => t.priority === 'MANNED');
        
        badge.className = hasRed ? 'result-status status-threat' : 'result-status status-processing';
        badge.textContent = hasManned ? 'MANNED ASSET CONJUNCTION' : (hasRed ? 'CRITICAL CONJUNCTION(S)' : 'YELLOW ALERT');
        title.textContent = `Screening Complete – ${data.threats.length} Threat(s) Found`;

        // Build threat cards with priority indicators
        let threatsHTML = data.threats.map(t => {
            // Priority badge colors
            const priorityColors = {
                'MANNED': 'background:#ff0000; color:#fff;',
                'HIGH-RISK': 'background:#ff8800; color:#fff;',
                'CATALOG': 'background:#00d9ff; color:#000;'
            };
            const priorityStyle = priorityColors[t.priority] || priorityColors['CATALOG'];
            
            // Risk level border
            const borderColor = t.risk_level === 'RED' ? 'var(--danger)' : 'var(--primary)';
            
            return `
                <div style="margin:24px 0; padding:20px; background:var(--surface-light); border-radius:8px; border-left:5px solid ${borderColor}">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                        <strong style="font-size:1.2em;">${t.asset}</strong>
                        <div style="display:flex; gap:8px;">
                            <span style="${priorityStyle} padding:4px 12px; border-radius:4px; font-size:0.75em; font-weight:bold; text-transform:uppercase;">
                                ${t.priority}
                            </span>
                            <span style="background:${t.risk_level==='RED'?'var(--danger)':'var(--primary)'}; color:${t.risk_level==='RED'?'#fff':'#000'}; padding:4px 12px; border-radius:4px; font-size:0.75em; font-weight:bold;">
                                ${t.risk_level}
                            </span>
                        </div>
                    </div>
                    <div style="margin-bottom:8px;">
                        <strong>Intruder:</strong> <span style="color:${t.risk_level==='RED'?'var(--danger)':''}">${t.intruder}</span>
                    </div>
                    ${t.priority_reason ? `
                        <div style="margin-bottom:12px; padding:8px; background:rgba(255,255,255,0.05); border-radius:4px; font-size:0.85em; font-style:italic;">
                            ${t.priority_reason}
                        </div>
                    ` : ''}
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:12px 0; font-size:0.95em;">
                        <div><strong>Miss Distance:</strong> ${t.min_km} km</div>
                        <div><strong>TCA:</strong> ${t.tca}</div>
                        <div><strong>Rel Velocity:</strong> ${t.relative_velocity_kms} km/s</div>
                        <div><strong>Pc:</strong> ${t.pc}</div>
                    </div>
                    <a href="${t.pdf_url}" target="_blank" class="btn btn-primary" style="display:inline-block; margin-top:8px; padding:10px 20px; font-size:0.9em;">
                        Download PDF Report
                    </a>
                </div>
            `;
        }).join('');

        // Add screening statistics
        if (data.screening_stats) {
            const stats = data.screening_stats;
            threatsHTML += `
                <div style="margin:24px 0; padding:20px; background:var(--surface-light); border-radius:8px; border:1px solid var(--border);">
                    <h3 style="color:var(--primary); margin-bottom:12px; font-size:1em;">Screening Statistics</h3>
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; font-size:0.9em;">
                        <div><strong>Manned Assets:</strong> ${stats.manned_checked}</div>
                        <div><strong>High-Risk Objects:</strong> ${stats.high_risk_checked}</div>
                        <div><strong>Catalog Objects:</strong> ${stats.catalog_checked}</div>
                        <div><strong>Total Time:</strong> ${stats.total_time_sec}s</div>
                    </div>
                </div>
            `;
        }

        content.innerHTML = threatsHTML;

        // Show AI decision for highest-priority event
        recommendationBox.style.display = 'block';
        recommendationList.innerHTML = `<pre style="white-space: pre-wrap; background:transparent; border:none; padding:0; color:var(--text-muted);">${data.decision}</pre>`;
    },

    showError(msg) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        document.getElementById('recommendationBox').style.display = 'none';

        badge.className = 'result-status status-threat';
        badge.textContent = 'ERROR';
        title.textContent = 'Screening Failed';
        content.innerHTML = `<p style="color:var(--danger);">${msg}</p>`;
    }
};

// Routing & Nav
const PageRouter = {
    init() {
        Navigation.init();
        const path = window.location.pathname;
        if (path.includes('login.html')) LoginPage.init();
        else if (path.includes('dashboard.html')) DashboardPage.init();
    }
};

const Navigation = {
    init() {
        const toggle = document.querySelector('.nav-toggle');
        const menu = document.querySelector('.nav-menu');
        if (toggle && menu) toggle.addEventListener('click', () => menu.classList.toggle('active'));
    }
};

const LoginPage = {
    init() {
        Auth.redirectIfAuthenticated();
        const form = document.getElementById('loginForm');
        if (form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                const code = document.getElementById('accessCode').value.trim();
                const err = document.getElementById('errorMessage');
                if (Auth.authenticate(code)) {
                    window.location.href = 'dashboard.html';
                } else {
                    err.textContent = 'Invalid Access Code';
                    err.style.display = 'block';
                }
            });
        }
    }
};

// Start the app
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => PageRouter.init());
} else {
    PageRouter.init();
}