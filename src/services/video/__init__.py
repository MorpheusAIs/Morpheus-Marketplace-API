"""
Video generation jobs (/v1/videos).

- errors: VideoError, the {"error": {...}} responses of the video routes
- params: pure mappings between the OpenAI Videos API and the node's job fields
- jobs: submit, refresh and sweep of video_jobs rows
"""

from .errors import VideoError, VideoParamError

__all__ = [
    "VideoError",
    "VideoParamError",
]
