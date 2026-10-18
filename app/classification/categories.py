"""Category definitions and Gmail label mapping."""

from enum import StrEnum


class Category(StrEnum):
    PRIORITY = "Priority"
    NEWSLETTER = "Newsletter"
    MONITORING = "Monitoring"
    OTHER = "Other"


CATEGORY_LABELS: dict[Category, str] = {
    Category.PRIORITY: "Inbox Manager/Priority",
    Category.NEWSLETTER: "Inbox Manager/Newsletter",
    Category.MONITORING: "Inbox Manager/Monitoring",
    Category.OTHER: "Inbox Manager/Other",
}
