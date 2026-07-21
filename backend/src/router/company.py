import logging
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter
from backend.src import companies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str
    domains: Optional[List[str]] = None


@router.get("")
async def list_companies():
    """List registered companies (tenants) for the company selector."""
    return companies.list_companies()


@router.post("")
async def create_company(payload: CompanyCreate):
    """Create/register a company. Domains (e.g. pinelabs.com) drive Fireflies routing."""
    return companies.add_company(payload.name, payload.domains)
