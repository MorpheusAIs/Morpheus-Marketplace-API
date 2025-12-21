    # Indexes
    __table_args__ = (
        Index("ix_api_credits_user_id_balance", "user_id", "balance"),
    )

