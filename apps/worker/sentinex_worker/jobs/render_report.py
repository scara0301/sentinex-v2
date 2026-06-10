import structlog

from ..reporting import generate_report

log = structlog.get_logger()


async def render_report(ctx, scan_id: str):
    path = await generate_report(scan_id)
    log.info("render_report complete", scan_id=scan_id, path=str(path))
    return str(path)
