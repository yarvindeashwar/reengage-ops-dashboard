"""
AI response generator — ported from backend's response_generator.py.
Uses GPT-4.1-nano via OpenAI API. Saves to PostgreSQL review_responses table.
"""

import json
import time
import random
import logging
from datetime import datetime

import pandas as pd
from openai import OpenAI

from secret_manager import get_openai_api_key
from pg_db import get_pg_session
from models import ReviewResponse

logger = logging.getLogger(__name__)

_client = None


def _get_openai_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=get_openai_api_key())
    return _client


# ── Prompt (same as backend) ─────────────────────────────────────────────────

RESPONSE_PROMPT = """
You are an AI assistant for a restaurant chain for online orders, responding to customer reviews with a **{tone}** tone. Your response should be natural and **final**, with **no placeholders**.

### **Review Details**
- **Customer Name:** {customer_name}
- **Review Rating:** {rating_display}
- **Review Feedback:** {review_text}
- **Coupon Given:** ${coupon} (will be "None" if not applicable)
{item_reviews_point}
{items_point}

### **Response Guidelines**
- **DO NOT** ask the customer any questions. This is a one-time reply.
- If the review is **positive (4-5 stars, Thumbs Up, or Loved)**:
  - Thank the customer warmly and close with a friendly note.
- If the review is **neutral (3 stars, Thumbs Up)**:
  - Acknowledge the feedback and state that their input is valued.
- If the review is **negative (1-2 stars or Thumbs Down)**:
  - Apologize genuinely and show concern.
  - If it's a **service issue** (slow delivery, rude staff), reassure them that it will be addressed.
  - If it's a **food issue** (cold food, wrong order, hair in food), acknowledge the issue and close with an appropriate resolution.
  - If it's a **serious issue** (food poisoning, allergic reaction, legal threat), state that the issue is being taken seriously and close with urgency.

### **Review Feedback Guidelines**
- **NEVER** mention / address the feedback if it is not present.
- **ONLY** acknowledge and address specific issues mentioned in the review feedback if feedback is present.

### **Item Feedback Guidelines**
- **NEVER** mention / address the item reviews if it is not present.
- **ONLY** acknowledge and address specific issues mentioned in the item reviews if item reviews are present.

### **Coupon Guidelines**
- **Currency is "$" / "USD" / "US Dollar"**
- **Coupon value is "{coupon}"**
- **IF** coupon is "None", "0", "$0", empty, or zero → **DO NOT** mention any coupon, discount, or offer
- **IF** coupon is a positive number (like "5", "10", "15") → **YOU MUST** include it naturally in your response
- **REMEMBER**: The coupon value "{coupon}" tells you exactly what to do

### **Critical Name Requirement**
- **MUST** include the exact customer name "{customer_name}" in your response.
- **DO NOT** change, modify, or substitute the customer name with any other name.

### **Important Constraints**
- Your response **MUST** be within **{char_limit} characters**.
- Keep it **concise but meaningful**.
- Ensure the response is **final** and **requires no manual edits**.

Now, generate a response following these guidelines.
"""


# ── Rating helpers ───────────────────────────────────────────────────────────

def get_rating_display(review: dict) -> str:
    platform = review.get("platform", "")
    rating_value = review.get("rating_value")
    star_rating = review.get("star_rating")

    if platform == "Doordash" and rating_value:
        rating_map = {
            "RATING_VALUE_THUMBS_DOWN": "Thumbs Down",
            "RATING_VALUE_THUMBS_UP": "Thumbs Up",
            "RATING_VALUE_LOVED": "Loved",
            "RATING_VALUE_FIVE": "5 stars",
        }
        return rating_map.get(rating_value, rating_value)

    if star_rating is not None:
        try:
            sr = int(float(star_rating))
            if sr > 0:
                return f"{sr} stars"
        except (ValueError, TypeError):
            pass

    return "No rating"


def get_sentiment_from_rating(review: dict) -> str:
    platform = review.get("platform", "")
    rating_value = review.get("rating_value")
    star_rating = review.get("star_rating")

    if platform == "Doordash" and rating_value:
        if rating_value == "RATING_VALUE_THUMBS_DOWN":
            return "negative"
        elif rating_value == "RATING_VALUE_THUMBS_UP":
            return "neutral"
        elif rating_value in ("RATING_VALUE_LOVED", "RATING_VALUE_FIVE"):
            return "positive"

    if star_rating is not None:
        try:
            sr = int(float(star_rating))
            if sr <= 2:
                return "negative"
            elif sr == 3:
                return "neutral"
            elif sr >= 4:
                return "positive"
        except (ValueError, TypeError):
            pass

    return "positive"


def adjust_tone_by_sentiment(sentiment: str, default_tone: str) -> str:
    return {
        "negative": "apologetic",
        "neutral": "professional",
        "positive": default_tone,
    }.get(sentiment, "professional")


# ── Config matching (same as backend's response_helpers.py) ──────────────────

def find_matching_config(review: dict, configurations: list[dict]) -> dict | None:
    """Find the best matching config for a review."""
    review_location = review.get("b_name_id")
    review_star_rating = review.get("star_rating")
    review_rating_value = review.get("rating_value")
    review_merchant = review.get("platform")
    review_customer_type = review.get("customer_type")
    review_text = review.get("review_text") or ""
    review_feedback_type = "with_feedback" if review_text.strip() else "without_feedback"

    matching = []
    for cfg in configurations:
        if cfg.get("paused", False):
            continue

        feedback_list = cfg.get("feedback_presence", ["with_feedback", "without_feedback"])
        if review_feedback_type not in feedback_list:
            continue

        rating_matches = False
        ratings = cfg.get("ratings", [])
        if not ratings:
            rating_matches = True
        else:
            if review_star_rating is not None and str(review_star_rating) in ratings:
                rating_matches = True
            if review_rating_value is not None and review_rating_value in ratings:
                rating_matches = True
        if not rating_matches:
            continue

        b_name_ids = cfg.get("b_name_ids", [])
        if b_name_ids and (review_location is None or review_location not in b_name_ids):
            continue

        platforms = cfg.get("vb_platforms", [])
        if platforms and (review_merchant is None or review_merchant not in platforms):
            continue

        customer_types = cfg.get("customer_types", [])
        if customer_types and (review_customer_type is None or review_customer_type not in customer_types):
            continue

        matching.append(cfg)

    if not matching:
        return None

    matching.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return matching[0]


# ── Generation ───────────────────────────────────────────────────────────────

def generate_ai_response(review: dict, config: dict, coupon_value: float) -> tuple[str, str | None]:
    """Generate an AI response for a single review. Returns (response_text, errors)."""
    client = _get_openai_client()
    char_limit = 300 if "Doordash" in review.get("platform", "") else 500

    sentiment = get_sentiment_from_rating(review)
    default_tone = config.get("tonality", "neutral")
    tone = adjust_tone_by_sentiment(sentiment, default_tone)
    rating_display = get_rating_display(review)

    # Format coupon
    coupon_str = "None"
    if coupon_value and coupon_value > 0:
        coupon_str = str(int(coupon_value)) if float(coupon_value).is_integer() else str(coupon_value)

    items_point = ""
    item_reviews_point = ""
    if review.get("items") and review.get("item_reviews"):
        items_point = f"- **Item Ordered:** {review.get('items', '')}"
        item_reviews_point = f"- **Item Reviews:** {review.get('item_reviews', '')}"

    prompt = RESPONSE_PROMPT.format(
        tone=tone,
        customer_name=review.get("customer_name", "Customer"),
        rating_display=rating_display,
        review_text=review.get("review_text", ""),
        coupon=coupon_str,
        item_reviews_point=item_reviews_point,
        items_point=items_point,
        char_limit=char_limit,
    )

    # Attempt generation with retries (same as backend)
    response_text = ""
    for attempt in range(3):
        resp = client.chat.completions.create(
            model="gpt-4.1-nano",
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = resp.choices[0].message.content.strip()
        if len(response_text) <= char_limit:
            break
        time.sleep(1)

    response_text = response_text[:char_limit]

    # Validate (same as backend)
    errors = validate_response(response_text, review, coupon_str)

    return response_text, errors


def validate_response(response: str, review: dict, coupon: str) -> str | None:
    errors = []
    customer_name = review.get("customer_name")
    if customer_name and response and customer_name not in response:
        errors.append("Customer name not found in response")
    if response and "$0" in response:
        errors.append("$0 coupon is found in response")
    if coupon and coupon != "None" and coupon != "0":
        if response and f"${coupon}" not in response:
            errors.append(f"Coupon value ${coupon} not found in response")
    return "|".join(errors) if errors else None


# ── Save to PostgreSQL ───────────────────────────────────────────────────────

def save_response_to_db(review: dict, response_text: str, errors: str | None,
                        config_id: int | None, coupon_value: float | None,
                        min_order_value: float | None, response_type: str) -> ReviewResponse | None:
    """Save generated response to review_responses table."""
    session = get_pg_session()
    try:
        # Check if response already exists for this order
        existing = session.query(ReviewResponse).filter(
            ReviewResponse.order_id == review.get("order_id")
        ).first()

        if existing:
            # Update existing
            existing.response_text = response_text
            existing.response_type = response_type
            existing.config_id = config_id
            existing.coupon_value = coupon_value
            existing.min_order_value = min_order_value
            existing.generated_at = datetime.utcnow()
            existing.response_sent = None
            existing.errors = errors
            session.commit()
            session.refresh(existing)
            return existing

        # Create new
        order_ts = review.get("event_timestamp")
        review_ts = review.get("event_timestamp")

        rr = ReviewResponse(
            platform=review.get("platform"),
            slug=review.get("slug"),
            b_name_id=review.get("b_name_id"),
            store_id=review.get("store_id"),
            order_id=review.get("order_id"),
            order_timestamp=order_ts if not pd.isna(order_ts) else None,
            review_timestamp=review_ts if not pd.isna(review_ts) else None,
            review_id=review.get("review_id"),
            customer_name=review.get("customer_name"),
            rating=_safe_int(review.get("star_rating")),
            review_text=review.get("review_text"),
            response_text=response_text,
            response_type=response_type,
            generated_at=datetime.utcnow(),
            config_id=config_id,
            coupon_value=coupon_value,
            min_order_value=min_order_value,
            errors=errors,
            items=review.get("items"),
            item_reviews=review.get("item_reviews"),
        )
        session.add(rr)
        session.commit()
        session.refresh(rr)
        return rr

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to save response for order {review.get('order_id')}: {e}")
        return None
    finally:
        session.close()


def _safe_int(val):
    if val is None:
        return None
    try:
        v = int(float(val))
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


# ── Public API ───────────────────────────────────────────────────────────────

def generate_and_save(review: dict, config: dict) -> dict:
    """
    Generate AI response for a single review and save to DB.
    Returns dict with response_text, errors, coupon_value.
    """
    # Get coupon from platform-specific config
    platform = review.get("platform", "")
    platform_configs = config.get("platform_configs", {})
    platform_config = platform_configs.get(platform, {})

    if platform_config and platform_config.get("coupon_type"):
        if platform_config["coupon_type"] == "FIXED":
            coupon_value = platform_config.get("fixed_value", 0.0)
        else:
            coupon_value = platform_config.get("percentage_value", 0.0)
    else:
        coupon_value = 0.0

    min_order_value = 0.0
    if platform_config:
        min_order_value = platform_config.get("min_order_value", 0.0)

    response_type = config.get("response_type", "AI")

    response_text, errors = generate_ai_response(review, config, coupon_value)

    saved = save_response_to_db(
        review=review,
        response_text=response_text,
        errors=errors,
        config_id=config.get("config_id"),
        coupon_value=coupon_value,
        min_order_value=min_order_value,
        response_type=response_type,
    )

    return {
        "response_text": response_text,
        "errors": errors,
        "coupon_value": coupon_value,
        "saved": saved is not None,
        "response_id": saved.id if saved else None,
    }
