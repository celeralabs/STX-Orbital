// STX Orbital - Main JavaScript Module
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
            if (data.status === "suppressed") {
                this.showAllClear(data.message);
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
        title.textContent = 'Running High-Fidelity Conjunction Screening';
        content.innerHTML = `<p>Propagating orbits with SGP4 • Fetching live catalog • Computing Pc & RIC geometry <span class="loading"></span></p>`;
    },

    showAllClear(message) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        document.getElementById('recommendationBox').style.display = 'none';

        badge.className = 'result-status status-clear';
        badge.textContent = 'ALL CLEAR';
        title.textContent = 'No Actionable Conjunctions';
        content.innerHTML = `<p style="color:var(--success);font-size:1.1em;">${message}</p>`;
    },

    showThreats(data) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');
        const recommendationList = document.getElementById('recommendationList');

        // Determine overall risk color
        const hasRed = data.threats.some(t => t.risk_level === 'RED');
        badge.className = hasRed ? 'result-status status-threat' : 'result-status status-processing';
        badge.textContent = hasRed ? 'CRITICAL CONJUNCTION(S)' : 'YELLOW ALERT';
        title.textContent = `Screening Complete – ${data.threats.length} Threat(s) Found`;

        // Build threat cards
        let threatsHTML = data.threats.map(t => `
            <div style="margin:24px 0; padding:20px; background:var(--surface-light); border-radius:8px; border-left:5px solid ${t.risk_level==='RED'?'var(--danger)':'var(--primary)'}">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <strong style="font-size:1.2em;">${t.asset}</strong>
                    <span style="background:${t.risk_level==='RED'?'var(--danger)':'var(--primary)'}; color:#000; padding:4px 12px; border-radius:4px; font-size:0.8em; font-weight:bold;">
                        ${t.risk_level}
                    </span>
                </div>
                <div style="margin-bottom:8 negativity;">
                    <strong>Intruder:</strong> <span style="color:${t.risk_level==='RED'?'var(--danger)':''}">${t.intruder}</span>
                </div>
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
        `).join('');

        content.innerHTML = threatsHTML;

        // Show AI decision for the highest-risk event
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