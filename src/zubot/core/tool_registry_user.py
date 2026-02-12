"""User-specific tool registrations layered on top of core registry."""

from __future__ import annotations

from typing import Any

from src.zubot.tools.kernel.hasdata_indeed import get_indeed_job_detail, get_indeed_jobs


def register_user_specific_tools(registry: Any, tool_spec_cls: Any) -> None:
    """Register personalized/user-specific tools into an existing registry."""
    registry.register(
        tool_spec_cls(
            name="get_indeed_jobs",
            handler=get_indeed_jobs,
            category="kernel",
            description="Get Indeed job listings via HasData (fixed: domain=www.indeed.com, sort=date).",
            parameters={
                "keyword": {"type": "string", "required": True},
                "location": {"type": "string", "required": True},
            },
        )
    )
    registry.register(
        tool_spec_cls(
            name="get_indeed_job_detail",
            handler=get_indeed_job_detail,
            category="kernel",
            description="Get detailed Indeed job info via HasData job endpoint.",
            parameters={"url": {"type": "string", "required": True}},
        )
    )

