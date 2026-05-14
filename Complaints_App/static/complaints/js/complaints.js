console.log("complaints.js loaded!");

// Utility function to get CSRF token from the page
function getCSRFToken() {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : null;
}

function renderTags(tags) {
    const tagsContainer = document.getElementById('intentTagsList');
    if (!tagsContainer) return;
    tagsContainer.innerHTML = '';
    if (!tags || tags.length === 0) {
        document.getElementById('generatedTags').style.display = 'none';
        return;
    }
    tags.forEach(t => {
        const label = typeof t === 'string' ? t : (t.label || t.name || '');
        const pill = document.createElement('div');
        pill.className = 'tag-pill';
        const icon = document.createElement('span');
        icon.className = 'tag-icon';
        icon.textContent = (label.charAt(0) || '').toUpperCase();
        pill.appendChild(icon);
        const txt = document.createElement('span');
        txt.textContent = label;
        pill.appendChild(txt);
        tagsContainer.appendChild(pill);
    });
    document.getElementById('generatedTags').style.display = 'block';
}

async function generateIntentTags() {
    const descriptionEl = document.querySelector('textarea[name="description"]');
    if (!descriptionEl) return alert('Description field missing');
    const description = descriptionEl.value || '';

    const csrf = getCSRFToken();
    if (!csrf) return alert('CSRF token missing');

    try {
        const resp = await fetch('/generate_intent/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf
            },
            body: JSON.stringify({ description: description })
        });

        if (!resp.ok) {
            const txt = await resp.text();
            throw new Error('Intent API error: ' + resp.status + ' ' + txt);
        }

        const data = await resp.json();
        if (data.success) {
            renderTags(data.tags);
        } else {
            throw new Error('Intent generation failed');
        }
    } catch (err) {
        console.error('generateIntentTags error:', err);
        alert('Failed to generate intent tags: ' + err.message);
    }
}
// Function to pick file for upload
function pickFile(type) {
    const picker = document.getElementById("filePicker");
    
    let accept = "*/*";  // default
    
    if (type === "image") accept = "image/*";
    if (type === "video") accept = "video/*";
    if (type === "audio") accept = "audio/*";
    if (type === "any") accept = "*/*";

    picker.accept = accept;

    picker.onchange = function () {
        const file = picker.files[0];
        if (!file) return;

        console.log("Selected file:", file.name);

        // Display the uploaded file name in the UI
        const fileNameDisplay = document.getElementById('fileNameDisplay');
        fileNameDisplay.textContent = file.name;  // Show the file name

        // Show the remove button
        const removeButton = document.getElementById('removeFileButton');
        removeButton.style.display = 'inline';  // Show the remove button

        // Handle file removal
        removeButton.onclick = function () {
            document.getElementById('filePicker').value = '';  // Clear the file picker
            fileNameDisplay.textContent = '';  // Clear the file name display
            removeButton.style.display = 'none';  // Hide the remove button
            console.log("File removed");
        };
    };

    picker.click();
}

// Simple filter functions for complaints and action types
function filterComplaint() {
    const complaintElem = document.getElementById('complaintDropdown');
    const complaintId = complaintElem ? complaintElem.value : '';
    const actionElem = document.getElementById('actionTypeDropdown');
    const actionType = actionElem ? actionElem.value : '';
    const url = `/track/?complaint_id=${encodeURIComponent(complaintId)}&action_type=${encodeURIComponent(actionType)}`;
    window.location.href = url;
}

function filterActionType() {
    filterComplaint();
}

// Re-align date bubbles based on timeline scroll
function alignDateBubbles() {
    const timeline = document.querySelector('.timeline');
    if (!timeline) return;
    const datesCol = timeline.querySelector('.dates-col');
    const cardsCol = timeline.querySelector('.cards-col');
    if (!datesCol || !cardsCol) return;

    const bubbles = Array.from(datesCol.querySelectorAll('.date-bubble'));
    bubbles.forEach(b => {
        const date = b.getAttribute('data-date');
        if (!date) return;
        const firstCard = cardsCol.querySelector(`.timeline-item[data-date="${date}"]`);
        if (firstCard) {
            const containerRect = timeline.getBoundingClientRect();
            const cardRect = firstCard.getBoundingClientRect();
            const bubbleRect = b.getBoundingClientRect();
            const offset = (cardRect.top - containerRect.top) + timeline.scrollTop;
            const centered = offset + (cardRect.height / 2) - (bubbleRect.height / 2);
            b.style.top = Math.max(0, Math.round(centered)) + 'px';
        } else {
            b.style.top = '-9999px';
        }
    });
}

document.addEventListener('DOMContentLoaded', function() {
    alignDateBubbles();
    setTimeout(alignDateBubbles, 120);
});

window.addEventListener('resize', function() {
    alignDateBubbles();
});

// Re-align during scroll with requestAnimationFrame throttle
(() => {
    const timeline = document.querySelector('.timeline');
    if (!timeline) return;
    let ticking = false;
    timeline.addEventListener('scroll', function() {
        if (!ticking) {
            window.requestAnimationFrame(function() {
                alignDateBubbles();
                ticking = false;
            });
            ticking = true;
        }
    });
})();


document.addEventListener("DOMContentLoaded", function() {
    const filterAgreementNo = document.getElementById("filterAgreementNo");
    const filterDescription = document.getElementById("filterDescription");
    const filterStatus = document.getElementById("filterStatus");
    const filterUserName = document.getElementById("filterUserName");
    const complaintsTableBody = document.getElementById("complaintsTableBody");

    // Filter function
    function filterTable() {
        const rows = complaintsTableBody.getElementsByTagName("tr");
        Array.from(rows).forEach(row => {
            const agreementNo = row.cells[0].innerText.toLowerCase();  // Adjust for Agreement No column
            const description = row.cells[1].innerText.toLowerCase();  // Adjust for Description column
            const status = row.cells[2].innerText.toLowerCase();  // Adjust for Status column
            const userName = row.cells[4].innerText.toLowerCase();  // Adjust for User Name column

            const agreementNoMatch = agreementNo.includes(filterAgreementNo.value.toLowerCase());
            const descriptionMatch = description.includes(filterDescription.value.toLowerCase());
            const statusMatch = status.includes(filterStatus.value.toLowerCase());
            const userNameMatch = userName.includes(filterUserName.value.toLowerCase());

            // Show/hide row based on filter conditions
            if (agreementNoMatch && descriptionMatch && statusMatch && userNameMatch) {
                row.style.display = "";
            } else {
                row.style.display = "none";
            }
        });
    }

    // Add event listeners to each filter input
    filterAgreementNo.addEventListener("input", filterTable);
    filterDescription.addEventListener("input", filterTable);
    filterStatus.addEventListener("input", filterTable);
    filterUserName.addEventListener("input", filterTable);
});


let mediaRecorder;
let audioChunks = [];
let audioBlob;
let audioUrl;
let audioFileName = 'recorded_complaint.wav'; // Local file name for testing

// Start recording audio
function startRecording() {
    // Show the status to inform the user
    document.getElementById('audioStatus').style.display = 'block';
    document.getElementById('fileNameDisplay').textContent = 'Recording...';

    // Get access to the microphone
    navigator.mediaDevices.getUserMedia({ audio: true })
        .then(function(stream) {
            mediaRecorder = new MediaRecorder(stream);

            mediaRecorder.ondataavailable = function(event) {
                audioChunks.push(event.data); // Collect the audio data as it's recorded
            };

            mediaRecorder.onstop = function() {
                // Once recording stops, create a blob of the audio data
                audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
                audioUrl = URL.createObjectURL(audioBlob);

                // Display the audio file name (for testing)
                document.getElementById('fileNameDisplay').textContent = 'File ready: ' + audioFileName;

                // You can later implement upload to S3 here
                saveAudioLocally(audioBlob);
            };

            // Start recording the audio
            mediaRecorder.start();
        })
        .catch(function(error) {
            console.error('Error accessing the microphone: ', error);
            alert('Failed to access the microphone.');
        });
}

// Stop recording audio
function stopRecording() {
    mediaRecorder.stop(); // Stop the recording process
    document.getElementById('recordingStatus').textContent = 'Recording stopped.';
}

// Save the audio locally in the root folder (for testing purposes)
function saveAudioLocally(blob) {
    // For testing purposes, let's save the file in the root folder (this will not work for a real production environment)
    const file = new File([blob], audioFileName, { type: 'audio/wav' });

    // Create a temporary download link to trigger the file save
    const link = document.createElement('a');
    link.href = URL.createObjectURL(file);
    link.download = audioFileName;
    link.click();

    // Reset the audio chunks for the next recording
    audioChunks = [];
}
