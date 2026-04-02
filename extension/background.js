/**
 * Background Service Worker
 * Intercepts Segment analytics POSTs to extract delivery_uuid from DoorDash events.
 * Parses UberEats review page URLs to extract workflowUUID.
 * Proxies API calls and mark-responded actions.
 */

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

    // Segment batch format or single event
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
    // Silently ignore parse errors (not all requests are JSON)
  }
}

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
  } catch (e) {
    // Ignore invalid URLs
  }
}, { url: [{ hostContains: "merchants.ubereats.com" }] });

// Also capture when navigating within UberEats SPA (hash/history changes)
chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
  if (details.frameId !== 0) return; // main frame only
  try {
    const url = new URL(details.url);
    const match = url.pathname.match(/\/feedback\/reviews\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
    if (match) {
      const workflowUUID = match[1];
      console.log("[ReEngage] Captured UberEats workflowUUID (SPA nav):", workflowUUID);
      storeAndNotify(details.tabId, workflowUUID, "ubereats_review_page_spa", {});
    }
  } catch (e) {
    // Ignore invalid URLs
  }
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
  }).catch(() => {
    // Content script may not be ready yet, that's OK - it'll read from storage
  });
}

// Clean up old tab data when tabs close
chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove(`tab_${tabId}`);
});

// Handle messages from content script
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "GET_DELIVERY_UUID" && sender.tab) {
    const storageKey = `tab_${sender.tab.id}`;
    chrome.storage.session.get(storageKey, (result) => {
      sendResponse(result[storageKey] || null);
    });
    return true; // async response
  }

  if (message.type === "GET_API_ENDPOINT") {
    chrome.storage.sync.get("apiEndpoint", (result) => {
      sendResponse(result.apiEndpoint || "");
    });
    return true;
  }

  if (message.type === "GET_OPERATOR_EMAIL") {
    chrome.storage.sync.get("operatorEmail", (result) => {
      sendResponse(result.operatorEmail || "");
    });
    return true;
  }

  // Proxy API calls through background to avoid page CSP restrictions
  if (message.type === "FETCH_RESPONSE") {
    const { apiEndpoint, orderId } = message;
    const url = `${apiEndpoint}?order_id=${encodeURIComponent(orderId)}`;
    fetch(url)
      .then(resp => resp.json())
      .then(data => sendResponse({ success: true, data }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true; // async response
  }

  // Mark responded via Apps Script POST
  if (message.type === "MARK_RESPONDED") {
    const { apiEndpoint, orderId, platform, operatorEmail, chainName } = message;
    fetch(apiEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "mark_responded",
        order_id: orderId,
        platform: platform,
        operator_email: operatorEmail,
        chain_name: chainName || ""
      })
    })
      .then(resp => resp.json())
      .then(data => sendResponse({ success: true, data }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
});
