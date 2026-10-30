"""Catalogue keys of the job types, for the *Running tasks* widget (ADR 0061 §2).

Written out literally so the catalogue test can check that every type has a Dutch and an
English label. The audit log shows the same words for the system events of a job run.
"""

from app.domain.job_plan import JobType

JOB_TYPE_LABELS: dict[JobType, str] = {
    JobType.CREATE_VIDEO_ROOM: "audit.job.create_video_room",
    JobType.DELETE_VIDEO_ROOM: "audit.job.delete_video_room",
    JobType.SEND_CONFIRMATION: "audit.job.send_confirmation",
    JobType.SEND_CHANGE: "audit.job.send_change",
    JobType.SEND_REMINDER: "audit.job.send_reminder",
    JobType.SEND_CANCELLATION: "audit.job.send_cancellation",
    JobType.DISPATCH_WXCC_TASK: "audit.job.dispatch_wxcc_task",
    JobType.END_WXCC_TASK: "audit.job.end_wxcc_task",
    JobType.PURGE_PII: "audit.job.purge_pii",
    JobType.SEND_SUSPENSION: "audit.job.send_suspension",
    JobType.SEND_CLOSURE_UPDATE: "audit.job.send_closure_update",
    JobType.SEND_REINSTATEMENT: "audit.job.send_reinstatement",
    JobType.SEND_REBOOK_REQUEST: "audit.job.send_rebook_request",
    JobType.SEND_REBOOK_REMINDER: "audit.job.send_rebook_reminder",
    JobType.SEND_REBOOK_LAPSED: "audit.job.send_rebook_lapsed",
    JobType.LAPSE_REBOOK: "audit.job.lapse_rebook",
    JobType.SEND_CONFIRM_REQUEST: "audit.job.send_confirm_request",
    JobType.SEND_CONFIRM_REMINDER: "audit.job.send_confirm_reminder",
    JobType.EXPIRE_UNCONFIRMED: "audit.job.expire_unconfirmed",
    JobType.SEND_UNCONFIRMED_LAPSED: "audit.job.send_unconfirmed_lapsed",
    JobType.SEND_TEAM_INVITE: "audit.job.send_team_invite",
    JobType.SEND_TEAM_CANCEL: "audit.job.send_team_cancel",
}
"""One label per job type; a test fails when a type is missing."""
