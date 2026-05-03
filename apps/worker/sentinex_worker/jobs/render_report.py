async def render_report(ctx, scan_id: str):
    # Sprint 4: WeasyPrint PDF generation
    import structlog
    structlog.get_logger().info("render_report stub", scan_id=scan_id)
