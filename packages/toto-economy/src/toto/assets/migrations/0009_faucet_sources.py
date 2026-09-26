"""Every increase of a balance is a faucet payout (2026-09-26).

Faucets gain a source and a Community; payouts name their faucet and
recipient directly (the member link stays for the members' sweep) and are
unique per (faucet, recipient, period); runs may name their faucet. Existing
member payouts are filled in from their membership.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def fill_from_members(apps, schema_editor):
    FaucetPayout = apps.get_model("assets", "FaucetPayout")
    for payout in FaucetPayout.objects.select_related("member").iterator():
        if payout.member_id and (payout.faucet_id is None or payout.recipient_id is None):
            payout.faucet_id = payout.member.faucet_id
            payout.recipient_id = payout.member.user_id
            payout.save(update_fields=["faucet", "recipient"])


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0008_faucetrun"),
        ("socialhub", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="faucet", name="source",
            field=models.CharField(choices=[("members", "Named members, hourly"), ("scheduled", "Scheduled regeneration"), ("automatic", "Automatic grant"), ("manual", "Manual grant")], default="members", max_length=12),
        ),
        migrations.AddField(
            model_name="faucet", name="community",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="faucets", to="socialhub.community"),
        ),
        migrations.AlterField(
            model_name="faucetpayout", name="member",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="payouts", to="assets.faucetmember"),
        ),
        migrations.AddField(
            model_name="faucetpayout", name="faucet",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="payouts", to="assets.faucet"),
        ),
        migrations.AddField(
            model_name="faucetpayout", name="recipient",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="faucet_payouts", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="faucetpayout", name="source",
            field=models.CharField(choices=[("members", "Named members, hourly"), ("scheduled", "Scheduled regeneration"), ("automatic", "Automatic grant"), ("manual", "Manual grant")], default="members", max_length=12),
        ),
        migrations.AddField(
            model_name="faucetpayout", name="community",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="faucet_payouts", to="socialhub.community"),
        ),
        migrations.AddField(
            model_name="faucetpayout", name="reason",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="faucetpayout", name="granted_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="faucetpayout", name="period_label",
            field=models.CharField(db_index=True, max_length=64),
        ),
        migrations.AlterField(
            model_name="faucetrun", name="period_label",
            field=models.CharField(db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="faucetrun", name="faucet",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="runs", to="assets.faucet"),
        ),
        migrations.RunPython(fill_from_members, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="faucetpayout", name="assets_one_payout_per_member_hour",
        ),
        migrations.AddConstraint(
            model_name="faucetpayout",
            constraint=models.UniqueConstraint(fields=("faucet", "recipient", "period_label"), name="assets_one_payout_per_recipient_period"),
        ),
        migrations.AddIndex(
            model_name="faucetpayout",
            index=models.Index(fields=["faucet", "-created_at"], name="assets_fauc_faucet__faba1f_idx"),
        ),
    ]
