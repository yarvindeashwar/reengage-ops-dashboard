"""
Shared constants and configuration for the ReEngage Ops Dashboard.
"""

PROJECT = "arboreal-vision-339901"

# ── V2 table names (new tables, never touch existing ones) ───────────────────
TABLE_USERS       = "ops_metrics_data.reengage_dashboard_v2_users"
TABLE_ASSIGNMENTS = "ops_metrics_data.reengage_dashboard_v2_assignments"
TABLE_OPS_LOG     = "ops_metrics_data.reengage_dashboard_v2_ops_log"

# ── Read-only existing tables ────────────────────────────────────────────────
TABLE_AUTOMATION_REVIEWS   = "elt_data.automation_reviews"
TABLE_SLUG_AM_MAPPING      = "restaurant_aggregate_metrics.slug_am_mapping"
TABLE_REVIEW_RESPONSE_CFG  = "pg_cdc_public.review_response_config"
TABLE_REVIEW_RESPONSES     = "pg_cdc_public.review_responses"

# ── UI constants ─────────────────────────────────────────────────────────────
PRIORITY_ICON = {"CRITICAL": "🔴", "URGENT": "🟠", "NORMAL": "🟢"}
STATUS_ICON   = {"PENDING": "⏳", "RESPONDED": "✅", "EXPIRED": "💀"}

TONALITIES     = ["casual", "enthusiastic", "grateful", "polished"]
RESPONSE_TYPES = ["ai", "template"]
COUPON_TYPES   = ["fixed", "percentage", "none"]
CUSTOMER_TYPES = ["new", "existing"]
FEEDBACK_TYPES = ["with_feedback", "without_feedback"]
SENTIMENTS     = ["positive", "negative", "neutral"]
RATING_OPTIONS_DD = ["RATING_VALUE_THUMBS_DOWN", "RATING_VALUE_THUMBS_UP", "RATING_VALUE_LOVED"]
RATING_OPTIONS_UE = ["1", "2", "3", "4", "5"]

# ── User roles ───────────────────────────────────────────────────────────────
ROLE_LEAD    = "lead"
ROLE_TENURED = "tenured_operator"
ROLE_NEW     = "new_operator"
ALL_ROLES    = [ROLE_LEAD, ROLE_TENURED, ROLE_NEW]
