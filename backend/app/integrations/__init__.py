"""External integrations.

Phase 0 ships only thin stubs for FCM and APNs — enough surface for
`notification_service.send_to_user` to call into and for tests to
mock. The real provider calls (HTTP/2 to FCM, JWT + APNs HTTP/2)
land in Phase 1 when there's an actual notification trigger to
exercise them.
"""
