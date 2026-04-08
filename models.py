"""
SQLAlchemy model for review_responses table.
Mirrors the backend's ReviewResponse model exactly.
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class ReviewResponse(Base):
    __tablename__ = "review_responses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    b_name_id = Column(Integer, index=True)
    store_id = Column(String)
    order_id = Column(String, nullable=False)
    order_timestamp = Column(DateTime)
    review_timestamp = Column(DateTime)
    items = Column(Text)
    item_reviews = Column(Text)
    review_id = Column(String)
    customer_name = Column(String)
    rating = Column(Integer)
    review_text = Column(Text)
    response_text = Column(Text, nullable=False)
    response_type = Column(String, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow)
    config_id = Column(Integer)
    coupon_value = Column(Float)
    min_order_value = Column(Float)
    response_sent = Column(DateTime)
    errors = Column(Text)
