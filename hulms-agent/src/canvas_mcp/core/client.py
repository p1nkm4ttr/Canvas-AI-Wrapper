"""HTTP client and Canvas API utilities."""

import asyncio
import re
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final, Literal, cast
from urllib.parse import urlencode

import httpx

from .credentials import get_request_credentials, is_http_request_active
from .logging import log_debug, log_error, log_warning, sanitize_url

# Rate limit retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2

# Default number of results per page for paginated requests
DEFAULT_PAGE_SIZE = 100
API_ROOT_REST: Final = "rest"
API_ROOT_QUIZ: Final = "quiz"


def _canvas_auth_headers(api_token: str) -> dict[str, str]:
    """Build the standard Canvas auth + User-Agent headers for a token."""
    from .. import __version__

    return {
        "Authorization": f"Bearer {api_token}",
        "User-Agent": f"canvas-mcp/{__version__} (https://github.com/vishalsachdev/canvas-mcp)",
    }

def _resolve_canvas_api_root(base_api_url: str, api_root: Literal["rest", "quiz"]) -> str:
    """Resolve a configured ``…/api/v<N>`` base URL to a selected Canvas API root.

    ``rest`` keeps the configured URL unchanged. ``quiz`` rewrites only the
    trailing API version segment to ``/api/quiz/v1`` while preserving any
    institution prefix (e.g. ``/lms``). This is an explicit call-site opt-in;
    endpoint strings never select a base path implicitly.
    """
    if api_root == API_ROOT_REST:
        return base_api_url

    match = re.search(r"/api/v\d+$", base_api_url)
    if not match:
        raise ValueError(
            "Invalid Canvas API base URL for quiz root resolution: expected trailing /api/v<N>"
        )

    return f"{base_api_url[:match.start()]}/api/quiz/v1"


def _is_rate_limited(response: httpx.Response) -> bool:
    """Whether a response is Canvas throttling (retryable), not a permission error.

    Canvas signals quota exhaustion with BOTH 429 and 403 (verified on the live
    instance docs: "handle both 403 and 429 with exponential backoff"). But a
    plain 403 is usually a real permission denial (e.g. an instructor-hidden
    files tab) and must NOT be retried. The throttle 403 is distinguished by
    its body text and by the X-Rate-Limit-Remaining header hitting zero.
    """
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if "Rate Limit Exceeded" in (response.text or ""):
        return True
    remaining = response.headers.get("X-Rate-Limit-Remaining", "")
    try:
        return float(remaining) <= 0
    except ValueError:
        return False


def _link_header_next(headers: Any) -> str | None:
    """Extract the rel="next" URL from a Link header, or None.

    Header-name lookup is case-insensitive (verified API fact). Accepts httpx
    Headers or a plain dict (tests).
    """
    link_value = None
    try:
        link_value = headers.get("Link") or headers.get("link")
    except AttributeError:
        pass
    if not link_value and isinstance(headers, dict):
        for k, v in headers.items():
            if k.lower() == "link":
                link_value = v
                break
    if not link_value:
        return None
    for part in link_value.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip("<>")
        for seg in segments[1:]:
            if seg.strip().replace('"', "").replace("'", "").lower() == "rel=next":
                return url
    return None


def absolute_url(html_url: str | None) -> str | None:
    """Make a Canvas html_url absolute (verified fact: the API returns them relative)."""
    if not html_url:
        return html_url
    if html_url.startswith("http://") or html_url.startswith("https://"):
        return html_url
    from .config import get_config
    base = get_config().canvas_api_url
    origin = base.split("/api/")[0] if "/api/" in base else base.rstrip("/")
    return f"{origin}{html_url}" if html_url.startswith("/") else f"{origin}/{html_url}"


# HTTP client will be initialized with configuration
http_client: httpx.AsyncClient | None = None
_http_client_loop_ref: "weakref.ref[asyncio.AbstractEventLoop] | None" = None  # weakref to the loop that owns http_client

# Concurrency limiter for outbound Canvas API calls
_request_semaphore: asyncio.Semaphore | None = None
_semaphore_loop_ref: "weakref.ref[asyncio.AbstractEventLoop] | None" = None  # weakref to the loop that owns _request_semaphore


def _get_request_semaphore() -> asyncio.Semaphore:
    """Get or create the concurrency semaphore from config.

    Recreates the semaphore when the running event loop has changed (e.g. after
    asyncio.run() closes one loop and mcp.run() starts a new one) to avoid
    "Event loop is closed" errors on asyncio synchronization primitives.

    A weakref to the creating loop is stored so that when the old loop is
    garbage-collected (as asyncio.run() does), the weakref goes dead and we
    detect the loop change reliably without relying on object id() reuse.
    """
    global _request_semaphore, _semaphore_loop_ref
    try:
        current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    stored_loop = _semaphore_loop_ref() if _semaphore_loop_ref is not None else None

    if _request_semaphore is not None and current_loop is not None and stored_loop is not current_loop:
        _request_semaphore = None
        _semaphore_loop_ref = None

    if _request_semaphore is None:
        from .config import get_config
        _request_semaphore = asyncio.Semaphore(get_config().max_concurrent_requests)
        _semaphore_loop_ref = weakref.ref(current_loop) if current_loop is not None else None
    return _request_semaphore


def _get_http_client() -> httpx.AsyncClient:
    """Get or create the HTTP client with current configuration.

    Recreates the client when the running event loop has changed (e.g. after
    asyncio.run() closes one loop and mcp.run() starts a new one).  The stale
    client's internal anyio/asyncio connection-pool primitives are tied to the
    closed loop and will raise "Event loop is closed" on the first use.

    A weakref to the creating loop is stored so that when the old loop is
    garbage-collected (as asyncio.run() does), the weakref goes dead and we
    detect the loop change reliably without relying on object id() reuse.
    """
    global http_client, _http_client_loop_ref
    try:
        current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    stored_loop = _http_client_loop_ref() if _http_client_loop_ref is not None else None

    if http_client is not None and (
        http_client.is_closed
        or (current_loop is not None and stored_loop is not current_loop)
    ):
        http_client = None
        _http_client_loop_ref = None

    if http_client is None:
        from .config import get_config
        config = get_config()
        http_client = httpx.AsyncClient(
            headers=_canvas_auth_headers(config.canvas_api_token),
            timeout=config.api_timeout
        )
        _http_client_loop_ref = weakref.ref(current_loop) if current_loop is not None else None
    return http_client


async def cleanup_http_client() -> None:
    """Close the HTTP client and release resources."""
    global http_client
    if http_client is not None:
        await http_client.aclose()
        http_client = None


@asynccontextmanager
async def canvas_authenticated_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield a Canvas-authenticated httpx client for the current context.

    Resolution, fail-closed:
    - Per-request credentials present (HTTP mode) -> a fresh client with the
      caller's token.
    - No per-request credentials but an HTTP request is active -> raise
      PermissionError (never fall back to the server's own token).
    - Otherwise (stdio mode) -> the shared global client (env-based token).
    """
    from .config import get_config

    req_creds = get_request_credentials()
    if req_creds:
        config = get_config()
        async with httpx.AsyncClient(
            headers=_canvas_auth_headers(req_creds.api_token),
            timeout=config.api_timeout,
        ) as client:
            yield client
        return

    if is_http_request_active():
        raise PermissionError("Canvas token required for HTTP request")

    yield _get_http_client()


async def make_canvas_request(
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | list[tuple[str, Any]] | None = None,
    use_form_data: bool = False,
    skip_anonymization: bool = False,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    api_root: Literal["rest", "quiz"] = API_ROOT_REST,
    return_headers: bool = False,
) -> Any:
    """Make a request to the Canvas API with proper error handling.

    Automatically retries with exponential backoff on rate limiting — both 429
    and the throttle-flavored 403 (see _is_rate_limited).

    Args:
        method: HTTP method (get, post, put, delete)
        endpoint: Canvas API endpoint, or a same-origin absolute URL (used by
            the paginated fetcher to follow Link rel="next" bookmarks)
        params: Query parameters
        data: Request body data
        use_form_data: Use form data instead of JSON
        skip_anonymization: Retained for call-site compatibility; no effect
            (the multi-user anonymization layer was removed in this fork)
        files: Dictionary of file objects for multipart form uploads
        api_root: Which Canvas API root to call ("rest" => /api/v<N>, "quiz" => /api/quiz/v1)
        return_headers: When True, return (result, headers) instead of result
            (errors come back as ({"error": ...}, {})). Needed by pagination.
    """

    from .audit import log_data_access
    from .config import get_config

    def _ret(value: Any, headers: Any = None) -> Any:
        if return_headers:
            return value, dict(headers) if headers is not None else {}
        return value

    config = get_config()

    # Check for per-request credentials (HTTP transport mode)
    req_creds = get_request_credentials()

    # A same-origin absolute URL (from a Canvas Link header) bypasses endpoint
    # assembly below. Anything cross-origin is refused: the only legitimate
    # source of absolute URLs is Canvas's own pagination links, and following a
    # foreign host would ship the bearer token off-instance.
    absolute = endpoint.startswith("http://") or endpoint.startswith("https://")
    if absolute:
        expected_origin = (
            (req_creds.api_url if req_creds else config.canvas_api_url)
            .split("/api/")[0]
            .rstrip("/")
        )
        if not endpoint.startswith(expected_origin + "/"):
            log_warning(
                "Blocked Canvas API request to a cross-origin absolute URL",
                endpoint=sanitize_url(endpoint),
            )
            return _ret({"error": "Absolute request URLs must match the Canvas origin"})
    else:
        # Ensure the endpoint starts with a slash
        if not endpoint.startswith('/'):
            endpoint = f"/{endpoint}"

        # Endpoints are built by f-string interpolation of caller-supplied identifiers
        # (".../assignments/{assignment_id}/submissions/self"). A '?' or '#' inside an
        # identifier ends the path early and demotes everything after it to the query
        # or fragment, so a value like "123/submissions/456?" silently retargets a
        # hard-coded self-scoped route at another user's record. Every caller passes
        # query parameters via `params=`, so a delimiter in the path is always
        # smuggling, never a legitimate call.
        bad_delimiter = next((c for c in ("?", "#") if c in endpoint), None)
        if bad_delimiter is not None:
            log_warning(
                "Blocked Canvas API request with a delimiter in the endpoint path",
                endpoint=sanitize_url(endpoint),
            )
            return _ret({"error": f"Invalid endpoint: '{bad_delimiter}' is not allowed in a request path"})
        if any(seg == ".." for seg in endpoint.split("/")):
            log_warning(
                "Blocked Canvas API request with a traversal segment in the endpoint path",
                endpoint=sanitize_url(endpoint),
            )
            return _ret({"error": "Invalid endpoint: '..' is not allowed in a request path"})

    if api_root not in (API_ROOT_REST, API_ROOT_QUIZ):
        return _ret({"error": f"Unsupported api_root: {api_root}"})

    if req_creds:
        # Per-request client with user's credentials (HTTP mode)
        client = httpx.AsyncClient(
            headers=_canvas_auth_headers(req_creds.api_token),
            timeout=config.api_timeout,
        )
        try:
            base_url = _resolve_canvas_api_root(req_creds.api_url.rstrip('/'), api_root)
        except ValueError as exc:
            await client.aclose()
            return _ret({"error": str(exc)})
        url = endpoint if absolute else f"{base_url}{endpoint}"
        _close_client = True
    elif is_http_request_active():
        # HTTP request without a per-request token: fail closed. Never fall
        # back to the server's own credentials (would mis-attribute actions).
        log_warning(
            "Blocked Canvas API request without per-request Canvas token",
            endpoint=sanitize_url(endpoint),
        )
        return _ret({"error": "Canvas token required for HTTP request"})
    else:
        # Global client (stdio mode)
        client = _get_http_client()
        try:
            base_url = _resolve_canvas_api_root(config.canvas_api_url.rstrip('/'), api_root)
        except ValueError as exc:
            return _ret({"error": str(exc)})
        url = endpoint if absolute else f"{base_url}{endpoint}"
        _close_client = False

    # Gate outbound calls with concurrency semaphore (uses MAX_CONCURRENT_REQUESTS)
    semaphore = _get_request_semaphore()
    async with semaphore:
        # Retry loop for rate limiting
        try:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    # Log the request for debugging (if enabled)
                    if config.log_api_requests:
                        retry_info = f" (retry {attempt}/{MAX_RETRIES})" if attempt > 0 else ""
                        log_debug(f"Making {method.upper()} request to {sanitize_url(url)}{retry_info}")

                    if method.lower() == "get":
                        response = await client.get(url, params=params)
                    elif method.lower() == "post":
                        if files:
                            # File uploads always pass dict form fields, never
                            # the list-of-tuples encoding.
                            response = await client.post(
                                url,
                                data=cast("dict[str, Any] | None", data),
                                files=files,
                            )
                        elif use_form_data:
                            # Handle list of tuples separately to work around httpx async bug
                            # with duplicate keys (e.g., module[prerequisite_module_ids][])
                            if isinstance(data, list):
                                encoded = urlencode(data)
                                response = await client.post(
                                    url,
                                    content=encoded,
                                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                                )
                            else:
                                response = await client.post(url, data=data)
                        else:
                            response = await client.post(url, json=data)
                    elif method.lower() == "put":
                        if use_form_data:
                            # Handle list of tuples separately to work around httpx async bug
                            if isinstance(data, list):
                                encoded = urlencode(data)
                                response = await client.put(
                                    url,
                                    content=encoded,
                                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                                )
                            else:
                                response = await client.put(url, data=data)
                        else:
                            response = await client.put(url, json=data)
                    elif method.lower() == "delete":
                        response = await client.delete(url, params=params)
                    else:
                        return _ret({"error": f"Unsupported method: {method}"})

                    response.raise_for_status()
                    result = response.json()

                    # Audit: log successful data access
                    log_data_access(method, endpoint, "success")

                    return _ret(result, getattr(response, "headers", None))

                except httpx.HTTPStatusError as e:
                    # Rate limiting (429, or 403-flavored throttling) backs off
                    # and retries; a real permission 403 falls through.
                    if _is_rate_limited(e.response) and attempt < MAX_RETRIES:
                        # Check for Retry-After header
                        retry_after = e.response.headers.get('Retry-After')
                        if retry_after:
                            try:
                                wait_time = int(retry_after)
                            except ValueError:
                                wait_time = INITIAL_BACKOFF_SECONDS * (2 ** attempt)
                        else:
                            wait_time = INITIAL_BACKOFF_SECONDS * (2 ** attempt)

                        log_warning(
                            f"Rate limited ({e.response.status_code}). Retrying in {wait_time}s...",
                            attempt=attempt + 1, max_retries=MAX_RETRIES,
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    # Not a rate limit error or out of retries - format and return error
                    error_message = f"HTTP error: {e.response.status_code}"
                    try:
                        error_details = e.response.json()
                        error_message += f", Details: {error_details}"
                    except ValueError:
                        error_details = e.response.text
                        error_message += f", Text: {error_details}"

                    log_error(f"API error on {sanitize_url(endpoint)}", status_code=e.response.status_code)

                    # Audit: log HTTP error (status code only — response body may contain PII)
                    log_data_access(method, endpoint, "error", f"HTTP {e.response.status_code}")

                    return _ret({"error": error_message})

                except Exception as e:
                    log_error(f"Request failed for {sanitize_url(endpoint)}", error_type=type(e).__name__)

                    # Audit: log request exception (type only — message may contain PII)
                    log_data_access(method, endpoint, "error", type(e).__name__)

                    return _ret({"error": f"Request failed: {str(e)}"})

            # Should never reach here, but just in case
            return _ret({"error": "Max retries exceeded"})
        finally:
            if _close_client:
                await client.aclose()


async def upload_file_to_storage(
    upload_url: str,
    upload_params: dict[str, Any],
    file_path: str,
    filename: str,
    content_type: str
) -> dict[str, Any]:
    """Upload a file to Canvas storage URL (step 2 of 3-step upload process).

    This function handles the multipart file upload to S3/Instructure storage.
    It's called after requesting an upload URL from the Canvas API.

    Args:
        upload_url: The pre-signed upload URL from Canvas API
        upload_params: Additional parameters required by the storage (from Canvas API)
        file_path: Local filesystem path to the file
        filename: Name to use for the uploaded file
        content_type: MIME type of the file

    Returns:
        Response from the storage service, typically containing file confirmation
        or redirect location

    Note:
        This posts to external storage (S3/Instructure), not the Canvas API.
        The response handling differs from regular Canvas API calls.
    """
    from .config import get_config

    config = get_config()

    # Create a separate client for external uploads (no auth header needed)
    async with httpx.AsyncClient(timeout=config.api_timeout) as client:
        try:
            # Read the file content
            with open(file_path, 'rb') as f:
                file_content = f.read()

            # Build multipart form data
            # upload_params contains required fields like 'key', 'Policy', 'Signature', etc.
            files = {
                'file': (filename, file_content, content_type)
            }

            # Log for debugging
            if config.log_api_requests:
                log_debug(f"Uploading file to storage: {upload_url}", filename=filename, content_type=content_type, size=len(file_content))

            # Make the upload request
            # Note: follow_redirects=False because Canvas may return a 3xx with file info
            response = await client.post(
                upload_url,
                data=upload_params,
                files=files,
                follow_redirects=False
            )

            # Canvas storage upload can return:
            # - 200/201 with JSON body containing file info
            # - 301/302/303 redirect to Canvas API with file info
            if response.status_code in (200, 201):
                # Direct success response
                try:
                    body: dict[str, Any] = response.json()
                    return body
                except ValueError:
                    # Some storage backends return empty success
                    return {"success": True, "status_code": response.status_code}

            elif response.status_code in (301, 302, 303):
                # Redirect - follow it to get file info from Canvas
                redirect_url = response.headers.get('Location')
                if redirect_url:
                    # Follow the redirect to get file info. This goes back to the
                    # Canvas API and needs auth; route through the shared
                    # fail-closed resolver so HTTP mode never uses the server's
                    # own token.
                    try:
                        async with canvas_authenticated_client() as canvas_client:
                            confirm_response = await canvas_client.get(redirect_url)
                            confirm_response.raise_for_status()
                            confirmed: dict[str, Any] = confirm_response.json()
                            return confirmed
                    except PermissionError as e:
                        return {"error": str(e)}
                else:
                    return {"error": "Redirect without Location header"}

            else:
                # Unexpected status
                error_text = response.text
                return {
                    "error": f"Storage upload failed with status {response.status_code}",
                    "details": error_text
                }

        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}
        except PermissionError:
            return {"error": f"Permission denied reading file: {file_path}"}
        except httpx.TimeoutException:
            return {"error": "Upload timed out"}
        except Exception as e:
            return {"error": f"Upload failed: {str(e)}"}


async def fetch_all_paginated_results(
    endpoint: str,
    params: dict[str, Any] | None = None,
    skip_anonymization: bool = False,
    api_root: Literal["rest", "quiz"] = API_ROOT_REST,
) -> Any:
    """Fetch all results from a paginated Canvas API endpoint.

    Follows the Link header rel="next" (verified API fact: always pass
    per_page=100 and follow Link; header name matched case-insensitively).
    Some endpoints — notably /planner/items — use opaque bookmark cursors, so
    numeric page parameters are not a substitute.

    Args:
        endpoint: Canvas API endpoint.
        params: Query parameters.
        skip_anonymization: Retained for call-site compatibility; no effect.
        api_root: Which Canvas API root to call ("rest" => /api/v<N>,
            "quiz" => /api/quiz/v1).
    """
    if params is None:
        params = {}

    # Ensure we get a reasonable number per page
    if "per_page" not in params:
        params["per_page"] = 100

    all_results: list[Any] = []
    url: str = endpoint
    current_params: dict[str, Any] | None = params
    # Runaway backstop, far above any real collection at per_page=100.
    max_pages = 500

    for page_num in range(1, max_pages + 1):
        response, headers = await make_canvas_request(
            "get", url, params=current_params, skip_anonymization=True,
            api_root=api_root, return_headers=True,
        )

        if isinstance(response, dict) and "error" in response:
            log_error(f"Error fetching page {page_num}", error=response['error'])
            return response

        if not isinstance(response, list):
            # Non-list payload on a paginated fetch: surface it unchanged.
            return response

        all_results.extend(response)

        next_url = _link_header_next(headers)
        if not next_url:
            break
        # The next link is absolute and already carries every query parameter
        # (including bookmark cursors); passing params again would corrupt it.
        url = next_url
        current_params = None
    else:
        log_warning(
            f"Pagination stopped at the {max_pages}-page backstop; results may be incomplete",
            endpoint=sanitize_url(endpoint),
        )

    return all_results
