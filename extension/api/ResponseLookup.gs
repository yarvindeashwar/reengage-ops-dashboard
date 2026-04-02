/**
 * ReEngage Response Lookup & Mark Responded API
 *
 * Deploy as Web App: Execute as me, Anyone can access
 *
 * GET  ?order_id=<uuid>         → Lookup AI response + review context
 * POST { action: "mark_responded", order_id, platform, operator_email, chain_name }
 *                                → Log to ops_log + mark assignment completed
 */

const BQ_PROJECT = "arboreal-vision-339901";
const TABLE_OPS_LOG = "ops_metrics_data.reengage_dashboard_v2_ops_log";
const TABLE_ASSIGNMENTS = "ops_metrics_data.reengage_dashboard_v2_assignments";

// ── GET: Lookup response ──

function doGet(e) {
  const orderId = (e.parameter.order_id || "").trim();

  if (!orderId) {
    return jsonResponse({ error: "Missing order_id parameter", found: false });
  }

  if (!isValidUuid(orderId)) {
    return jsonResponse({ error: "Invalid order_id format", found: false });
  }

  try {
    const result = lookupResponse(orderId);
    return jsonResponse(result);
  } catch (err) {
    return jsonResponse({ error: err.message, found: false });
  }
}

function lookupResponse(orderId) {
  const safeId = orderId.replace(/'/g, "");
  const sql = `
    SELECT
      rr.response_text,
      rr.coupon_value,
      rr.response_type,
      rr.response_sent,
      ar.customer_name,
      ar.review_text,
      ar.star_rating,
      ar.slug,
      ar.is_replied
    FROM \`${BQ_PROJECT}.pg_cdc_public.review_responses\` rr
    LEFT JOIN \`${BQ_PROJECT}.elt_data.automation_reviews\` ar
      ON rr.order_id = ar.order_id
    WHERE rr.order_id = '${safeId}'
    LIMIT 1
  `;

  const request = { query: sql, useLegacySql: false };
  const queryResults = BigQuery.Jobs.query(request, BQ_PROJECT);

  if (!queryResults.rows || queryResults.rows.length === 0) {
    return { found: false, order_id: orderId };
  }

  const row = queryResults.rows[0].f;
  const fields = queryResults.schema.fields;

  const record = {};
  fields.forEach((field, i) => {
    const val = row[i].v;
    record[field.name] = val === null ? null : val;
  });

  if (record.coupon_value !== null) {
    record.coupon_value = parseFloat(record.coupon_value);
  }

  return {
    found: true,
    order_id: orderId,
    response_text: record.response_text || null,
    coupon_value: record.coupon_value || 0,
    response_type: record.response_type || null,
    response_sent: record.response_sent || null,
    customer_name: record.customer_name || null,
    review_text: record.review_text || null,
    star_rating: record.star_rating || null,
    slug: record.slug || null,
    is_replied: record.is_replied === "true" || record.is_replied === true
  };
}

// ── POST: Mark responded ──

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const action = body.action;

    if (action === "mark_responded") {
      return handleMarkResponded(body);
    }

    return jsonResponse({ error: "Unknown action: " + action, success: false });
  } catch (err) {
    return jsonResponse({ error: err.message, success: false });
  }
}

function handleMarkResponded(body) {
  const orderId = (body.order_id || "").trim();
  const platformName = (body.platform || "").trim();
  const operatorEmail = (body.operator_email || "").trim();
  const chainName = (body.chain_name || "").trim();

  if (!orderId || !isValidUuid(orderId)) {
    return jsonResponse({ error: "Invalid or missing order_id", success: false });
  }
  if (!operatorEmail) {
    return jsonResponse({ error: "Missing operator_email", success: false });
  }

  // Look up review_uid for this order_id from assignments table
  const reviewUid = lookupReviewUid(orderId);

  const safeReviewUid = (reviewUid || orderId).replace(/'/g, "''");
  const safePlatform = platformName.replace(/'/g, "''");
  const safeChain = chainName.replace(/'/g, "''");
  const safeOperator = operatorEmail.replace(/'/g, "''");
  const platformShort = platformName.toLowerCase().includes("uber") ? "extension_ue" : "extension_dd";
  const logId = Utilities.getUuid();
  const now = new Date().toISOString().replace("T", " ").replace("Z", "");

  // Insert ops log entry
  const logSql = `
    INSERT INTO \`${BQ_PROJECT}.${TABLE_OPS_LOG}\`
      (id, review_uid, platform, chain_name, action,
       operator_email, performed_by, remarks, processing_timestamp)
    VALUES
      ('${logId}', '${safeReviewUid}', '${safePlatform}', '${safeChain}',
       'mark_responded', '${safeOperator}', '${safeOperator}',
       '${platformShort}', TIMESTAMP('${now}'))
  `;
  BigQuery.Jobs.query({ query: logSql, useLegacySql: false }, BQ_PROJECT);

  // Mark assignment as completed (if one exists for this order)
  const safeOrderId = orderId.replace(/'/g, "");
  const completeSql = `
    UPDATE \`${BQ_PROJECT}.${TABLE_ASSIGNMENTS}\`
    SET status = 'completed', completed_at = TIMESTAMP('${now}')
    WHERE order_id = '${safeOrderId}' AND status = 'pending'
  `;
  BigQuery.Jobs.query({ query: completeSql, useLegacySql: false }, BQ_PROJECT);

  return jsonResponse({
    success: true,
    message: "Marked as responded",
    review_uid: reviewUid || orderId
  });
}

function lookupReviewUid(orderId) {
  const safeId = orderId.replace(/'/g, "");
  const sql = `
    SELECT review_uid
    FROM \`${BQ_PROJECT}.${TABLE_ASSIGNMENTS}\`
    WHERE order_id = '${safeId}'
    LIMIT 1
  `;
  try {
    const result = BigQuery.Jobs.query({ query: sql, useLegacySql: false }, BQ_PROJECT);
    if (result.rows && result.rows.length > 0) {
      return result.rows[0].f[0].v;
    }
  } catch (e) {
    // Fall through — use order_id as review_uid
  }
  return null;
}

// ── Shared helpers ──

function isValidUuid(str) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(str);
}

function jsonResponse(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Test functions ──

function testLookup() {
  const result = lookupResponse("55057dae-3404-3001-a4ef-914ad3684eca");
  Logger.log(JSON.stringify(result, null, 2));
}

function testMarkResponded() {
  const result = handleMarkResponded({
    order_id: "55057dae-3404-3001-a4ef-914ad3684eca",
    platform: "Doordash",
    operator_email: "test@example.com",
    chain_name: "test-chain"
  });
  Logger.log(result.getContent());
}
