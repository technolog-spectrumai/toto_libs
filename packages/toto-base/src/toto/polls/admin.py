"""The operator's view of a question — read-mostly on purpose.

Ballots are readonly here and cannot be added. A vote whose record can be
edited in the admin is not a record, and the whole reason Business Center's
resolutions can live on this engine is that the ballot table is append-only in
practice as well as in principle.
"""

from django.contrib import admin

from .models import Ballot, Choice, Decision, Question


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 2


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "status", "scope_type", "scope_id",
                    "opens_at", "closes_at")
    list_filter = ("kind", "status", "scope_type", "revisability", "visibility")
    search_fields = ("title", "question_text", "slug", "scope_id")
    readonly_fields = ("created_at", "updated_at", "closed_at", "slug")
    inlines = [ChoiceInline]


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ("question", "voter", "choice", "weight", "cast_at",
                    "revisions")
    list_filter = ("question__kind",)
    search_fields = ("voter__username", "question__title")
    readonly_fields = [f.name for f in Ballot._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # A superuser deleting one ballot rewrites a tally that was already
        # read. The model raises on formal ballots anyway; this closes the
        # button for polls too.
        return False


@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    """The ledger, behind glass. The model raises on save and delete; this
    keeps the admin from even offering the forms."""

    list_display = ("title", "outcome", "winner_label", "scope_type",
                    "scope_id", "decided_at", "decided_by")
    list_filter = ("outcome", "scope_type")
    search_fields = ("title", "scope_id")
    readonly_fields = [f.name for f in Decision._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# -- quizzes ------------------------------------------------------------------

from .quiz_models import (Quiz, QuizAnswer, QuizAttempt,  # noqa: E402
                          QuizCertificate, QuizQuestion)


class QuizQuestionInline(admin.TabularInline):
    model = QuizQuestion
    extra = 1
    fields = ("text", "is_multiple_choice", "points", "position")
    show_change_link = True


class QuizAnswerInline(admin.TabularInline):
    model = QuizAnswer
    extra = 2
    fields = ("text", "is_correct", "position")


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    """The authoring surface. A quiz is written here; it is TAKEN on the
    pages, and what attempts recorded can never be edited anywhere."""

    list_display = ("title", "scope_type", "scope_id", "pass_mark",
                    "max_attempts", "is_active", "created_by")
    list_filter = ("is_active", "scope_type")
    search_fields = ("title", "slug")
    readonly_fields = ("slug", "created_at", "updated_at")
    inlines = [QuizQuestionInline]


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "quiz", "is_multiple_choice", "points", "position")
    list_filter = ("quiz",)
    inlines = [QuizAnswerInline]


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    """Read-only: an attempt is a graded fact."""

    list_display = ("quiz", "user", "number", "score", "max_score",
                    "percent", "passed", "finished_at")
    list_filter = ("quiz", "passed")
    readonly_fields = [f.name for f in QuizAttempt._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuizCertificate)
class QuizCertificateAdmin(admin.ModelAdmin):
    """The issued paper, behind glass — the model raises on save and delete."""

    list_display = ("quiz_title", "user", "percent", "passed", "serial",
                    "issued_at")
    list_filter = ("passed",)
    search_fields = ("quiz_title", "serial", "user__username")
    readonly_fields = [f.name for f in QuizCertificate._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# -- electorates and consensus rules (stages 5, 7) ---------------------------

from .electorate_models import (ConsensusProfile, Electorate,  # noqa: E402
                                ElectorateMember, QuorumRule, RollEntry,
                                VoteExclusion, VoteProcedure)


class ElectorateMemberInline(admin.TabularInline):
    model = ElectorateMember
    extra = 2
    raw_id_fields = ("user",)
    fields = ("user", "weight")


@admin.register(Electorate)
class ElectorateAdmin(admin.ModelAdmin):
    """Where a roll is configured. Members and weights — never shares."""

    list_display = ("name", "kind", "scope_type", "scope_id", "is_active")
    list_filter = ("kind", "is_active", "scope_type")
    search_fields = ("name", "slug")
    readonly_fields = ("slug", "created_at", "updated_at")
    inlines = [ElectorateMemberInline]


@admin.register(ConsensusProfile)
class ConsensusProfileAdmin(admin.ModelAdmin):
    """Staff only, by Django's own permission: a named threshold is policy.

    Editing one never rewrites history — a vote snapshots the name AND the
    percentage when it opens.
    """

    list_display = ("name", "percent", "is_active")
    list_filter = ("is_active",)
    readonly_fields = ("slug",)


@admin.register(RollEntry)
class RollEntryAdmin(admin.ModelAdmin):
    """A frozen register, behind glass — the model refuses edits."""

    list_display = ("question", "label", "user", "weight", "created_at")
    search_fields = ("label", "question__title")
    raw_id_fields = ("question", "user")
    readonly_fields = [f.name for f in RollEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VoteExclusion)
class VoteExclusionAdmin(admin.ModelAdmin):
    """Who was barred from which vote, and why. Frozen once decided —
    the model refuses writes after a Decision exists."""

    list_display = ("question", "label", "user", "excluded_by", "created_at")
    search_fields = ("label", "reason", "question__title")
    raw_id_fields = ("question", "user", "excluded_by")
    readonly_fields = ("created_at",)


@admin.register(VoteProcedure)
class VoteProcedureAdmin(admin.ModelAdmin):
    """The session's snapshot, read-only: it is written with the register."""

    list_display = ("question", "electorate_weight", "represented_weight",
                    "eligible_weight", "excluded_weight", "frozen_at")
    raw_id_fields = ("question",)
    readonly_fields = [f.name for f in VoteProcedure._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(QuorumRule)
class QuorumRuleAdmin(admin.ModelAdmin):
    """Staff only, like the consensus profiles: an attendance bar is policy.
    A vote snapshots mode, name and number at open, so edits here never
    rewrite history."""

    list_display = ("name", "mode", "threshold", "is_active")
    list_filter = ("mode", "is_active")
    readonly_fields = ("slug",)
