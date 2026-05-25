from django.forms import Textarea
from django.utils.safestring import mark_safe


class AceYamlWidget(Textarea):
    """Textarea replaced by an Ace editor (YAML mode)."""

    class Media:
        js = ["https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/ace.js"]

    def render(self, name, value, attrs=None, renderer=None):
        if attrs is None:
            attrs = {}
        field_id = attrs.get("id", f"id_{name}")
        ace_div_id = f"ace_editor_{field_id}"
        attrs["style"] = "display:none"
        textarea_html = super().render(name, value, attrs, renderer)
        init_value = (value or "").replace("\\", "\\\\").replace("`", "\\`")
        return mark_safe(f"""
<div id="{ace_div_id}" style="height:500px;border:1px solid #ccc;border-radius:4px"></div>
{textarea_html}
<script>
(function() {{
  function initAce() {{
    if (typeof ace === 'undefined') {{ setTimeout(initAce, 50); return; }}
    var editor = ace.edit("{ace_div_id}");
    editor.setTheme("ace/theme/monokai");
    editor.session.setMode("ace/mode/yaml");
    editor.setFontSize(13);
    editor.setValue(`{init_value}`, -1);
    var ta = document.getElementById("{field_id}");
    editor.session.on('change', function() {{ ta.value = editor.getValue(); }});
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initAce);
  }} else {{
    initAce();
  }}
}})();
</script>
""")
