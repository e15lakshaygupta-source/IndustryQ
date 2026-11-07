def notification_count(request):
    if not request.user.is_authenticated:
        return {"unread_notification_count": 0}
    try:
        return {"unread_notification_count": request.user.notifications.filter(is_read=False).count()}
    except Exception:
        return {"unread_notification_count": 0}
