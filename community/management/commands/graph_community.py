from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the communities app"

    def create_collection_types(self):
        """Define CollectionTypes for community models."""
        for name in [
            "SocialEntity", "Community", "CommunityMember",
            "MembershipApplication", "ReferenceRequest"
        ]:
            CollectionType.objects.get_or_create(
                name=name,
                defaults={"json_schema": {}, "form_layout": {}}
            )

    def create_relation_types(self):
        """Define RelationTypes for community models."""
        relations = [
            ("Community-Head", {"from": "Community", "to": "CommunityMember", "type": "headed_by"}),
            ("Community-Federation", {"from": "Community", "to": "Federation", "type": "belongs_to"}),
            ("Community-Location", {"from": "Community", "to": "Address", "type": "located_at"}),
            ("Community-Members", {"from": "Community", "to": "CommunityMember", "type": "has_member"}),
            ("Member-Patron", {"from": "CommunityMember", "to": "CommunityMember", "type": "patron_of"}),
            ("Member-Address", {"from": "CommunityMember", "to": "Address", "type": "resides_at"}),
            ("Application-Community", {"from": "MembershipApplication", "to": "Community", "type": "applies_to"}),
            ("Reference-Application", {"from": "ReferenceRequest", "to": "MembershipApplication", "type": "for_application"}),
            ("Reference-Referrer", {"from": "ReferenceRequest", "to": "CommunityMember", "type": "given_by"}),
        ]
        for name, metadata in relations:
            RelationType.objects.get_or_create(name=name, defaults={"metadata": metadata})

    def create_collector_config(self):
        """Register AppCollector + Graph with descriptive config."""
        communities_config = {
            "models": {
                "SocialEntity": {
                    "collection_type": "SocialEntity",
                    "fields": ["created_at"],
                    "relations": {}
                },
                "Community": {
                    "collection_type": "Community",
                    "fields": ["name", "slug", "established_year", "updated_at", "email"],
                    "relations": {
                        "head": "Community-Head",
                        "federation": "Community-Federation",
                        "location": "Community-Location",
                        "members": "Community-Members",
                    }
                },
                "CommunityMember": {
                    "collection_type": "CommunityMember",
                    "fields": ["display_name", "bio", "avatar", "joined_date", "slug"],
                    "relations": {
                        "patron": "Member-Patron",
                        "address": "Member-Address",
                        "communities": "Community-Members",
                    }
                },
                "MembershipApplication": {
                    "collection_type": "MembershipApplication",
                    "fields": ["email", "code", "verified_at", "created_at", "expires_at", "status"],
                    "relations": {"community": "Application-Community"}
                },
                "ReferenceRequest": {
                    "collection_type": "ReferenceRequest",
                    "fields": ["message", "status", "created_at", "responded_at"],
                    "relations": {
                        "application": "Reference-Application",
                        "referrer": "Reference-Referrer",
                    }
                }
            }
        }

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.create_collector_config()
        self.stdout.write(self.style.SUCCESS("✅ Communities graph schema + AppCollector created."))
