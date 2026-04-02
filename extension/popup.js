document.addEventListener("DOMContentLoaded", () => {
  const endpointInput = document.getElementById("endpoint");
  const operatorInput = document.getElementById("operator-email");
  const operatorDisplay = document.getElementById("operator-display");
  const saveBtn = document.getElementById("save");
  const statusDiv = document.getElementById("status");

  // Load saved settings
  chrome.storage.sync.get(["apiEndpoint", "operatorEmail"], (result) => {
    if (result.apiEndpoint) {
      endpointInput.value = result.apiEndpoint;
    }
    if (result.operatorEmail) {
      operatorInput.value = result.operatorEmail;
      operatorDisplay.textContent = result.operatorEmail;
      operatorDisplay.style.display = "inline-block";
    }

    if (result.apiEndpoint && result.operatorEmail) {
      showStatus("ok", "Connected");
    } else if (!result.operatorEmail) {
      showStatus("warn", "Please enter your operator email");
    } else {
      showStatus("warn", "API endpoint not configured");
    }
  });

  saveBtn.addEventListener("click", () => {
    const endpoint = endpointInput.value.trim();
    const email = operatorInput.value.trim();

    if (!email) {
      showStatus("warn", "Please enter your operator email");
      return;
    }
    if (!endpoint) {
      showStatus("warn", "Please enter an endpoint URL");
      return;
    }

    chrome.storage.sync.set({ apiEndpoint: endpoint, operatorEmail: email }, () => {
      operatorDisplay.textContent = email;
      operatorDisplay.style.display = "inline-block";
      showStatus("ok", "Saved!");
    });
  });

  function showStatus(type, message) {
    statusDiv.style.display = "block";
    statusDiv.className = `status ${type}`;
    statusDiv.textContent = message;
  }
});
