# app/services/spend_cleanup.py
import logging
from datetime import datetime, timedelta, timezone
from app.db.session import SessionLocal
from app.crud import loyalty as crud_loyalty
from app.models.loyalty import LoyaltyTransaction

logger = logging.getLogger(__name__)

PENDING_LIFETIME_MINUTES = 30 # Время жизни "подвисшего" резерва

def cleanup_pending_spends_task():
    """
    Находит и отменяет "подвисшие" транзакции резервирования баллов,
    которые не были подтверждены или отменены из-за сбоя.
    """
    logger.info("--- Starting scheduled job: Cleanup Pending Spends ---")
    
    with SessionLocal() as db:
        try:
            time_threshold = datetime.now(timezone.utc) - timedelta(minutes=PENDING_LIFETIME_MINUTES)
            
            # Находим все старые "резервные" транзакции
            stale_pending_txs = db.query(LoyaltyTransaction).filter(
                LoyaltyTransaction.type == 'order_pending_spend',
                LoyaltyTransaction.created_at < time_threshold
            ).all()

            if not stale_pending_txs:
                logger.info("No stale pending spend transactions found.")
                return

            logger.warning(f"Found {len(stale_pending_txs)} stale pending spend transactions to refund.")
            
            for tx in stale_pending_txs:
                # Создаем компенсирующую транзакцию
                crud_loyalty.create_transaction(
                    db,
                    user_id=tx.user_id,
                    points=abs(tx.points),
                    type="spend_refund",
                    related_transaction_id=tx.id
                )
                # Меняем тип старой, чтобы она больше не попадала в выборку
                tx.type = "order_spend_failed"

            db.commit()
            logger.info("Stale pending spends have been successfully refunded.")
            
        except Exception as e:
            logger.error("An error occurred during pending spend cleanup task", exc_info=True)
            db.rollback()

    logger.info("--- Finished scheduled job: Cleanup Pending Spends ---")