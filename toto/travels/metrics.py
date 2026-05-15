from django.db.models import Avg, Count, ExpressionWrapper, F, DurationField

from toto.travels.models import Travel, Visit


class TravelMetricsCalculator:
    def __init__(self):
        self.travels = (
            Travel.objects
            .select_related("route")
            .prefetch_related("participants")
        )
        self.visits = (
            Visit.objects
            .select_related("participant", "location")
        )

    def percent(self, part, total):
        return round((part / total) * 100, 1) if total else 0

    def get_kpi_summary(self):
        total_travels = self.travels.count()
        reviewed_travels = self.travels.filter(score__isnull=False).count()
        total_visits = self.visits.count()
        reviewed_visits = self.visits.filter(score__isnull=False).count()

        avg_travel_score = (
            self.travels
            .filter(score__isnull=False)
            .aggregate(avg=Avg("score"))["avg"]
        )
        avg_visit_score = (
            self.visits
            .filter(score__isnull=False)
            .aggregate(avg=Avg("score"))["avg"]
        )

        avg_stay_result = (
            self.visits
            .filter(visited_at__isnull=False, ends_at__isnull=False)
            .annotate(stay=ExpressionWrapper(F("ends_at") - F("visited_at"), output_field=DurationField()))
            .aggregate(avg=Avg("stay"))["avg"]
        )
        avg_length_of_stay_display = None
        if avg_stay_result:
            total = int(avg_stay_result.total_seconds())
            if total > 0:
                days = avg_stay_result.days
                hours, rem = divmod(total % 86400, 3600)
                minutes = rem // 60
                if days > 0:
                    avg_length_of_stay_display = f"{days}d {hours}h" if hours else f"{days}d"
                elif hours > 0:
                    avg_length_of_stay_display = f"{hours}h {minutes}m" if minutes else f"{hours}h"
                else:
                    avg_length_of_stay_display = f"{minutes}m"

        return {
            "total_travels": total_travels,
            "reviewed_travels": reviewed_travels,
            "total_visits": total_visits,
            "reviewed_visits": reviewed_visits,
            "avg_travel_score": round(avg_travel_score, 1) if avg_travel_score else None,
            "avg_visit_score": round(avg_visit_score, 1) if avg_visit_score else None,
            "travel_review_rate": self.percent(reviewed_travels, total_travels),
            "visit_review_rate": self.percent(reviewed_visits, total_visits),
            "avg_length_of_stay_display": avg_length_of_stay_display,
        }

    def get_route_items(self):
        rows = (
            self.travels
            .filter(route__isnull=False)
            .values("route__id", "route__name")
            .annotate(travel_count=Count("id"), avg_score=Avg("score"))
            .order_by("-travel_count")
        )

        return [
            {
                "name": row["route__name"],
                "travel_count": row["travel_count"],
                "avg_score": round(row["avg_score"], 1) if row["avg_score"] else None,
            }
            for row in rows
        ]

    def get_destination_items(self):
        rows = (
            self.visits
            .filter(location__isnull=False)
            .values(
                "location__id",
                "location__street",
                "location__locality_name",
                "location__country_name",
            )
            .annotate(visit_count=Count("id"), avg_score=Avg("score"))
            .order_by("-visit_count")[:10]
        )

        return [
            {
                "id": row["location__id"],
                "name": row["location__street"],
                "locality": row["location__locality_name"],
                "country": row["location__country_name"],
                "visit_count": row["visit_count"],
                "avg_score": round(row["avg_score"], 1) if row["avg_score"] else None,
            }
            for row in rows
        ]

    def get_person_items(self):
        travel_map = {
            row["participants__id"]: {
                "name": row["participants__display_name"],
                "travel_count": row["travel_count"],
            }
            for row in (
                self.travels
                .filter(participants__isnull=False)
                .values("participants__id", "participants__display_name")
                .annotate(travel_count=Count("id", distinct=True))
            )
        }

        visit_map = {
            row["participant__id"]: {
                "name": row["participant__display_name"],
                "visit_count": row["visit_count"],
            }
            for row in (
                self.visits
                .filter(participant__isnull=False)
                .values("participant__id", "participant__display_name")
                .annotate(visit_count=Count("id"))
            )
        }

        all_ids = set(travel_map.keys()) | set(visit_map.keys())
        items = []

        for person_id in all_ids:
            t = travel_map.get(person_id, {})
            v = visit_map.get(person_id, {})
            items.append({
                "name": t.get("name") or v.get("name") or "Unknown",
                "travel_count": t.get("travel_count", 0),
                "visit_count": v.get("visit_count", 0),
            })

        return sorted(items, key=lambda x: x["travel_count"] + x["visit_count"], reverse=True)

    def get_score_distribution(self, queryset):
        score_counts = {
            row["score"]: row["count"]
            for row in (
                queryset
                .filter(score__isnull=False)
                .values("score")
                .annotate(count=Count("id"))
            )
        }
        max_count = max(score_counts.values(), default=0)

        return [
            {
                "score": score,
                "count": score_counts.get(score, 0),
                "percent": round((score_counts.get(score, 0) / max_count) * 100, 1) if max_count else 0,
            }
            for score in range(5, 0, -1)
        ]

    def get_context_data(self):
        return {
            **self.get_kpi_summary(),
            "route_items": self.get_route_items(),
            "destination_items": self.get_destination_items(),
            "person_items": self.get_person_items(),
            "travel_score_distribution": self.get_score_distribution(self.travels),
            "visit_score_distribution": self.get_score_distribution(self.visits),
        }
