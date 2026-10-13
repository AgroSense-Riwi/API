from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_current_user
from agrosense_api.db.session import get_db
from agrosense_api.models import Farm, FarmVisit
from agrosense_api.schemas.visits import PriorityVisitResponse

router = APIRouter(
    prefix="/visits",
    tags=["farm visits"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/priority", response_model=list[PriorityVisitResponse])
async def list_priority_visits(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[PriorityVisitResponse]:
    query = (
        select(FarmVisit, Farm.code, Farm.name)
        .join(Farm, FarmVisit.farm_id == Farm.id)
        .where(FarmVisit.completed_at.is_(None))
        .order_by(FarmVisit.priority_score.desc(), FarmVisit.due_at.asc())
        .limit(limit)
    )
    rows = (await session.execute(query)).all()
    return [
        PriorityVisitResponse(
            id=visit.id,
            farm_id=visit.farm_id,
            reason=visit.reason,
            priority_score=visit.priority_score,
            due_at=visit.due_at,
            farm_code=farm_code,
            farm_name=farm_name,
        )
        for visit, farm_code, farm_name in rows
    ]
