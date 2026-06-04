from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="experiences",
    title="Experiences",
    order=20,
)
class ExperiencesProfilePlugin(ProfilePlugin):
    template_name = "competence/profile_plugins/experiences.html"
    section_icon = "fa-solid fa-briefcase"

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        profile = kwargs["profile"]

        context["experiences"] = profile.experiences.all()
        return context
