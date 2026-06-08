"""Payment-proof ticket lifecycle."""
from typing import Optional, List
from datetime import datetime
from sqlalchemy.future import select
from bot.models import get_db, PaymentTicket
import logging

logger = logging.getLogger(__name__)


async def create_ticket(telegram_id: int, plan_id: str, amount_inr: int, proof_file_id: str) -> PaymentTicket:
    """Create a pending payment ticket. Returns the ticket (id populated)."""
    async with get_db() as session:
        ticket = PaymentTicket(
            telegram_id=telegram_id,
            plan_id=plan_id,
            amount_inr=amount_inr,
            proof_file_id=proof_file_id,
            status="pending",
        )
        session.add(ticket)
        await session.flush()  # populate ticket.id before commit
        logger.info(f"Created payment ticket #{ticket.id} for user {telegram_id}, plan {plan_id}.")
        return ticket  # expire_on_commit=False keeps attributes readable after commit


async def get_ticket(ticket_id: int) -> Optional[PaymentTicket]:
    async with get_db() as session:
        result = await session.execute(select(PaymentTicket).filter(PaymentTicket.id == ticket_id))
        return result.scalars().first()


async def set_ticket_status(ticket_id: int, status: str, reviewed_by: Optional[int] = None, reason: Optional[str] = None) -> bool:
    async with get_db() as session:
        result = await session.execute(select(PaymentTicket).filter(PaymentTicket.id == ticket_id))
        ticket = result.scalars().first()
        if not ticket:
            return False
        ticket.status = status
        ticket.reviewed_by = reviewed_by
        ticket.reviewed_at = datetime.utcnow()
        if reason is not None:
            ticket.reject_reason = reason
        logger.info(f"Ticket #{ticket_id} -> {status} by {reviewed_by}.")
        return True


async def list_pending_tickets(limit: int = 50) -> List[PaymentTicket]:
    async with get_db() as session:
        result = await session.execute(
            select(PaymentTicket).filter(PaymentTicket.status == "pending")
            .order_by(PaymentTicket.created_at.asc()).limit(limit)
        )
        return list(result.scalars().all())
