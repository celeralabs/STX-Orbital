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
    }
};

const PageRouter = {
    init() {
        const path = window.location.pathname;
        if (path.endsWith('login.html')) {
            LoginPage.init();
        } else if (path.endsWith('dashboard.html')) {
            DashboardPage.init();
        } else {
            LandingPage.init();
        }
    }
};

const LandingPage = {
    init() {
        // Mobile nav toggle
        const navToggle = document.querySelector('.nav-toggle');
        const navMenu = document.querySelector('.nav-menu');
        
        if (navToggle && navMenu) {
            navToggle.addEventListener('click', () => {
                navMenu.classList.toggle('active');
            });
            
            // Close menu when clicking a link
            const navLinks = document.querySelectorAll('.nav-menu a');
            navLinks.forEach(link => {
                link.addEventListener('click', () => {
                    navMenu.classList.remove('active');
                });
            });
        }
        
        const cta = document.getElementById('ctaButton');
        if (cta) {
            cta.addEventListener('click', (e) => {
                e.preventDefault();
                if (Auth.isAuthenticated()) {
                    window.location.href = 'dashboard.html';
                } else {
                    window.location.href = 'login.html';
                }
            });
        }
    }
};

const DashboardPage = {
    currentJobId: null,

    init() {
        Auth.requireAuth();
        const logoutBtn = document.getElementById('logoutBtn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', (e) => {
                e.preventDefault();
                Auth.logout();
            });
        }
        this.setupFileUpload();
        this.setupSummaryButton();
    },

    setupSummaryButton() {
        const btn = document.getElementById('summaryPdfBtn');
        if (!btn) return;
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            this.downloadSummaryPdf();
        });
    },

    setupFileUpload() {
        const dropzone = document.getElementById('dropzone');
        const fileInput = document.getElementById('fileInput');
        if (!dropzone || !fileInput) return;

        ['dragenter', 'dragover'].forEach(e =>
            dropzone.addEventListener(e, (evt) => {
                evt.preventDefault();
                dropzone.classList.add('dragover');
            })
        );
        ['dragleave', 'drop'].forEach(e =>
            dropzone.addEventListener(e, (evt) => {
                evt.preventDefault();
                dropzone.classList.remove('dragover');
            })
        );

        dropzone.addEventListener('drop', (e) => {
            if (e.dataTransfer.files.length) {
                this.processFile(e.dataTransfer.files[0]);
            }
        });

        dropzone.addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) {
                this.processFile(e.target.files[0]);
            }
        });
    },

    processFile(file) {
        this.currentJobId = null;
        this.showProcessing();

        const formData = new FormData();
        formData.append('file', file);
        // Optional override:
        // formData.append('catalog_limit', '5000');

        fetch('/screen', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer stx-authorized-user' },
            body: formData
        })
        .then(r => {
            if (!r.ok) {
                if (r.status === 401) throw new Error("Unauthorized – Enterprise license required");
                return r.json().then(data => { throw new Error(data.error || "Server error"); });
            }
            return r.json();
        })
        .then(data => {
            if (data.status === 'queued' && data.job_id) {
                this.currentJobId = data.job_id;
                this.pollJobStatus(data.job_id, 0);
            } else if (data.status === 'all_clear') {
                this.showAllClear(data.message, data.screening_stats);
            } else if (data.threats && data.threats.length > 0) {
                this.showThreats(data);
            } else {
                this.showAllClear("No conjunctions found above reporting threshold.");
            }
        })
        .catch(err => this.showError(err.message || "Upload failed"));
    },

    pollJobStatus(jobId, attempt) {
        const maxAttempts = 450; // ~15 minutes @ 2s
        if (attempt >= maxAttempts) {
            this.showError('Screening timed out. Try again or reduce catalog limit.');
            return;
        }

        fetch(`/screen_status/${jobId}`, {
            method: 'GET',
            headers: { 'Authorization': 'Bearer stx-authorized-user' }
        })
        .then(r => {
            if (!r.ok) {
                return r.json().then(d => { throw new Error(d.error || "Status check failed"); });
            }
            return r.json();
        })
        .then(data => {
            if (data.status === 'queued' || data.status === 'running') {
                setTimeout(() => this.pollJobStatus(jobId, attempt + 1), 2000);
            } else if (data.status === 'all_clear') {
                this.showAllClear(data.message, data.screening_stats);
            } else if (data.status === 'success' && data.threats && data.threats.length > 0) {
                this.showThreats(data);
            } else if (data.status === 'failed') {
                this.showError(data.error || "Screening failed.");
            } else {
                this.showError("Unexpected response from server.");
            }
        })
        .catch(err => this.showError(err.message || "Status check failed"));
    },

    showProcessing() {
        const container = document.getElementById('resultContainer');
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');
        const summaryBtn = document.getElementById('summaryPdfBtn');

        if (container) container.style.display = 'block';
        if (badge) {
            badge.className = 'result-status status-processing';
            badge.textContent = 'ANALYZING';
        }
        if (title) title.textContent = 'Running Tiered Conjunction Screening';
        if (content) {
            content.innerHTML = `<p>
                Tier 1: Checking manned assets (ISS, Tiangong)<br>
                Tier 2: Checking high-risk objects (decay, unstable)<br>
                Tier 3: Catalog sweep<br>
                <span class="loading"></span>
            </p>`;
        }
        if (recommendationBox) recommendationBox.style.display = 'none';
        if (summaryBtn) summaryBtn.style.display = 'none';
    },

    showAllClear(message, stats) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');
        const recommendationList = document.getElementById('recommendationList');
        const summaryBtn = document.getElementById('summaryPdfBtn');

        if (badge) {
            badge.className = 'result-status status-clear';
            badge.textContent = 'ALL CLEAR';
        }
        if (title) title.textContent = 'No Actionable Conjunctions Detected';

        let html = `<p>${message || 'All conjunctions are below reporting thresholds.'}</p>`;

        if (stats) {
            html += `
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

        if (content) content.innerHTML = html;

        if (recommendationBox) {
            recommendationBox.style.display = 'block';
        }
        if (recommendationList) {
            recommendationList.innerHTML = `<pre style="white-space:pre-wrap; background:none; border:none; padding:0; color:var(--text-muted);">No maneuver recommended. Continue routine monitoring.</pre>`;
        }

        if (summaryBtn) {
            summaryBtn.style.display = this.currentJobId ? 'inline-block' : 'none';
        }
    },

    showThreats(data) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');
        const recommendationList = document.getElementById('recommendationList');
        const summaryBtn = document.getElementById('summaryPdfBtn');

        const hasRed = data.threats.some(t => t.risk_level === 'RED');
        const hasManned = data.threats.some(t => t.priority === 'MANNED');

        if (badge) {
            if (hasRed) {
                badge.className = 'result-status status-threat';
                badge.textContent = 'CRITICAL CONJUNCTION(S)';
            } else {
                badge.className = 'result-status status-processing';
                badge.textContent = hasManned ? 'MANNED ASSET CONJUNCTION' : 'YELLOW ALERT';
            }
        }
        if (title) {
            title.textContent = `Screening Complete – ${data.threats.length} Threat(s) Found`;
        }

        let threatsHTML = data.threats.map(t => {
            const priorityColors = {
                'MANNED': 'background:#ff0000; color:#fff;',
                'HIGH-RISK': 'background:#ff8800; color:#fff;',
                'CATALOG': 'background:#00d9ff; color:#000;'
            };
            const priorityStyle = priorityColors[t.priority] || priorityColors['CATALOG'];
            const borderColor = t.risk_level === 'RED' ? 'var(--danger)' : 'var(--primary)';

            const pdfButton = t.pdf_url
                ? `<a href="${t.pdf_url}" target="_blank" class="btn" style="display:inline-block; margin-top:8px; padding:10px 20px; font-size:0.9em;">
                        Download PDF Report
                   </a>`
                : '';

            return `
                <div style="margin:24px 0; padding:20px; background:var(--surface-light); border-radius:8px; border-left:5px solid ${borderColor}">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                        <div style="font-size:0.95em;">
                            <div><strong>Asset:</strong> ${t.asset}</div>
                            <div><strong>Intruder:</strong> ${t.intruder}</div>
                        </div>
                        <div style="font-size:0.8em;">
                            <span style="${priorityStyle} padding:4px 10px; border-radius:999px; font-weight:bold;">
                                ${t.priority}
                            </span>
                            <span style="margin-left:8px; color:var(--text-muted);">${t.priority_reason}</span>
                        </div>
                    </div>
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:8px; font-size:0.9em;">
                        <div><strong>Risk Level:</strong> ${t.risk_level}</div>
                        <div><strong>Miss Distance:</strong> ${t.min_km} km</div>
                        <div><strong>TCA (UTC):</strong> ${t.tca}</div>
                        <div><strong>Rel Velocity:</strong> ${t.relative_velocity_kms} km/s</div>
                        <div><strong>Pc:</strong> ${t.pc}</div>
                    </div>
                    ${pdfButton}
                </div>
            `;
        }).join('');

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

        if (content) content.innerHTML = threatsHTML;

        if (recommendationBox) {
            recommendationBox.style.display = 'block';
        }
        if (recommendationList) {
            recommendationList.innerHTML = `<pre style="white-space:pre-wrap; background:none; border:none; padding:0; color:var(--text-muted);">${data.decision}</pre>`;
        }
        if (summaryBtn) {
            summaryBtn.style.display = this.currentJobId ? 'inline-block' : 'none';
        }
    },

    downloadSummaryPdf() {
        if (!this.currentJobId) {
            this.showError('No completed job to summarize.');
            return;
        }

        fetch(`/summary_pdf/${this.currentJobId}`, {
            method: 'GET',
            headers: { 'Authorization': 'Bearer stx-authorized-user' }
        })
        .then(r => {
            if (!r.ok) {
                return r.json().then(d => { throw new Error(d.error || "Summary generation failed"); });
            }
            return r.json();
        })
        .then(data => {
            if (data.pdf_url) {
                window.open(data.pdf_url, '_blank');
            } else {
                this.showError("Summary PDF not available.");
            }
        })
        .catch(err => this.showError(err.message || "Summary generation failed"));
    },

    showError(msg) {
        const badge = document.getElementById('statusBadge');
        const title = document.getElementById('resultTitle');
        const content = document.getElementById('resultContent');
        const recommendationBox = document.getElementById('recommendationBox');
        const summaryBtn = document.getElementById('summaryPdfBtn');

        if (badge) {
            badge.className = 'result-status status-threat';
            badge.textContent = 'ERROR';
        }
        if (title) title.textContent = 'Screening Failed';
        if (content) content.innerHTML = `<p>${msg}</p>`;

        if (recommendationBox) recommendationBox.style.display = 'none';
        if (summaryBtn) summaryBtn.style.display = 'none';
    }
};

const LoginPage = {
    init() {
        const form = document.getElementById('loginForm');
        if (!form) return;
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
};

// Start the app
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => PageRouter.init());
} else {
    PageRouter.init();
}
