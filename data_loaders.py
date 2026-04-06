"""
Data loaders — BigQuery for everything (reviews, configs, v2 tables).
"""

import json

import pandas as pd
import streamlit as st

from config import (
    TABLE_AUTOMATION_REVIEWS, TABLE_SLUG_AM_MAPPING,
    TABLE_REVIEW_RESPONSE_CFG, TABLE_REVIEW_RESPONSES,
    TABLE_ASSIGNMENTS, TABLE_OPS_LOG,
)
from db import bq_read


# ── Reviews (existing tables, read-only) ─────────────────────────────────────

@st.cache_data(ttl=120, show_spinner="Loading reviews from BigQuery...")
def load_reviews() -> pd.DataFrame:
    sql = f"""
    WITH eligible_reviews AS (
        SELECT
            COALESCE(ar.review_id, ar.order_id) AS review_uid,
            ar.order_id,
            ar.review_id,
            sm.chain AS chain_name,
            sm.b_name_id,
            sm.vb_name AS brand_name,
            ar.platform,
            ar.slug,
            ar.store_id,
            ar.customer_name,
            ar.user_id AS customer_id,
            SAFE_CAST(ar.orders_count AS INT64) AS orders_count,
            CASE WHEN SAFE_CAST(ar.orders_count AS INT64) > 1 THEN 'existing' ELSE 'new' END AS customer_type,
            ar.star_rating,
            ar.rating_type,
            ar.rating_value,
            ar.review_text,
            ar.is_replied,
            ar.replied_comment,
            ar.items,
            ar.order_value,
            COALESCE(ar.review_timestamp, ar.order_timestamp) AS event_timestamp,
            DATE(DATETIME(COALESCE(ar.review_timestamp, ar.order_timestamp), 'America/Chicago')) AS review_date
        FROM `{TABLE_AUTOMATION_REVIEWS}` ar
        INNER JOIN `{TABLE_SLUG_AM_MAPPING}` sm ON ar.slug = sm.slug
        WHERE COALESCE(ar.review_timestamp, ar.order_timestamp)
              >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND EXISTS (
              SELECT 1 FROM `{TABLE_REVIEW_RESPONSE_CFG}` rrc
              WHERE rrc.paused = FALSE AND sm.chain = rrc.chain_name
              AND (
                  ARRAY_LENGTH(IFNULL(JSON_VALUE_ARRAY(rrc.vb_platforms), [])) = 0
                  OR ar.platform IN UNNEST(JSON_VALUE_ARRAY(rrc.vb_platforms))
              )
              AND (
                  ARRAY_LENGTH(IFNULL(JSON_VALUE_ARRAY(rrc.ratings), [])) = 0
                  OR CAST(ar.star_rating AS STRING) IN UNNEST(JSON_VALUE_ARRAY(rrc.ratings))
                  OR ar.rating_value IN UNNEST(JSON_VALUE_ARRAY(rrc.ratings))
              )
          )
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY COALESCE(ar.review_id, ar.order_id)
            ORDER BY COALESCE(ar.review_timestamp, ar.order_timestamp) DESC
        ) = 1
    ),

    with_days AS (
        SELECT *,
            CASE
                WHEN platform = 'Doordash' THEN
                    DATE_DIFF(DATE_ADD(review_date, INTERVAL 7 DAY), CURRENT_DATE('America/Chicago'), DAY)
                WHEN platform = 'UberEats' THEN
                    DATE_DIFF(DATE_ADD(review_date, INTERVAL 14 DAY), CURRENT_DATE('America/Chicago'), DAY)
                ELSE 0
            END AS days_left
        FROM eligible_reviews
    ),

    with_response AS (
        SELECT
            w.*,
            rr.response_text AS ai_response,
            rr.response_type AS rr_response_type,
            rr.coupon_value,
            rr.response_sent,
            rr.config_id
        FROM with_days w
        LEFT JOIN `{TABLE_REVIEW_RESPONSES}` rr ON w.order_id = rr.order_id
    ),

    with_assignment AS (
        SELECT w.*,
            a.status AS assignment_status
        FROM with_response w
        LEFT JOIN `{TABLE_ASSIGNMENTS}` a
            ON w.review_uid = a.review_uid AND a.status = 'completed'
    ),

    with_status AS (
        SELECT *,
            CASE
                WHEN is_replied = TRUE THEN 'RESPONDED'
                WHEN response_sent IS NOT NULL THEN 'RESPONDED'
                WHEN assignment_status = 'completed' THEN 'RESPONDED'
                WHEN days_left <= 0 THEN 'EXPIRED'
                ELSE 'PENDING'
            END AS status,
            COALESCE(replied_comment, ai_response) AS response_text,
            CASE
                WHEN days_left <= 1 THEN 'CRITICAL'
                WHEN days_left = 2  THEN 'URGENT'
                ELSE 'NORMAL'
            END AS priority,
            CASE
                WHEN rating_value = 'RATING_VALUE_LOVED' THEN 'Loved'
                WHEN rating_value = 'RATING_VALUE_THUMBS_UP' THEN 'Thumbs Up'
                WHEN rating_value = 'RATING_VALUE_THUMBS_DOWN' THEN 'Thumbs Down'
                WHEN rating_value = 'RATING_VALUE_FIVE' THEN '5 Stars'
                WHEN SAFE_CAST(star_rating AS FLOAT64) > 0
                    THEN CONCAT(CAST(CAST(SAFE_CAST(star_rating AS FLOAT64) AS INT64) AS STRING), ' Stars')
                ELSE COALESCE(rating_value, CAST(star_rating AS STRING))
            END AS rating_display,
            CASE
                WHEN rating_value = 'RATING_VALUE_LOVED' THEN 5.0
                WHEN rating_value = 'RATING_VALUE_THUMBS_UP' THEN 4.0
                WHEN rating_value = 'RATING_VALUE_THUMBS_DOWN' THEN 1.0
                WHEN rating_value = 'RATING_VALUE_FIVE' THEN 5.0
                WHEN SAFE_CAST(star_rating AS FLOAT64) > 0 THEN SAFE_CAST(star_rating AS FLOAT64)
                ELSE NULL
            END AS rating_numeric,
            CASE
                WHEN platform = 'UberEats' THEN
                    CONCAT('https://merchants.ubereats.com/manager/home/', store_id, '/feedback/reviews/', order_id)
                WHEN platform = 'Doordash' THEN
                    CONCAT('https://www.doordash.com/merchant/feedback?store_id=', store_id)
                ELSE ''
            END AS portal_link
        FROM with_assignment
    )

    SELECT
        review_uid, order_id, review_id, chain_name, b_name_id, brand_name,
        platform, slug, store_id,
        customer_name, customer_id, orders_count, customer_type,
        CAST(star_rating AS STRING) AS star_rating, rating_type, rating_value,
        rating_numeric, rating_display, review_text, items, order_value,
        review_date, days_left,
        portal_link, response_text, rr_response_type AS response_type, coupon_value,
        CAST(config_id AS STRING) AS config_id, status, priority, is_replied
    FROM with_status
    ORDER BY
        CASE priority WHEN 'CRITICAL' THEN 1 WHEN 'URGENT' THEN 2 ELSE 3 END,
        days_left ASC, chain_name, platform, slug
    """
    return bq_read(sql)


# ── Assignments (BQ v2 table) ────────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner="Loading assignments...")
def load_assignments() -> pd.DataFrame:
    return bq_read(f"""
        SELECT assignment_id, review_uid, order_id, operator_email,
               chain_name, platform, days_left, status, assigned_at, completed_at
        FROM `{TABLE_ASSIGNMENTS}`
        WHERE assigned_at >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        ORDER BY assigned_at DESC
    """)


def load_my_assignments(email: str) -> pd.DataFrame:
    se = email.replace("'", "''")
    return bq_read(f"""
        SELECT assignment_id, review_uid, order_id, chain_name, platform,
               days_left, status, assigned_at, completed_at
        FROM `{TABLE_ASSIGNMENTS}`
        WHERE operator_email = '{se}'
          AND status = 'pending'
          AND assigned_at >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        ORDER BY days_left ASC
    """)


# ── Response configs (existing, read-only) ───────────────────────────────────

@st.cache_data(ttl=120, show_spinner="Loading configs...")
def load_response_configs() -> pd.DataFrame:
    sql = f"""
    SELECT config_id, config_name, chain_name, paused,
           response_type, tonality,
           TO_JSON_STRING(vb_platforms) AS vb_platforms,
           TO_JSON_STRING(ratings) AS ratings,
           TO_JSON_STRING(customer_types) AS customer_types,
           TO_JSON_STRING(review_sentiments) AS review_sentiments,
           TO_JSON_STRING(feedback_presence) AS feedback_presence,
           response_text AS response_template_legacy,
           coupon_type, coupon_fixed_value, coupon_percentage_value,
           dd_coupon_type, dd_coupon_fixed_value, dd_coupon_percentage_value,
           ue_coupon_type, ue_coupon_fixed_value, ue_coupon_percentage_value,
           paraphrase, min_order_value,
           created_by, created_at, updated_by, updated_at
    FROM `{TABLE_REVIEW_RESPONSE_CFG}`
    ORDER BY paused ASC, updated_at DESC
    """
    return bq_read(sql)


def load_configs_for_matching() -> list[dict]:
    """Load configs in the format expected by find_matching_config()."""
    df = load_response_configs()
    if df.empty:
        return []

    configs = []
    for _, row in df.iterrows():
        def _parse(val):
            if pd.isna(val) or val is None:
                return []
            try:
                return json.loads(val)
            except Exception:
                return []

        platform_configs = {}
        for plat, prefix in [("Doordash", "dd"), ("UberEats", "ue")]:
            ct = row.get(f"{prefix}_coupon_type")
            if ct:
                platform_configs[plat] = {
                    "coupon_type": ct,
                    "fixed_value": row.get(f"{prefix}_coupon_fixed_value", 0) or 0,
                    "percentage_value": row.get(f"{prefix}_coupon_percentage_value", 0) or 0,
                    "min_order_value": row.get("min_order_value", 0) or 0,
                }

        configs.append({
            "config_id": row["config_id"],
            "chain_name": row["chain_name"],
            "paused": row["paused"],
            "response_type": row["response_type"],
            "tonality": row.get("tonality", "neutral"),
            "vb_platforms": _parse(row["vb_platforms"]),
            "ratings": _parse(row["ratings"]),
            "customer_types": _parse(row["customer_types"]),
            "feedback_presence": _parse(row.get("feedback_presence")),
            "b_name_ids": [],
            "created_at": str(row.get("created_at", "")),
            "platform_configs": platform_configs,
        })

    return configs


# ── Ops log (BQ v2 table) ───────────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner="Loading ops log...")
def load_ops_log() -> pd.DataFrame:
    return bq_read(f"""
        SELECT id, review_uid, platform, chain_name, action,
               operator_email, performed_by, remarks, processing_timestamp
        FROM `{TABLE_OPS_LOG}`
        ORDER BY processing_timestamp DESC
        LIMIT 500
    """)
