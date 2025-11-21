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

        ['dragenter', 'dragover'].forEach(e => dropzone.addEventListener(e, (evt) => { evt.preventDefault(); dropzone.classList.add('dragover'); }));
        ['dragleave', 'drop'].forEach(e => dropzone.addEventListener(e, (evt) => { evt.preventDefault(); dropzone.classList.remove('dragover'); }));

        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            if(e.dataTransfer.files.length) this.processFile(e.dataTransfer.files[0]);
        });
        fileInput.addEventListener('change', (e) => {
            if(e.target.files.length) this.processFile(e.target.files[0]);
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
            if (r.status === 401) throw new Error("UNAUTHORIZED: Enterprise License Required");
            return r.json();
        })
        .then(data => {
            if (data.error) this.showError(data.error);
            else this.showThreats(data);
        })
        .catch(e => this.showError(e.message));
    },

    showProcessing() {
        const resultContainer = document.getElementById('resultContainer');
        const statusBadge = document.getElementById('statusBadge');
        const resultTitle = document.getElementById('resultTitle');
        const resultContent = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');

        resultContainer.style.display = 'block';
        statusBadge.className = 'result-status status-processing';
        statusBadge.textContent = 'Processing';
        resultTitle.textContent = 'Running Autonomous Conjunction Assessment';
        resultContent.innerHTML = `<p>Propagating orbits... <span class="loading"></span></p>`;
        recommendationBox.style.display = 'none';
    },

    showThreats(data) {
        const threat = data.threats[0];
        const statusBadge = document.getElementById('statusBadge');
        const resultTitle = document.getElementById('resultTitle');
        const resultContent = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');
        const recommendationList = document.getElementById('recommendationList');

        statusBadge.className = 'result-status status-threat';
        statusBadge.textContent = 'CRITICAL CONJUNCTION';
        resultTitle.textContent = 'LIVE SCREENING COMPLETE';
        
        resultContent.innerHTML = `
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div style="background: var(--surface-light); padding: 10px; border: 1px solid var(--primary);">
                    <div style="font-size: 12px; color: var(--text-muted);">PRIMARY</div>
                    <div style="font-weight: 700;">${threat.asset}</div>
                </div>
                <div style="background: var(--surface-light); padding: 10px; border: 1px solid var(--danger);">
                    <div style="font-size: 12px; color: var(--text-muted);">INTRUDER</div>
                    <div style="font-weight: 700; color: var(--danger);">${threat.intruder}</div>
                </div>
            </div>
            <p style="margin-top: 10px;">Miss Distance: <strong>${threat.min_km} km</strong></p>
            <p>TCA: ${threat.tca}</p>
            <a href="${threat.pdf_url}" target="_blank" class="btn btn-primary" style="margin-top:15px; width:100%; text-align:center;">Download PDF Report</a>
        `;

        recommendationBox.style.display = 'block';
        recommendationList.innerHTML = `<pre style="white-space: pre-wrap; font-family: inherit; color: var(--text-muted);">${data.decision}</pre>`;
    },

    showError(msg) {
        const statusBadge = document.getElementById('statusBadge');
        const resultContent = document.getElementById('resultContent');
        statusBadge.className = 'result-status status-threat';
        statusBadge.textContent = 'Error';
        resultContent.innerHTML = `<p style="color: var(--danger);">${msg}</p>`;
    }
};

const PageRouter = {
    init() {
        const path = window.location.pathname;
        Navigation.init();
        
        // Handle routing logic
        if (path.includes('login.html')) {
            LoginPage.init();
        } else if (path.includes('dashboard.html')) {
            DashboardPage.init();
        }
    }
};

const Navigation = {
    init() {
        const toggle = document.querySelector('.nav-toggle');
        const menu = document.querySelector('.nav-menu');
        if (toggle && menu) toggle.addEventListener('click', () => menu.classList.toggle('active'));
    }
};

// Login Page specific logic
const LoginPage = {
    init() {
        Auth.redirectIfAuthenticated();
        const form = document.getElementById('loginForm');
        if(form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                const code = document.getElementById('accessCode').value;
                const err = document.getElementById('errorMessage');
                if(Auth.authenticate(code)) window.location.href = 'dashboard.html';
                else { err.textContent = 'Invalid Access Code'; err.style.display = 'block'; }
            });
        }
    }
};

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => PageRouter.init());
else PageRouter.init();