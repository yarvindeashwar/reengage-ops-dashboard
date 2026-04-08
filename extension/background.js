/**
 * Background Service Worker
 * Intercepts Segment analytics POSTs to extract delivery_uuid from DoorDash events.
 * Parses UberEats review page URLs to extract workflowUUID.
 * Proxies API calls to the ReEngage Cloud Run backend.
 */

// Default API base — can be overridden in popup for local/ngrok testing
const DEFAULT_API_BASE = "https://reengage-ops-dashboard-v2-ul5ne76yva-uc.a.run.app";

// ── DoorDash: Listen for Segment tracking calls ──
chrome.webRequest.onBeforeRequest.addListener(
  handleSegmentRequest,
  {
    urls: [
      "*://api.segment.io/v1/track*",
      "*://api.segment.com/v1/track*",
      "*://api.segment.io/v1/t*",
      "*://api.segment.com/v1/t*"
    ]
  },
  ["requestBody"]
);

function handleSegmentRequest(details) {
  if (details.method !== "POST" || !details.requestBody) return;

  const raw = details.requestBody.raw;
  if (!raw || raw.length === 0) return;

  try {
    const decoder = new TextDecoder("utf-8");
    const bodyText = raw.map(part => decoder.decode(part.bytes)).join("");
    const payload = JSON.parse(bodyText);

    const events = payload.batch || [payload];

    for (const event of events) {
      const props = event.properties || {};
      const deliveryUuid = props.delivery_uuid || props.deliveryUuid || props.order_uuid;

      if (deliveryUuid) {
        console.log("[ReEngage] Captured delivery_uuid:", deliveryUuid, "from event:", event.event);
        storeAndNotify(details.tabId, deliveryUuid, event.event || "unknown", {
          store_id: props.store_id || props.storeId || null,
          consumer_name: props.consumer_name || null
        });
      }
    }
  } catch (e) {
    // Silently ignore parse errors
  }
}

// ── Inject fetch interceptor into MAIN world on merchant portals ──
chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId !== 0) return;
  chrome.scripting.executeScript({
    target: { tabId: details.tabId },
    files: ["interceptor.js"],
    world: "MAIN",
    injectImmediately: true
  }).catch(() => {});
}, {
  url: [
    { hostContains: "doordash.com" },
    { hostContains: "ubereats.com" }
  ]
});

// ── UberEats: Listen for review page navigation ──
chrome.webNavigation.onCompleted.addListener((details) => {
  try {
    const url = new URL(details.url);
    const match = url.pathname.match(/\/feedback\/reviews\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
    if (match) {
      const workflowUUID = match[1];
      console.log("[ReEngage] Captured UberEats workflowUUID:", workflowUUID);
      storeAndNotify(details.tabId, workflowUUID, "ubereats_review_page", {});
    }
  } catch (e) {}
}, { url: [{ hostContains: "merchants.ubereats.com" }] });

chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
  if (details.frameId !== 0) return;
  try {
    const url = new URL(details.url);
    const match = url.pathname.match(/\/feedback\/reviews\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
    if (match) {
      const workflowUUID = match[1];
      console.log("[ReEngage] Captured UberEats workflowUUID (SPA nav):", workflowUUID);
      storeAndNotify(details.tabId, workflowUUID, "ubereats_review_page_spa", {});
    }
  } catch (e) {}
}, { url: [{ hostContains: "merchants.ubereats.com" }] });

// ── Shared: Store UUID and notify content script ──
function storeAndNotify(tabId, uuid, eventName, extra) {
  if (tabId <= 0) return;

  const storageKey = `tab_${tabId}`;
  const data = {
    delivery_uuid: uuid,
    timestamp: Date.now(),
    event_name: eventName,
    store_id: extra.store_id || null,
    consumer_name: extra.consumer_name || null
  };

  chrome.storage.session.set({ [storageKey]: data });

  chrome.tabs.sendMessage(tabId, {
    type: "DELIVERY_UUID_CAPTURED",
    ...data
  }).catch(() => {});
}

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove(`tab_${tabId}`);
});

// ── Get operator email — Chrome profile first, stored email as fallback ──
function getOperatorEmail() {
  return new Promise((resolve) => {
    chrome.identity.getProfileUserInfo({ accountStatus: "ANY" }, (info) => {
      if (info && info.email) {
        resolve(info.email);
      } else {
        // Fallback: manually entered email from popup
        chrome.storage.sync.get("operatorEmail", (result) => {
          resolve(result.operatorEmail || "");
        });
      }
    });
  });
}

// ── Get API base URL (allows override for ngrok testing) ──
function getApiBase() {
  return new Promise((resolve) => {
    chrome.storage.sync.get("apiBase", (result) => {
      resolve(result.apiBase || DEFAULT_API_BASE);
    });
  });
}

// ── Message handler ──
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "GET_DELIVERY_UUID" && sender.tab) {
    const storageKey = `tab_${sender.tab.id}`;
    chrome.storage.session.get(storageKey, (result) => {
      sendResponse(result[storageKey] || null);
    });
    return true;
  }

  if (message.type === "GET_OPERATOR_EMAIL") {
    getOperatorEmail().then(sendResponse);
    return true;
  }

  if (message.type === "GET_API_BASE") {
    getApiBase().then(sendResponse);
    return true;
  }

  // Lookup AI response from Cloud Run
  if (message.type === "FETCH_RESPONSE") {
    const { orderId } = message;
    Promise.all([getApiBase(), getOperatorEmail()]).then(([apiBase, email]) => {
      const url = `${apiBase}/api/extension/lookup?order_id=${encodeURIComponent(orderId)}`;
      fetch(url, {
        headers: {
          "X-Operator-Email": email,
          "ngrok-skip-browser-warning": "true"
        }
      })
        .then(resp => resp.json().then(data => ({ ok: resp.ok, status: resp.status, data })))
        .then(({ ok, status, data }) => {
          if (!ok) {
            sendResponse({ success: false, error: data.detail || `HTTP ${status}` });
          } else {
            sendResponse({ success: true, data });
          }
        })
        .catch(err => sendResponse({ success: false, error: err.message }));
    });
    return true;
  }

  // Mark responded on Cloud Run
  if (message.type === "MARK_RESPONDED") {
    const { orderId, platform, chainName } = message;
    Promise.all([getApiBase(), getOperatorEmail()]).then(([apiBase, email]) => {
      fetch(`${apiBase}/api/extension/mark-responded`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Operator-Email": email,
          "ngrok-skip-browser-warning": "true"
        },
        body: JSON.stringify({
          order_id: orderId,
          platform: platform,
          chain_name: chainName || ""
        })
      })
        .then(resp => resp.json())
        .then(data => sendResponse({ success: true, data }))
        .catch(err => sendResponse({ success: false, error: err.message }));
    });
    return true;
  }
});
