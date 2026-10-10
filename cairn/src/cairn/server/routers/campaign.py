"""Project-level campaign synthesis router.

This router exposes a read-only aggregation endpoint that summarizes a single
project's current main line, strongest findings, blockers, and next steps. It
derives the result from existing core and product tables and does not mutate
project state.
"""

from __future__ import annotations

from fastapi import APIRouter

from cairn.server.campaign_models import CampaignSynthesis
from cairn.server.campaign_service import build_campaign_synthesis

router = APIRouter(prefix="/api/projects/{project_id}/campaign", tags=["campaign"])


@router.get("", response_model=CampaignSynthesis)
def get_campaign_synthesis(project_id: str) -> CampaignSynthesis:
    return build_campaign_synthesis(project_id)
