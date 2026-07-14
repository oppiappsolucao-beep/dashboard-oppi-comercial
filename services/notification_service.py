from datetime import datetime

from database.connection import SessionLocal
from database.repositories import add_notification, tenant_query


class NotificationService:
    def notify(self, tenant_id: int, user_id: int, title: str, description: str = "", link: str = ""):
        db = SessionLocal()
        try:
            add_notification(db, tenant_id, user_id, title, description, link)
            db.commit()
        finally:
            db.close()

    def check_overdue_activities(self, tenant_id: int):
        from database.models import Activity

        db = SessionLocal()
        try:
            now = datetime.utcnow()
            activities = (
                tenant_query(db, Activity, tenant_id)
                .filter(Activity.status == "Pendente", Activity.scheduled_date < now)
                .all()
            )
            for activity in activities:
                activity.status = "Atrasada"
                add_notification(
                    db,
                    tenant_id,
                    activity.assigned_user_id,
                    "Atividade vencida",
                    activity.title,
                    link=f"lead:{activity.lead_id}",
                )
            db.commit()
        finally:
            db.close()


notification_service = NotificationService()
