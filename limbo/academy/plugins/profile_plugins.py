from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="skills",
    title="Skills & Certificates",
    order=30,
)
class SkillsProfilePlugin(ProfilePlugin):
    template_name = "academy/profile_plugins/skills.html"
    section_icon = "fa-solid fa-award"

    def is_visible_for_profile(self, **kwargs):
        profile = self.get_profile_from_kwargs(**kwargs)
        return (
            hasattr(profile, "academy_student")
            or profile.academy_certificates.exists()
        )

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        profile = kwargs["profile"]

        try:
            student = profile.academy_student
            badges = student.badges.select_related("group").order_by("group__order", "order")
        except Exception:
            badges = []

        context["student_badges"] = badges
        context["certificates"] = profile.academy_certificates.select_related("course").order_by("-granted_at")
        return context
