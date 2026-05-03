async def apply_fix(ctx, scan_id: str, remediation_id: str):
    # Sprint 4: apply diff patch to agent bundle and enqueue new scan
    import structlog
    structlog.get_logger().info("apply_fix stub", scan_id=scan_id, remediation_id=remediation_id)
