"""
Data loaders — BigQuery for reviews/configs (read-only), local SQLite for v2 tables.
"""

import pandas as pd
import streamlit as st

from config import (
    TABLE_AUTOMATION_REVIEWS, TABLE_SLUG_AM_MAPPING,
    TABLE_REVIEW_RESPONSE_CFG, TABLE_REVIEW_RESPONSES,
)
from db import bq_read
from local_db import get_conn


# ── Reviews (BigQuery, read-only) ───────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner="Loading reviews from BigQuery...")
def load_reviews() -> pd.DataFrame:
    sql = f"""
    WITH eligible_reviews AS (
        SELECT
            COALESCE(ar.review_id, ar.order_id) AS review_uid,
            ar.order_id,
            ar.review_id,
            sm.chain AS chain_name,
            ar.platform,
            ar.slug,
            ar.store_id,
            ar.customer_name,
            CASE WHEN SAFE_CAST(ar.orders_count AS INT64) > 1 THEN 'existing' ELSE 'new' END AS customer_type,
            ar.star_rating,
            ar.rating_type,
            ar.rating_value AS rating_value_raw,
            ar.review_text,
            ar.is_replied,
            ar.replied_comment,
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

    with_status AS (
        SELECT *,
            CASE
                WHEN is_replied = TRUE THEN 'RESPONDED'
                WHEN response_sent IS NOT NULL THEN 'RESPONDED'
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
                WHEN LOWER(CAST(star_rating AS STRING)) IN ('thumbs_up', 'rating_value_thumbs_up', 'rating_value_loved') THEN 'Thumbs Up'
                WHEN LOWER(CAST(star_rating AS STRING)) IN ('thumbs_down', 'rating_value_thumbs_down') THEN 'Thumbs Down'
                WHEN SAFE_CAST(star_rating AS FLOAT64) IS NOT NULL
                    THEN CONCAT(CAST(CAST(SAFE_CAST(star_rating AS FLOAT64) AS INT64) AS STRING), ' Stars')
                ELSE CAST(star_rating AS STRING)
            END AS rating_display,
            SAFE_CAST(star_rating AS FLOAT64) AS rating_numeric,
            CASE
                WHEN platform = 'UberEats' THEN
                    CONCAT('https://merchants.ubereats.com/manager/home/', store_id, '/feedback/reviews/', order_id)
                WHEN platform = 'Doordash' THEN
                    CONCAT('https://www.doordash.com/merchant/feedback?store_id=', store_id)
                ELSE ''
            END AS portal_link
        FROM with_response
    )

    SELECT
        review_uid, order_id, review_id, chain_name, platform, slug, store_id,
        customer_name, customer_type, CAST(star_rating AS STRING) AS star_rating,
        rating_numeric, rating_display, review_text, review_date, days_left,
        portal_link, response_text, rr_response_type AS response_type, coupon_value,
        CAST(config_id AS STRING) AS config_id, status, priority, is_replied
    FROM with_status
    ORDER BY
        CASE priority WHEN 'CRITICAL' THEN 1 WHEN 'URGENT' THEN 2 ELSE 3 END,
        days_left ASC, chain_name, platform, slug
    """
    return bq_read(sql)


# ── Assignments (local SQLite) ───────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner="Loading assignments...")
def load_assignments() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT assignment_id, review_uid, order_id, operator_email,
               chain_name, platform, days_left, status, assigned_at, completed_at
        FROM assignments
        WHERE assigned_at >= datetime('now', '-14 days')
        ORDER BY assigned_at DESC
    """, conn)
    conn.close()
    return df


def load_my_assignments(email: str) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT assignment_id, review_uid, order_id, chain_name, platform,
               days_left, status, assigned_at, completed_at
        FROM assignments
        WHERE operator_email = ?
          AND status = 'pending'
          AND assigned_at >= datetime('now', '-14 days')
        ORDER BY days_left ASC
    """, conn, params=(email,))
    conn.close()
    return df


# ── Response configs (BigQuery, read-only) ───────────────────────────────────

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


# ── Ops log (local SQLite) ──────────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner="Loading ops log...")
def load_ops_log() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT id, review_uid, platform, chain_name, action,
               operator_email, performed_by, remarks, processing_timestamp
        FROM ops_log
        ORDER BY processing_timestamp DESC
        LIMIT 500
    """, conn)
    conn.close()
    return df
