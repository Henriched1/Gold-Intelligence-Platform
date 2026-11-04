"""Provider readiness endpoint."""

from fastapi import APIRouter
from app.data.source_registry import get_data_source_readiness

router = APIRouter(tags=["data-sources"])


@router.get("/v1/data-sources/readiness")
def data_source_readiness():
    return get_data_source_readiness()
