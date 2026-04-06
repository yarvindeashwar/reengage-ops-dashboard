document.addEventListener("DOMContentLoaded", () => {
  const accountRow = document.getElementById("account-row");
  const accountDot = document.getElementById("account-dot");
  const accountEmail = document.getElementById("account-email");
  const manualEmailSection = document.getElementById("manual-email-section");
  const manualEmailInput = document.getElementById("manual-email");
  const apiBaseInput = document.getElementById("api-base");
  const saveBtn = document.getElementById("save");
  const savedMsg = document.getElementById("saved-msg");

  // Load saved settings
  chrome.storage.sync.get(["apiBase", "operatorEmail"], (result) => {
    if (result.apiBase) {
      apiBaseInput.value = result.apiBase;
      document.getElementById("dev-section").open = true;
    }
    if (result.operatorEmail) {
      manualEmailInput.value = result.operatorEmail;
    }
  });

  // Try auto-detect from Chrome profile
  chrome.identity.getProfileUserInfo({ accountStatus: "ANY" }, (info) => {
    if (info && info.email) {
      accountEmail.textContent = info.email;
      accountEmail.classList.remove("warn");
      accountDot.classList.remove("warn");
      accountRow.classList.remove("warn");
      // Save auto-detected email
      chrome.storage.sync.set({ operatorEmail: info.email });
    } else {
      // Show manual fallback
      accountEmail.textContent = "Auto-detect failed — enter email below";
      manualEmailSection.style.display = "block";
    }
  });

  saveBtn.addEventListener("click", () => {
    const apiBase = apiBaseInput.value.trim();
    const manualEmail = manualEmailInput.value.trim();

    const toSave = { apiBase: apiBase || "" };
    if (manualEmail) toSave.operatorEmail = manualEmail;

    chrome.storage.sync.set(toSave, () => {
      if (manualEmail) {
        accountEmail.textContent = manualEmail;
        accountEmail.classList.remove("warn");
        accountDot.classList.remove("warn");
        accountRow.classList.remove("warn");
      }
      savedMsg.style.display = "block";
      setTimeout(() => { savedMsg.style.display = "none"; }, 2000);
    });
  });
});
