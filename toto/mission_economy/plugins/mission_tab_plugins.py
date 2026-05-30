from toto.kanban.plugins.mission_tab_plugins import MissionTabPlugin


@MissionTabPlugin.plugin(key="economy", title="Economy", order=50)
class MissionEconomyTabPlugin(MissionTabPlugin):
    tab_icon = "fa-solid fa-coins"
    url_name = "mission_economy:mission_economy"
