/**
 * Fetch interceptor — runs in the page's MAIN world (bypasses CSP).
 * Detects when DoorDash/UberEats confirms a review has been replied to,
 * then posts the data back to content.js via window.postMessage.
 */
(function () {
  "use strict";

  console.log("[ReEngage Interceptor] Loaded in MAIN world on", window.location.hostname);

  var _origFetch = window.fetch;
  window.fetch = function () {
    var args = arguments;
    return _origFetch.apply(this, args).then(function (resp) {
      try {
        var url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url ? args[0].url : "");
        var init = args[1] || {};
        var method = (init.method || "GET").toUpperCase();

        if (method === "POST" && resp.ok) {
          console.log("[ReEngage Interceptor] POST:", url.slice(0, 120));

          // DoorDash: reviews endpoint
          if (url.indexOf("/consumer_feedback/reviews") !== -1) {
            var clone = resp.clone();
            clone.json().then(function (json) {
              // DoorDash wraps reviews in cxReviewsList
              var reviews = json.cxReviewsList || json.reviews || [];
              if (!Array.isArray(reviews) && json.data) {
                reviews = json.data.cxReviewsList || json.data.reviews || [];
              }
              if (!Array.isArray(reviews)) reviews = [];
              var responded = [];
              for (var i = 0; i < reviews.length; i++) {
                var r = reviews[i];
                if (r.merchantResponded) {
                  // deliveryUuid is nested inside orderReviewDetail
                  var detail = r.orderReviewDetail || {};
                  responded.push({
                    deliveryUuid: detail.deliveryUuid || detail.delivery_uuid || null,
                    cxReviewId: r.cxReviewId || null,
                    storeId: r.storeId || null
                  });
                }
              }
              window.postMessage({
                type: "REENGAGE_PLATFORM_RESPONDED",
                platform: "doordash",
                responded: responded,
                totalReviews: reviews.length
              }, "*");
            }).catch(function () {});
          }

          // UberEats: GraphQL EaterReviews
          if (url.indexOf("/manager/graphql") !== -1) {
            var bodyText = typeof init.body === "string" ? init.body : "";
            if (bodyText.indexOf("EaterReview") !== -1) {
              var clone2 = resp.clone();
              clone2.json().then(function (json) {
                var reviews = [];
                try { reviews = json.data.eaterReviews.reviews || []; } catch (e) {}
                if (!Array.isArray(reviews)) {
                  try { reviews = json.data.eaterReviews || []; } catch (e) { reviews = []; }
                }
                var responded = [];
                for (var i = 0; i < reviews.length; i++) {
                  var r = reviews[i];
                  if (r.reply && r.reply.comment) {
                    responded.push({
                      workflowUUID: (r.order && r.order.workflowUUID) || r.uuid || null,
                      userUUID: r.reply.userUUID || null
                    });
                  }
                }
                window.postMessage({
                  type: "REENGAGE_PLATFORM_RESPONDED",
                  platform: "ubereats",
                  responded: responded,
                  totalReviews: reviews.length
                }, "*");
              }).catch(function () {});
            }
          }
        }
      } catch (e) {}
      return resp;
    });
  };
  // ── Also intercept XMLHttpRequest (DoorDash uses Axios/XHR for reviews) ──
  var _origOpen = XMLHttpRequest.prototype.open;
  var _origSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this._reengageUrl = url || "";
    this._reengageMethod = (method || "GET").toUpperCase();
    return _origOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function (body) {
    var self = this;
    var bodyStr = typeof body === "string" ? body : "";

    // DoorDash: reviews endpoint (XHR)
    if (this._reengageMethod === "POST" && this._reengageUrl.indexOf("/consumer_feedback/reviews") !== -1) {
      this.addEventListener("load", function () {
        try {
          var json = JSON.parse(self.responseText);
          var reviews = json.cxReviewsList || json.reviews || [];
          if (!Array.isArray(reviews)) reviews = [];
          var responded = [];
          for (var i = 0; i < reviews.length; i++) {
            var r = reviews[i];
            if (r.merchantResponded) {
              var detail = r.orderReviewDetail || {};
              responded.push({
                deliveryUuid: detail.deliveryUuid || detail.delivery_uuid || null,
                cxReviewId: r.cxReviewId || null,
                storeId: r.storeId || null
              });
            }
          }
          console.log("[ReEngage Interceptor] XHR DoorDash reviews:", reviews.length, "responded:", responded.length);
          window.postMessage({
            type: "REENGAGE_PLATFORM_RESPONDED",
            platform: "doordash",
            responded: responded,
            totalReviews: reviews.length
          }, "*");
        } catch (e) {}
      });
    }

    // UberEats: GraphQL endpoint (XHR)
    if (this._reengageMethod === "POST" && this._reengageUrl.indexOf("/manager/graphql") !== -1) {
      this.addEventListener("load", function () {
        try {
          var json = JSON.parse(self.responseText);

          // Case 1: EaterReviews list response
          if (json.data && (json.data.eaterReviews || json.data.getEaterReviews)) {
            var reviewData = json.data.eaterReviews || json.data.getEaterReviews || {};
            var reviews = reviewData.reviews || reviewData;
            if (!Array.isArray(reviews)) reviews = [];
            var responded = [];
            for (var i = 0; i < reviews.length; i++) {
              var r = reviews[i];
              if (r.reply && r.reply.comment) {
                responded.push({
                  workflowUUID: (r.order && r.order.workflowUUID) || r.uuid || null,
                  userUUID: r.reply.userUUID || null
                });
              }
            }
            console.log("[ReEngage Interceptor] XHR UberEats reviews:", reviews.length, "responded:", responded.length);
            window.postMessage({
              type: "REENGAGE_PLATFORM_RESPONDED",
              platform: "ubereats",
              responded: responded,
              totalReviews: reviews.length
            }, "*");
          }

          // Case 2: SubmitReply mutation response — reply was just posted
          if (json.data && json.data.submitEaterReviewReply) {
            var reply = json.data.submitEaterReviewReply;
            console.log("[ReEngage Interceptor] UberEats reply submitted!", reply);
            window.postMessage({
              type: "REENGAGE_REPLY_SUBMITTED",
              platform: "ubereats"
            }, "*");
          }
        } catch (e) {}
      });
    }

    // DoorDash: detect reply submission (respond to review endpoint)
    if (this._reengageMethod === "POST" && this._reengageUrl.indexOf("/consumer_feedback/send_response") !== -1) {
      this.addEventListener("load", function () {
        if (self.status >= 200 && self.status < 300) {
          console.log("[ReEngage Interceptor] DoorDash reply submitted!");
          window.postMessage({
            type: "REENGAGE_REPLY_SUBMITTED",
            platform: "doordash"
          }, "*");
        }
      });
    }

    return _origSend.apply(this, arguments);
  };
})();
