// main.js - FINAL WORKING VERSION (you can delete this file and use the inline script in dashboard.html instead if you want)

const fileInput = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
const loading = document.getElementById('loading');
const resultContainer = document.getElementById('resultContainer');
const statusBadge = document.getElementById('statusBadge');
const resultTitle = document.getElementById('resultTitle');
const resultContent = document.getElementById('resultContent');
const recommendationBox = document.getElementById('recommendationBox');
const recommendationText = document.getElementById('recommendationText');
const downloadLink = document.getElementById('downloadLink');

// Drag & drop handling (visual feedback)
dropzone.addEventListener('click', () => fileInput.click());

dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.style.background = 'rgba(0, 255, 170, 0.1)';
});

dropzone.addEventListener('dragleave', () => {
    dropzone.style.background = '';
});

dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.style.background = '';
    if (e.dataTransfer.files.length) {
        handleFile(e.dataTransfer.files[0]);
    }
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length) {
        handleFile(e.target.files[0]);
    }
});

document.getElementById('logoutBtn').addEventListener('click', (e) => {
    e.preventDefault();
    location.href = 'login.html';
});

function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.txt') && !file.name.toLowerCase().endsWith('.tle')) {
        alert('Please upload a .txt or .tle file');
        return;
    }

    const formData = new FormData();
    formData.append('tle_file', file);

    loading.style.display = 'block';
    resultContainer.style.display = 'none';

    fetch('/screen', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer stx-authorized-user'
        },
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        loading.style.display = 'none';

        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }

        if (data.status === 'suppressed') {
            alert('GREEN event - suppressed per filter settings');
            return;
        }

        // Show results
        resultContainer.style.display = 'block';

        const risk = data.risk_level;
        statusBadge.textContent = `RISK LEVEL: ${risk}`;
        statusBadge.className = `result-status ${risk.toLowerCase()}`;

        const t = data.threats[0];
        resultTitle.textContent = `${t.asset.trim()} vs ${t.intruder.trim()}`;

        resultContent.innerHTML = `
            <strong>TCA (UTC):</strong> ${t.tca}<br>
            <strong>Miss Distance:</strong> ${t.min_km} km<br>
            <strong>Rel Velocity:</strong> ${t.relative_velocity_kms} km/s<br>
            <strong>Pc:</strong> ${t.pc}<br><br>
            <strong>RIC (km):</strong><br>
             • Radial:     ${parseFloat(data.geometry.radial).toFixed(3)}<br>
             • In-Track:   ${parseFloat(data.geometry.in_track).toFixed(3)}<br>
             • Cross-Track:${parseFloat(data.geometry.cross_track).toFixed(3)}<br><br>
            <strong>Profile:</strong> ${data.profile}
        `;

        if (data.decision) {
            recommendationBox.style.display = 'block';
            recommendationText.innerHTML = data.decision.replace(/\n/g, '<br>');
        } else {
            recommendationBox.style.display = 'none';
        }

        if (data.pdf_url) {
            downloadLink.href = '/' + data.pdf_url;
            downloadLink.style.display = 'inline-block';
            // Remove old downloaded files from page if you want
        }
    })
    .catch(err => {
        loading.style.display = 'none';
        console.error(err);
        alert('Connection error');
    });
}