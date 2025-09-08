# app/schemas/referral.py
from pydantic import BaseModel

class ReferralInfo(BaseModel):
    referral_link: str
    pending_referrals: int  # Сколько человек зарегистрировалось, но еще не купило
    completed_referrals: int # Сколько человек совершили первую покупку
    total_earned: int       # Сколько всего баллов заработано