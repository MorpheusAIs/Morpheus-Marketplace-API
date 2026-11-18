# Model routes 
from fastapi import APIRouter, HTTPException, status, Query, Depends, Path
from typing import List, Dict, Any, Optional, Set
import time
import uuid

from ....schemas import openai as openai_schemas
from ....services.model_mapper import model_mapper
from ....services import proxy_router_service
from ....core.config import settings
from ....core.direct_model_service import direct_model_service
from ....core.logging_config import get_api_logger
from ....core.model_types import is_audio_model
from ....utils.error_sanitizer import sanitize_error_message
from .spec_enrichment import spec_enrichment

logger = get_api_logger()

router = APIRouter(tags=["Models"])

# Bounds on the enums copied from provider video guides into the listing
VIDEO_ENUM_MAX_ITEMS = 16
VIDEO_ENUM_MAX_CHARS = 16


def _guide_field(guide: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for field in guide.get("fields") or []:
        if isinstance(field, dict) and field.get("name") == name:
            return field
    return None


def _guide_enum(field: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """A field's enum, bounded; None when the guide does not restrict it."""
    if not field or not isinstance(field.get("enum"), list):
        return None
    return [v for v in field["enum"] if isinstance(v, str) and 0 < len(v) <= VIDEO_ENUM_MAX_CHARS]


def _guide_int(field: Optional[Dict[str, Any]], key: str) -> Optional[int]:
    value = (field or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _intersect(agreed: Optional[List[str]], values: Optional[List[str]]) -> Optional[List[str]]:
    if values is None:
        return agreed
    if agreed is None:
        return values
    return [v for v in agreed if v in values]


def video_limits(bid_details: Any, healthy_bid_ids: Set[str]) -> Optional[Dict[str, Any]]:
    """
    What every healthy bid's video guide (bidDetail[].video) accepts, for a VIDEO row.

    Guides that differ are reduced to what they agree on: enums are
    intersected, the minimum is the largest minimum and the maximum the
    smallest maximum, audio and image only when every guide offers them.
    None when no healthy bid carries a guide.
    """
    guides = []
    for bid in bid_details if isinstance(bid_details, list) else []:
        if not isinstance(bid, dict) or not isinstance(bid.get("video"), dict):
            continue
        bid_id = str(bid.get("bidId") or bid.get("bid_id") or "").strip().lower()
        if bid_id in healthy_bid_ids:
            guides.append(bid["video"])
    if not guides:
        return None

    min_seconds: Optional[int] = None
    max_seconds: Optional[int] = None
    resolutions: Optional[List[str]] = None
    aspect_ratios: Optional[List[str]] = None
    audio = image = True
    for guide in guides:
        duration = _guide_field(guide, "duration_seconds")
        low, high = _guide_int(duration, "minimum"), _guide_int(duration, "maximum")
        if low is not None:
            min_seconds = low if min_seconds is None else max(min_seconds, low)
        if high is not None:
            max_seconds = high if max_seconds is None else min(max_seconds, high)
        resolutions = _intersect(resolutions, _guide_enum(_guide_field(guide, "resolution")))
        aspect_ratios = _intersect(aspect_ratios, _guide_enum(_guide_field(guide, "aspect_ratio")))
        audio = audio and _guide_field(guide, "generate_audio") is not None
        image = image and (
            _guide_field(guide, "image_url") is not None or _guide_field(guide, "image_b64") is not None
        )

    return {
        "min_seconds": min_seconds,
        "max_seconds": max_seconds,
        "resolutions": resolutions[:VIDEO_ENUM_MAX_ITEMS] if resolutions is not None else None,
        "aspect_ratios": aspect_ratios[:VIDEO_ENUM_MAX_ITEMS] if aspect_ratios is not None else None,
        "audio": audio,
        "image": image,
    }


@router.get(
    "/models",
    response_model=None,
    responses={200: {"model": openai_schemas.MarketplaceModelList, "description": "Active models"}},
)  # Handle /api/v1/models (without trailing slash)
@router.get("/models/", response_model=None, include_in_schema=False)  # Handle /api/v1/models/ (with trailing slash) - backward compatibility, hidden from docs
async def list_models():
    """
    Get a list of active models.
    
    Response is in OpenAI API format with selected fields from the blockchain data.
    Only returns active models with available providers.
    TTS/STT models are omitted while audio endpoints are disabled (410).
    """
    try:
        models_logger = logger.bind(endpoint="list_models", event_type="models_fetch_start")
        models_logger.info("Fetching active models from direct model service")
        
        # Use DirectModelService to get active models
        active_models = await direct_model_service.get_raw_models_data()
        
        # Convert blockchain models to OpenAI format with required fields
        models = []
        for model in active_models:
            model_name = model.get("Name", "unknown-model")
            blockchain_id = model.get("Id", "")
            created_timestamp = model.get("CreatedAt", int(time.time()))
            
            # Get model tags and type
            tags = model.get("Tags", [])
            model_type = model.get("ModelType", "UNKNOWN")
            if is_audio_model(tags=tags, model_type=model_type, model_name=model_name):
                continue

            # Create simplified OpenAI-compatible model
            openai_model = {
                "id": model_name,
                "blockchainID": blockchain_id,
                "created": created_timestamp,
                "tags": tags,
                "modelType": model_type
            }
            openai_model.update(spec_enrichment(blockchain_id))
            if model_type == "VIDEO":
                healthy = await direct_model_service.get_healthy_bid_ids_for_model(blockchain_id)
                limits = video_limits(model.get("bidDetail"), healthy)
                if limits:
                    openai_model["video"] = limits
            
            models.append(openai_model)
        
        models_logger.info("Successfully fetched active models",
                          model_count=len(models),
                          event_type="models_fetch_success")
        return {"object": "list", "data": models}
    except Exception as e:
        # Check if it's an httpx error from direct_model_service
        if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
            # Handle HTTP errors and return detailed error messages  
            models_logger.error("HTTP error getting active models",
                               error=str(e),
                               status_code=e.response.status_code,
                               event_type="models_fetch_http_error")
            try:
                error_detail = e.response.json()
                if isinstance(error_detail, dict):
                    if "error" in error_detail:
                        detail_message = error_detail["error"]
                    elif "detail" in error_detail:
                        detail_message = error_detail["detail"]
                    else:
                        detail_message = str(error_detail)
                else:
                    detail_message = str(error_detail)
            except:
                detail_message = f"Status code: {e.response.status_code}, Reason: {e.response.reason_phrase}"
                
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Error fetching active models: {detail_message}"
            )
        else:
            # Handle other errors
            models_logger.error("Error getting active models",
                               error=str(e),
                               event_type="models_fetch_error")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=sanitize_error_message(f"Error fetching active models: {str(e)}")
            )

@router.get("/models/allmodels", response_model=None)
async def list_all_models():
    """
    Get a list of all available models.
    
    Response is in OpenAI API format with selected fields from the blockchain data.
    Only returns non-deleted models.
    TTS/STT models are omitted while audio endpoints are disabled (410).
    """
    try:
        allmodels_logger = logger.bind(endpoint="list_all_models", event_type="all_models_fetch_start")
        allmodels_logger.info("Fetching all models from blockchain API")
        
        # Use SDK to fetch from blockchain API
        response = await proxy_router_service.getAllModels()
        blockchain_data = response.json()
        blockchain_models = blockchain_data.get("models", [])
        
        # Filter out deleted models
        active_models = [model for model in blockchain_models if not model.get("IsDeleted", False)]
        
        allmodels_logger.info("Retrieved models from blockchain",
                             total_models=len(blockchain_models),
                             active_models=len(active_models),
                             deleted_models=len(blockchain_models) - len(active_models))
        
        # Convert blockchain models to OpenAI format with required fields
        models = []
        for model in active_models:
            model_name = model.get("Name", "unknown-model")
            blockchain_id = model.get("Id", "")
            created_timestamp = model.get("CreatedAt", int(time.time()))
            
            # Get model tags and type
            tags = model.get("Tags", [])
            model_type = model.get("ModelType", "UNKNOWN")
            if is_audio_model(tags=tags, model_type=model_type, model_name=model_name):
                continue

            # Create simplified OpenAI-compatible model
            openai_model = {
                "id": model_name,
                "blockchainID": blockchain_id,
                "created": created_timestamp,
                "tags": tags,
                "modelType": model_type
            }
            
            models.append(openai_model)
        
        allmodels_logger.info("Successfully fetched all models",
                             model_count=len(models),
                             event_type="all_models_fetch_success")
        return {"object": "list", "data": models}
    except proxy_router_service.ProxyRouterServiceError as e:
        # Handle proxy router service errors
        allmodels_logger.error("Proxy router service error getting all models",
                              error=str(e),
                              status_code=e.status_code,
                              error_type=e.error_type,
                              event_type="all_models_fetch_service_error")
        raise HTTPException(
            status_code=e.get_http_status_code(),
            detail=f"Error fetching all models from blockchain: {e.message}"
        )
    except Exception as e:
        # Handle other errors
        allmodels_logger.error("Error getting all models",
                              error=str(e),
                              event_type="all_models_fetch_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=sanitize_error_message(f"Error fetching all models: {str(e)}")
        )

@router.get("/models/ratedbids")
async def get_rated_bids(
    model_id: str = Query(..., description="The blockchain ID (hex) of the model to get rated bids for, e.g. 0x1234...")
):
    """
    Get rated bids for a specific model.
    
    Connects to the proxy-router's /blockchain/models/{id}/bids/rated endpoint.
    Note: Use the blockchain model ID (hex) not the name.
    """
    try:
        bids_logger = logger.bind(endpoint="get_rated_bids", model_id=model_id, event_type="rated_bids_fetch_start")
        bids_logger.info("Fetching rated bids for model",
                        model_id=model_id)
        
        # Use SDK to get rated bids
        response = await proxy_router_service.getRatedBids(model_id)
        result = response.json()
        bid_count = len(result) if isinstance(result, list) else len(result.get('bids', [])) if isinstance(result, dict) else 0
        
        bids_logger.info("Successfully fetched rated bids",
                       bid_count=bid_count,
                       model_id=model_id,
                       event_type="rated_bids_fetch_success")
        
        return result
    except proxy_router_service.ProxyRouterServiceError as e:
        # Handle proxy router service errors
        bids_logger.error("Proxy router service error getting rated bids",
                         error=str(e),
                         status_code=e.status_code,
                         error_type=e.error_type,
                         model_id=model_id,
                         event_type="rated_bids_fetch_service_error")
        raise HTTPException(
            status_code=e.get_http_status_code(),
            detail=f"Error fetching rated bids: {e.message}"
        )
    except Exception as e:
        # Handle other errors
        bids_logger.error("Error getting rated bids",
                         error=str(e),
                         model_id=model_id,
                         event_type="rated_bids_fetch_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=sanitize_error_message(f"Error fetching rated bids: {str(e)}")
        ) 