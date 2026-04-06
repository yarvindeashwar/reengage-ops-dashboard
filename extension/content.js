/**
 * Content Script — DoorDash & UberEats Merchant Portals
 * Detects the review reply area, fetches the AI-generated response
 * from the ReEngage API, and injects full context + action buttons.
 */

(function () {
  "use strict";

  // ── Platform detection ──
  const platform = window.location.hostname.includes("ubereats") ? "ubereats" : "doordash";
  const UBEREATS_REVIEW_RE = /\/feedback\/reviews\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i;

  let currentDeliveryUuid = null;
  let injectedForUuid = null; // prevent duplicate injection
  let operatorEmail = "";
  let injectInProgress = false; // prevent concurrent API calls

  // Auto-detect operator email from Chrome profile
  chrome.runtime.sendMessage({ type: "GET_OPERATOR_EMAIL" }, (email) => {
    operatorEmail = email || "";
  });

  // Listen for delivery_uuid / workflowUUID from background script
  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "DELIVERY_UUID_CAPTURED") {
      currentDeliveryUuid = message.delivery_uuid;
      console.log(`[ReEngage] Got UUID (${platform}):`, currentDeliveryUuid);
      tryInject();
    }
  });

  // ── UberEats: Also extract UUID from URL on load ──
  if (platform === "ubereats") {
    const match = window.location.pathname.match(UBEREATS_REVIEW_RE);
    if (match) {
      currentDeliveryUuid = match[1];
      console.log("[ReEngage] Got UberEats UUID from URL:", currentDeliveryUuid);
    }
  }

  // ── Platform-specific selectors ──
  function findTextarea() {
    if (platform === "doordash") {
      return document.querySelector('textarea[data-anchor-id="ComplaintResponseComposeBox"]');
    }
    // UberEats: textarea with "Reply privately to" placeholder
    const textareas = document.querySelectorAll("textarea");
    for (const ta of textareas) {
      if (ta.placeholder && ta.placeholder.startsWith("Reply privately to")) {
        return ta;
      }
    }
    return null;
  }

  function isSidesheetNode(node) {
    if (!node.querySelector) return false;
    if (platform === "doordash") {
      return (
        node.querySelector('[data-anchor-id="ComplaintResponse"]') ||
        node.querySelector('[data-anchor-id="ComplaintResponseComposeBox"]') ||
        (node.matches && node.matches('[data-anchor-id="ComplaintResponse"]'))
      );
    }
    // UberEats: detect review detail panel
    return !!(node.querySelector('textarea[placeholder^="Reply privately to"]'));
  }

  function getTextareaWrapper(textarea) {
    if (platform === "doordash") {
      return (
        textarea.closest(".StyledStackChildren-sc-1cimee4-0") ||
        textarea.closest(".FieldRoot-sc-13e9y4-0") ||
        textarea.parentElement
      );
    }
    // UberEats: walk up to find a suitable container
    return textarea.closest("form") || textarea.parentElement;
  }

  // ── MutationObserver: watch for sidesheet open + textarea appearing ──
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (isSidesheetNode(node)) {
          console.log("[ReEngage] Sidesheet detected");
          setTimeout(tryInject, 300);
          return; // only trigger once per batch
        }
      }
    }

    // UberEats: also check if textarea appeared via subtree changes
    if (platform === "ubereats" && currentDeliveryUuid && injectedForUuid !== currentDeliveryUuid) {
      const textarea = findTextarea();
      if (textarea) {
        setTimeout(tryInject, 300);
      }
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });

  // UberEats: Try injection on initial load (page might already have the review open)
  if (platform === "ubereats") {
    setTimeout(tryInject, 1000);
  }

  // ── Core injection logic ──
  async function tryInject() {
    if (injectInProgress) return;

    const textarea = findTextarea();
    if (!textarea) return;

    // Don't re-inject for the same UUID
    if (injectedForUuid === currentDeliveryUuid && document.querySelector(".reengage-btn-container")) {
      return;
    }

    injectInProgress = true;
    try {
      // If we don't have a UUID yet, try to get it from storage
      if (!currentDeliveryUuid) {
        const data = await new Promise((resolve) => {
          chrome.runtime.sendMessage({ type: "GET_DELIVERY_UUID" }, resolve);
        });
        if (data && data.delivery_uuid) {
          currentDeliveryUuid = data.delivery_uuid;
        }
      }

      // UberEats: also try to extract from current URL
      if (!currentDeliveryUuid && platform === "ubereats") {
        const match = window.location.pathname.match(UBEREATS_REVIEW_RE);
        if (match) {
          currentDeliveryUuid = match[1];
        }
      }

      if (!currentDeliveryUuid) {
        injectStatusBadge(textarea, "waiting", "Waiting for order ID...");
        return;
      }

      // Get operator email (auto-detected from Chrome profile via background)
      if (!operatorEmail) {
        operatorEmail = await new Promise((resolve) => {
          chrome.runtime.sendMessage({ type: "GET_OPERATOR_EMAIL" }, resolve);
        }) || "";
      }

      if (!operatorEmail) {
        injectStatusBadge(textarea, "error", "Not signed into Chrome. Sign in with your loopai.com account.");
        return;
      }

      // Remove any existing injection
      removeExistingInjection();

      // Fetch response via background script (avoids page CSP restrictions)
      injectStatusBadge(textarea, "loading", "Fetching response...");

      const result = await new Promise((resolve) => {
        chrome.runtime.sendMessage({
          type: "FETCH_RESPONSE",
          orderId: currentDeliveryUuid
        }, resolve);
      });

      if (!result || !result.success) {
        throw new Error(result ? result.error : "No response from background");
      }
      const data = result.data;

      removeExistingInjection();

      if (!data.found || !data.response_text) {
        injectStatusBadge(textarea, "empty", "No response found for this order.");
        return;
      }

      injectResponsePanel(textarea, data);
      injectedForUuid = currentDeliveryUuid;
    } catch (err) {
      removeExistingInjection();
      injectStatusBadge(textarea, "error", `API error: ${err.message}`);
    } finally {
      injectInProgress = false;
    }
  }

  // ── Inject full context panel + action buttons ──
  function injectResponsePanel(textarea, data) {
    const container = document.createElement("div");
    container.className = "reengage-btn-container";

    // Already-responded warning
    const alreadyResponded = data.response_sent || data.is_replied;
    if (alreadyResponded) {
      const warning = document.createElement("div");
      warning.className = "reengage-already-responded";
      const respondedDate = data.response_sent
        ? new Date(data.response_sent).toLocaleDateString()
        : "previously";
      warning.innerHTML = `<strong>Already responded</strong> (${respondedDate}). Posting again will create a duplicate reply.`;
      container.appendChild(warning);
    }

    // Review context section
    const context = document.createElement("div");
    context.className = "reengage-context";

    // Customer name + rating
    const header = document.createElement("div");
    header.className = "reengage-context-header";
    const customerName = data.customer_name || "Unknown";
    const starRating = data.star_rating;
    const stars = starRating ? renderStars(parseInt(starRating)) : "";
    header.innerHTML = `<span class="reengage-customer-name">${escapeHtml(customerName)}</span>${stars ? ` <span class="reengage-stars">${stars}</span>` : ""}`;
    context.appendChild(header);

    // Original review text (collapsible)
    if (data.review_text) {
      const reviewBlock = document.createElement("div");
      reviewBlock.className = "reengage-review-text";
      reviewBlock.textContent = data.review_text;
      context.appendChild(reviewBlock);
    }

    container.appendChild(context);

    // Full AI response (not truncated)
    const responseBlock = document.createElement("div");
    responseBlock.className = "reengage-preview";
    responseBlock.textContent = data.response_text;
    container.appendChild(responseBlock);

    // Coupon instruction
    if (data.coupon_value && data.coupon_value > 0) {
      const couponBadge = document.createElement("div");
      couponBadge.className = "reengage-coupon-badge";
      couponBadge.innerHTML = `<strong>Apply $${data.coupon_value} discount</strong> before submitting the reply`;
      container.appendChild(couponBadge);
    }

    // Button row
    const btnRow = document.createElement("div");
    btnRow.className = "reengage-btn-row";

    // Copy button
    const copyBtn = document.createElement("button");
    copyBtn.className = "reengage-copy-btn";
    copyBtn.textContent = "Copy Response";
    copyBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        await navigator.clipboard.writeText(data.response_text);
        showToast("Response copied to clipboard!");
        copyBtn.textContent = "Copied!";
        copyBtn.classList.add("reengage-copied");
        setTimeout(() => {
          copyBtn.textContent = "Copy Response";
          copyBtn.classList.remove("reengage-copied");
        }, 2000);
      } catch (err) {
        showToast("Clipboard failed - response pasted into textbox");
        setTextareaValue(textarea, data.response_text);
      }
    });
    btnRow.appendChild(copyBtn);

    // Paste-into-box button
    const pasteBtn = document.createElement("button");
    pasteBtn.className = "reengage-paste-btn";
    pasteBtn.textContent = "Paste into Box";
    pasteBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setTextareaValue(textarea, data.response_text);
      showToast("Response pasted into textbox!");
    });
    btnRow.appendChild(pasteBtn);

    container.appendChild(btnRow);

    // Mark as Responded button (separate row)
    const markRow = document.createElement("div");
    markRow.className = "reengage-mark-row";

    const markBtn = document.createElement("button");
    markBtn.className = "reengage-mark-btn";
    markBtn.textContent = "Mark as Responded";
    markBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      markBtn.disabled = true;
      markBtn.textContent = "Logging...";

      try {
        const platformName = platform === "ubereats" ? "UberEats" : "Doordash";
        const result = await new Promise((resolve) => {
          chrome.runtime.sendMessage({
            type: "MARK_RESPONDED",
            orderId: currentDeliveryUuid,
            platform: platformName,
            chainName: data.slug || ""
          }, resolve);
        });

        if (result && result.success) {
          markBtn.textContent = "Logged!";
          markBtn.classList.add("reengage-mark-done");
          showToast("Response logged in dashboard!");
        } else {
          throw new Error(result ? result.error : "No response");
        }
      } catch (err) {
        markBtn.disabled = false;
        markBtn.textContent = "Mark as Responded";
        showToast(`Failed to log: ${err.message}`);
      }
    });
    markRow.appendChild(markBtn);
    container.appendChild(markRow);

    // Source badge + operator badge
    const footerRow = document.createElement("div");
    footerRow.className = "reengage-footer-row";

    const sourceBadge = document.createElement("span");
    sourceBadge.className = "reengage-source-badge";
    sourceBadge.textContent = `ReEngage ${data.response_type === "ai" ? "AI" : "Template"} Response`;
    footerRow.appendChild(sourceBadge);

    if (operatorEmail) {
      const opBadge = document.createElement("span");
      opBadge.className = "reengage-operator-badge";
      opBadge.textContent = operatorEmail;
      footerRow.appendChild(opBadge);
    }

    container.appendChild(footerRow);

    // Insert before the textarea's wrapper
    const textareaWrapper = getTextareaWrapper(textarea);
    textareaWrapper.parentElement.insertBefore(container, textareaWrapper);
  }

  // ── Helpers ──
  function renderStars(count) {
    if (!count || count < 1 || count > 5) return "";
    return "★".repeat(count) + "☆".repeat(5 - count);
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Status badge (loading, error, etc.) ──
  function injectStatusBadge(textarea, type, message) {
    removeExistingInjection();
    const badge = document.createElement("div");
    badge.className = `reengage-btn-container reengage-status-${type}`;
    badge.textContent = message;

    const textareaWrapper = getTextareaWrapper(textarea);
    textareaWrapper.parentElement.insertBefore(badge, textareaWrapper);
  }

  function removeExistingInjection() {
    document.querySelectorAll(".reengage-btn-container").forEach((el) => el.remove());
  }

  // ── Set textarea value (React-compatible) ──
  function setTextareaValue(textarea, value) {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, "value"
    ).set;
    nativeInputValueSetter.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // ── Toast notification ──
  function showToast(message) {
    const existing = document.querySelector(".reengage-toast");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.className = "reengage-toast";
    toast.textContent = message;
    document.body.appendChild(toast);

    requestAnimationFrame(() => toast.classList.add("reengage-toast-visible"));

    setTimeout(() => {
      toast.classList.remove("reengage-toast-visible");
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  }

  // ── Reset on sidesheet close ──
  const closeObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.removedNodes) {
        if (node.nodeType !== 1) continue;
        if (!node.querySelector) continue;
        const isClosing = platform === "doordash"
          ? (node.querySelector('[data-anchor-id="ComplaintResponse"]') ||
             (node.matches && node.matches('[data-anchor-id="ComplaintResponse"]')))
          : !!(node.querySelector('textarea[placeholder^="Reply privately to"]'));

        if (isClosing) {
          // UberEats: only reset if we're not already on a new review URL
          if (platform === "ubereats") {
            const stillOnReview = window.location.pathname.match(UBEREATS_REVIEW_RE);
            if (stillOnReview) continue; // navigated to a different review, don't reset
          }
          currentDeliveryUuid = null;
          injectedForUuid = null;
        }
      }
    }
  });
  closeObserver.observe(document.body, { childList: true, subtree: true });

  // ── UberEats: Watch for URL changes (SPA navigation between reviews) ──
  if (platform === "ubereats") {
    let lastUrl = window.location.href;
    const urlObserver = new MutationObserver(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        const match = window.location.pathname.match(UBEREATS_REVIEW_RE);
        if (match) {
          currentDeliveryUuid = match[1];
          injectedForUuid = null;
          removeExistingInjection();
          console.log("[ReEngage] UberEats URL changed, new UUID:", currentDeliveryUuid);
          setTimeout(tryInject, 500);
        } else {
          // Navigated away from a review
          currentDeliveryUuid = null;
          injectedForUuid = null;
          removeExistingInjection();
        }
      }
    });
    urlObserver.observe(document.body, { childList: true, subtree: true });
  }
})();
