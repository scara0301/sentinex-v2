"""
Embeddable SVG badges (Sprint 4).

``GET /badge/{scan_id}.svg`` is intentionally unauthenticated — badges are
meant for READMEs. The scan UUID is the capability; the badge leaks only
the letter grade. Finished scans get a signed badge persisted to the
badges table; in-flight scans get an ephemeral "scanning" badge.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sentinex_core.badges import grade_for_score, render_badge_svg, sign_badge
from sentinex_core.db.repos import BadgeRepo, ScanRepo

from ..deps import get_db
from ..settings import settings

router = APIRouter()

_SVG_HEADERS = {"Cache-Control": "max-age=300"}


@router.get("/badge/{scan_id}.svg")
async def get_badge(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    scan = await ScanRepo(db).get_by_id(scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found")

    badge_repo = BadgeRepo(db)
    badge = await badge_repo.get(scan_id)
    if badge and badge.svg:
        return Response(badge.svg, media_type="image/svg+xml", headers=_SVG_HEADERS)

    if scan.status != "DONE" or scan.risk_score is None:
        svg = render_badge_svg("?", label="sentinex scan")
        return Response(
            svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache"},
        )

    score = float(scan.risk_score)
    grade = grade_for_score(score)
    svg = render_badge_svg(grade, score=score)
    signed_at = datetime.now(timezone.utc)
    try:
        await badge_repo.create(
            scan_id=scan_id,
            svg=svg.encode(),
            grade=grade,
            signed_at=signed_at,
            signature=sign_badge(settings.secret_key, str(scan_id), grade, signed_at),
        )
        await db.commit()
    except IntegrityError:
        # A concurrent request created the badge first — serve the stored one.
        await db.rollback()
        existing = await badge_repo.get(scan_id)
        if existing and existing.svg:
            return Response(
                existing.svg, media_type="image/svg+xml", headers=_SVG_HEADERS
            )
    return Response(svg, media_type="image/svg+xml", headers=_SVG_HEADERS)
