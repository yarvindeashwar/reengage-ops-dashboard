const GOOGLE_CLIENT_ID = "983150008719-7v42pvpog93epoaf8fs8jlrmmv4f7ss0.apps.googleusercontent.com";

document.addEventListener("DOMContentLoaded", () => {
  const signedInSection = document.getElementById("signed-in-section");
  const signedOutSection = document.getElementById("signed-out-section");
  const accountEmail = document.getElementById("account-email");
  const accountName = document.getElementById("account-name");
  const signInBtn = document.getElementById("sign-in");
  const signOutBtn = document.getElementById("sign-out");
  const authError = document.getElementById("auth-error");
  const apiBaseInput = document.getElementById("api-base");
  const saveBtn = document.getElementById("save");
  const savedMsg = document.getElementById("saved-msg");

  // Load saved state
  chrome.storage.sync.get(["apiBase", "operatorEmail", "operatorName", "emailVerified"], (result) => {
    if (result.apiBase) {
      apiBaseInput.value = result.apiBase;
      document.getElementById("dev-section").open = true;
    }

    if (result.operatorEmail && result.emailVerified) {
      showSignedIn(result.operatorEmail, result.operatorName || "");
    } else {
      showSignedOut();
    }
  });

  function showSignedIn(email, name) {
    accountEmail.textContent = email;
    accountName.textContent = name || "";
    signedInSection.style.display = "block";
    signedOutSection.style.display = "none";
  }

  function showSignedOut() {
    signedInSection.style.display = "none";
    signedOutSection.style.display = "block";
  }

  // Sign in with Google OAuth
  signInBtn.addEventListener("click", async () => {
    signInBtn.disabled = true;
    signInBtn.textContent = "Signing in...";
    authError.style.display = "none";

    try {
      const redirectUri = chrome.identity.getRedirectURL();
      const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
      authUrl.searchParams.set("client_id", GOOGLE_CLIENT_ID);
      authUrl.searchParams.set("redirect_uri", redirectUri);
      authUrl.searchParams.set("response_type", "token");
      authUrl.searchParams.set("scope", "openid email profile");

      const responseUrl = await chrome.identity.launchWebAuthFlow({
        url: authUrl.toString(),
        interactive: true
      });

      // Parse access token from redirect URL fragment
      const hash = new URL(responseUrl).hash.slice(1);
      const params = new URLSearchParams(hash);
      const accessToken = params.get("access_token");

      if (!accessToken) throw new Error("No access token received");

      // Get verified user info from Google
      const resp = await fetch("https://www.googleapis.com/oauth2/v2/userinfo", {
        headers: { "Authorization": `Bearer ${accessToken}` }
      });
      if (!resp.ok) throw new Error("Failed to get user info");

      const userInfo = await resp.json();
      if (!userInfo.email) throw new Error("No email in Google response");

      // Store verified email
      await chrome.storage.sync.set({
        operatorEmail: userInfo.email,
        operatorName: userInfo.name || "",
        emailVerified: true
      });

      showSignedIn(userInfo.email, userInfo.name || "");
    } catch (err) {
      authError.textContent = err.message || "Sign in failed";
      authError.style.display = "block";
    } finally {
      signInBtn.disabled = false;
      signInBtn.textContent = "Sign in with Google";
    }
  });

  // Sign out
  signOutBtn.addEventListener("click", () => {
    chrome.storage.sync.remove(["operatorEmail", "operatorName", "emailVerified"], () => {
      showSignedOut();
    });
  });

  // Save API override
  saveBtn.addEventListener("click", () => {
    const apiBase = apiBaseInput.value.trim();
    chrome.storage.sync.set({ apiBase: apiBase || "" }, () => {
      savedMsg.style.display = "block";
      setTimeout(() => { savedMsg.style.display = "none"; }, 2000);
    });
  });
});
