# Video routes
"""
OpenAI Videos compatible endpoints, extended with Morpheus fields.

- POST /videos: quote the clip on the marketplace and hold its fixed
  price, then answer queued. Opening its session and submitting the job to
  the node continue in the background; a failure there shows on GET.
  Idempotency-Key: a retried POST answers with the first video, no new hold.
- GET /videos, GET /videos/{video_id}: the caller's jobs only.
- GET /videos/{video_id}/content: 307 to the vendor's link (no proxying).

Billing: a clip is priced per second by resolution (plus audio), held when
submitted, charged when it completes and voided when it fails, expires or
is cancelled. Rate limiting: one RPM check per submit.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from ....core.config import settings
from ....core.direct_model_service import direct_model_service
from ....core.logging_config import get_api_logger
from ....core.model_routing import model_router
from ....db.models import User, APIKey, VideoJobStatus
from ....dependencies import get_api_key_user, get_current_api_key
from ....services.rate_limiting import rate_limit_service, RateLimitResult, RateLimitStatus
from ....services.video import jobs as video_jobs
from ....services.video.errors import (
    VideoError,
    VideoParamError,
    video_disabled_error,
    video_not_found_error,
)
from ....services.video.params import (
    MAX_IMAGE_BYTES,
    PUBLIC_STATUS,
    image_data_uri,
    parse_video_request,
    to_video_object,
)
from .models import Video, VideoList, create_request_body

logger = get_api_logger()

# Largest accepted POST body: a 10 MiB first frame as base64 JSON, plus the fields
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_IDEMPOTENCY_KEY_CHARS = 255
LIST_LIMIT_DEFAULT = 20
LIST_LIMIT_MAX = 100

_FORM_CONTENT_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")

VIDEO_DISABLED_DOC = {"description": "Video generation is disabled (error.type video_disabled)"}


async def videos_enabled() -> None:
    """Kill switch: VIDEO_JOBS_ENABLED=false answers 410 to new videos.

    Reads stay open: the sweeper keeps settling clips already submitted, so
    their owners must still be able to fetch them.
    """
    if not settings.VIDEO_JOBS_ENABLED:
        raise video_disabled_error()


router = APIRouter(tags=["Videos"])


def _idempotency_key(request: Request) -> Optional[str]:
    """The Idempotency-Key header (the OpenAI SDK sends one per request and
    reuses it on its retries), or None."""
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_CHARS:
        raise VideoParamError(
            f"Idempotency-Key is longer than {MAX_IDEMPOTENCY_KEY_CHARS} characters", "Idempotency-Key"
        )
    return key


def _body_too_large() -> VideoError:
    return VideoError(413, "request_too_large", f"The request body is larger than {MAX_BODY_BYTES} bytes")


async def _read_body(request: Request) -> bytes:
    """The raw body, refused with 413 as soon as it passes MAX_BODY_BYTES,
    whether or not it declared a Content-Length (a chunked body does not)."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise _body_too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise _body_too_large()
    return bytes(body)


async def _read_create_body(request: Request) -> Tuple[Dict[str, Any], Optional[str]]:
    """The POST /videos fields, and the uploaded input_reference as a data URI."""
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json" and content_type not in _FORM_CONTENT_TYPES:
        raise VideoError(415, "invalid_request",
                         "Send the video request as multipart/form-data or application/json")

    body = await _read_body(request)
    if content_type == "application/json":
        try:
            fields = json.loads(body)
        except ValueError:
            raise VideoParamError("The request body is not valid JSON")
        if not isinstance(fields, dict):
            raise VideoParamError("The request body must be a JSON object")
        return fields, None

    # Starlette's form parser reads a replay of the bounded body
    async def replay_body() -> Dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        form = await Request(request.scope, replay_body).form()
    except (StarletteHTTPException, MultiPartException, ValueError) as e:
        # ValueError: python-multipart's own parse errors, which Starlette passes through
        detail = getattr(e, "detail", None) or getattr(e, "message", None) or "unreadable"
        raise VideoParamError(f"The form body is malformed: {detail}")
    try:
        fields: Dict[str, Any] = {}
        reference_image = None
        for key, value in form.items():
            if not isinstance(value, StarletteUploadFile):
                fields[key] = value
            elif key == "input_reference":
                data = await value.read(MAX_IMAGE_BYTES + 1)
                reference_image = image_data_uri(value.content_type, data)
            else:
                raise VideoParamError(f"{key} must be a text field", key)
    finally:
        await form.close()
    return fields, reference_image


async def _check_rate_limit(
    user: User,
    db_api_key: APIKey,
    model: Optional[str],
    request_id: str,
    video_logger,
) -> RateLimitResult:
    """One RPM check per submit (estimated_tokens=1), keyed by API key as chat is."""
    result = await rate_limit_service.check_rate_limit(
        user_id=f"key:{db_api_key.key_prefix}",
        model=model or None,
        estimated_tokens=1,
        request_id=request_id,
        multiplier=getattr(user, "rate_limit_multiplier", 1.0) or 1.0,
    )
    # A limiter-internal ERROR fails open unless RATE_LIMIT_FAIL_CLOSED, as in chat
    if not result.allowed and (
        result.status != RateLimitStatus.ERROR or settings.RATE_LIMIT_FAIL_CLOSED
    ):
        by_requests = result.status == RateLimitStatus.EXCEEDED_RPM
        video_logger.warning("Rate limit exceeded",
                             api_key_prefix=db_api_key.key_prefix,
                             model=model,
                             limit_type="rpm" if by_requests else "tpm",
                             rpm_current=result.rpm_current,
                             rpm_limit=result.rpm_limit,
                             retry_after=result.retry_after,
                             event_type="rate_limit_exceeded")
        if by_requests:
            message = (f"Rate limit exceeded: {result.rpm_current}/{result.rpm_limit} "
                       f"requests per minute. Please retry after {result.retry_after} seconds.")
        else:
            message = (f"Rate limit exceeded: {result.tpm_current}/{result.tpm_limit} "
                       f"tokens per minute. Please retry after {result.retry_after} seconds.")
        headers = rate_limit_service.create_rate_limit_headers(result).to_dict()
        headers["Retry-After"] = str(result.retry_after)
        raise VideoError(429, "requests" if by_requests else "tokens", message,
                         code="rate_limit_exceeded", headers=headers, details={"param": None})
    return result


async def _refreshed(job, video_logger):
    """The row after a node read when it is still running; the stored row if the read fails."""
    if job.is_terminal or not job.node_job_id:
        return job
    try:
        return await video_jobs.refresh_job(job.id, client_read=True) or job
    except Exception as e:
        # The class only: a failed UPDATE's text can carry video_url
        video_logger.warning("Video refresh failed; answering with the stored state",
                             error_class=type(e).__name__,
                             event_type="video_refresh_error")
        return job


def _list_limit(value: Optional[str]) -> int:
    """GET /videos limit, parsed here so a bad value is a 400 in the {error} shape."""
    if value is None:
        return LIST_LIMIT_DEFAULT
    text = value.strip()
    if not (text.isascii() and text.isdigit() and len(text) <= 3 and 1 <= int(text) <= LIST_LIMIT_MAX):
        raise VideoParamError(f"limit must be an integer between 1 and {LIST_LIMIT_MAX}", "limit")
    return int(text)


def _list_order(value: Optional[str]) -> str:
    order = (value or "desc").strip()
    if order not in ("asc", "desc"):
        raise VideoParamError("order must be asc or desc", "order")
    return order


@router.post(
    "/videos",
    response_model=None,
    responses={
        200: {"model": Video, "description": "The video job, queued (or the first one, on an Idempotency-Key retry)"},
        400: {"description": "Invalid request (error.type invalid_request or model_not_found)"},
        402: {"description": "Insufficient credits (error.type insufficient_balance)"},
        410: VIDEO_DISABLED_DOC,
        413: {"description": "The body is larger than 16 MiB (error.type request_too_large)"},
        429: {"description": "Rate limited; see Retry-After"},
        502: {"description": "The node could not quote the clip (error.type proxy_error)"},
        503: {"description": "No provider or price for this model (error.type model_unavailable)"},
    },
    openapi_extra=create_request_body(),
    dependencies=[Depends(videos_enabled)],
)
async def create_video(
    request: Request,
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """
    Create a video job.

    Accepts multipart/form-data (what the OpenAI SDK sends) or application/json.
    The clip is priced from the marketplace quote and held, then the job is
    answered queued while its session opens and the node takes it. Poll
    GET /videos/{video_id}, then download from /content. A retry with the
    same Idempotency-Key answers with the first video and holds nothing.
    """
    request_id = str(uuid.uuid4())[:8]
    video_logger = logger.bind(endpoint="create_video", user_id=user.id, request_id=request_id)

    idempotency_key = _idempotency_key(request)
    fields, reference_image = await _read_create_body(request)
    video_request = parse_video_request(fields, reference_image=reference_image)
    video_logger.info("Video request received",
                      model=video_request.model,
                      duration_seconds=video_request.duration_seconds,
                      resolution=video_request.resolution,
                      aspect_ratio=video_request.aspect_ratio,
                      generate_audio=video_request.generate_audio,
                      has_image=bool(video_request.image_url or video_request.image_b64),
                      idempotency_key_set=idempotency_key is not None,
                      event_type="video_request_start")

    if idempotency_key:
        existing = await video_jobs.get_job_by_idempotency_key(user.id, idempotency_key)
        if existing is not None:
            video_logger.info("Idempotent retry of a video request",
                              video_id=existing.id,
                              event_type="video_idempotent_replay")
            return JSONResponse(content=to_video_object(existing), headers={"X-Request-Id": request_id})

    # ModelRoutingError (400 not found / type mismatch, 503 allowlist) goes to its global handler
    model_id = await model_router.get_target_model(video_request.model, type="VIDEO")
    model_name = await direct_model_service.get_model_name_from_id(model_id) or video_request.model

    # Limited by the catalog name, so a hex id or an alias lands in the same
    # model group as the name.
    rate_limit_result = await _check_rate_limit(
        user, db_api_key, model_name, request_id, video_logger
    )

    job = await video_jobs.submit_video(
        user_id=user.id,
        api_key_id=db_api_key.id,
        model_id=model_id,
        model_name=model_name,
        request=video_request,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )

    headers = {"X-Request-Id": request_id}
    if rate_limit_result.rpm_limit > 0:
        headers.update(rate_limit_service.create_rate_limit_headers(rate_limit_result).to_dict())
    return JSONResponse(content=to_video_object(job), headers=headers)


@router.get(
    "/videos",
    response_model=None,
    responses={
        200: {"model": VideoList, "description": "The caller's videos"},
        400: {"description": "Invalid after, limit or order (error.type invalid_request)"},
    },
)
async def list_videos(
    after: Optional[str] = Query(None, description="List the videos after this video id"),
    limit: Optional[str] = Query(None, description="Number of videos, 1 to 100 (default 20)"),
    order: Optional[str] = Query(None, description="asc or desc by created_at (default desc)"),
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """List the caller's videos."""
    limit = _list_limit(limit)
    order = _list_order(order)

    rows, has_more = await video_jobs.list_jobs_for_user(user.id, after=after, limit=limit, order=order)
    rates = await video_jobs.render_rates_for(rows)
    data = [to_video_object(row, rates.get((row.model_name, row.resolution))) for row in rows]
    return JSONResponse(content={
        "object": "list",
        "data": data,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
        "has_more": has_more,
    })


@router.get(
    "/videos/{video_id}",
    response_model=None,
    responses={
        200: {"model": Video, "description": "The video job"},
        404: {"description": "No such video for this caller (error.type not_found)"},
    },
)
async def retrieve_video(
    video_id: str,
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """Get a video job. A running job is read from the node first."""
    video_logger = logger.bind(endpoint="retrieve_video", user_id=user.id, video_id=video_id)
    job = await video_jobs.get_job_for_user(video_id, user.id)
    if job is None:
        raise video_not_found_error()
    job = await _refreshed(job, video_logger)
    rates = await video_jobs.render_rates_for([job])
    return JSONResponse(content=to_video_object(job, rates.get((job.model_name, job.resolution))))


@router.get(
    "/videos/{video_id}/content",
    response_model=None,
    responses={
        307: {"description": "Redirect to the clip at the vendor (a short-lived link)"},
        400: {"description": "Unsupported variant (error.type unsupported_variant)"},
        404: {"description": "No such video for this caller (error.type not_found)"},
        409: {"description": "The video is not completed (error.type video_not_ready)"},
        410: {"description": "The download link expired (error.type video_expired)"},
    },
)
async def download_video_content(
    video_id: str,
    variant: str = Query("video", description="Only video is available"),
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """Download a completed clip: a 307 to the vendor's link, which is not proxied."""
    video_logger = logger.bind(endpoint="download_video_content", user_id=user.id, video_id=video_id)
    job = await video_jobs.get_job_for_user(video_id, user.id)
    if job is None:
        raise video_not_found_error()
    if variant != "video":
        raise VideoError(400, "unsupported_variant", "Only the video variant is available",
                         details={"param": "variant"})

    job = await _refreshed(job, video_logger)
    if job.status != VideoJobStatus.completed.value:
        raise VideoError(409, "video_not_ready",
                         f"The video is {PUBLIC_STATUS.get(job.status, 'queued')}, not completed")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not job.video_url or (job.expires_at is not None and job.expires_at <= now):
        raise VideoError(410, "video_expired", "The video download link has expired")

    # Never log the link: it is a vendor bearer URL
    video_logger.info("Redirecting to video content", event_type="video_content_redirect")
    return RedirectResponse(
        job.video_url,
        status_code=307,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
