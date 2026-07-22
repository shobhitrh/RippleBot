import logging
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from backend.src import companies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str
    domains: Optional[List[str]] = None


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    domains: Optional[List[str]] = None


@router.get("")
async def list_companies():
    """List registered companies (tenants) for the company selector."""
    return companies.list_companies()


@router.post("")
async def create_company(payload: CompanyCreate):
    """Create/register a company. Domains (e.g. pinelabs.com) drive Fireflies routing."""
    return companies.add_company(payload.name, payload.domains)


@router.patch("/{company_id}")
async def update_company(company_id: str, payload: CompanyUpdate):
    """Edit a company's settings (display name, Fireflies domain map). Id is fixed."""
    rec = companies.update_company(company_id, payload.name, payload.domains)
    if rec is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return rec
