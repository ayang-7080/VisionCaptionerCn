"""OpenAI-compatible visual captions without loading a local model."""
import base64
import io
import json
import os
import re
import socket
import ssl
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from PIL import Image, ImageOps
from file_utils import VIDEO_EXTS

DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_API_MODEL = "gpt-6.1-sol"
DEFAULT_API_TEST_PROMPT = "你好，你是什么模型"
DEFAULT_API_CONCURRENCY = 8


class ApiRequestError(ValueError):
    def __init__(self, message, status, code=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after


class _NoRedirect(HTTPRedirectHandler):
    # A redirect must not forward the API key or images to a different endpoint.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiCaptionClient:
    def __init__(self, base_url, api_key, model, max_resolution=768, timeout=120):
        url = urlsplit(base_url.strip())
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise ValueError("Enter an HTTP(S) API base URL without credentials, query or fragment.")
        # Validate malformed ports before any request is made.
        url.port
        path = url.path.rstrip("/") or "/v1"
        if not path.endswith("/chat/completions"):
            path += "/chat/completions"
        self.endpoint = urlunsplit((url.scheme, url.netloc, path, "", ""))
        self._token_limit_parameter = (
            "max_completion_tokens" if url.hostname.lower() == "api.openai.com" else "max_tokens")
        self.model = model.strip()
        if not self.model:
            raise ValueError("API model name is required.")
        self.api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
        if any(char in self.api_key for char in "\r\n"):
            raise ValueError("API Key must not contain line breaks.")
        self.max_resolution = int(max_resolution)
        self.timeout = float(timeout)
        if self.max_resolution < 1 or not 1 <= self.timeout <= 600:
            raise ValueError("Resolution must be positive; API timeout must be 1–600 seconds.")

    def test_chat(self, prompt=DEFAULT_API_TEST_PROMPT, max_tokens=256):
        """Send a text-only test without loading media or changing model state."""
        if not prompt.strip():
            raise ValueError("Test prompt must not be empty.")
        return self._request_caption(prompt.strip(), max_tokens)

    def _image_content(self, image):
        image = image.convert("RGB")
        image.thumbnail((self.max_resolution, self.max_resolution), Image.Resampling.LANCZOS)
        with io.BytesIO() as buffer:
            image.save(buffer, format="JPEG", quality=90)
            data = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}

    def _read_api_error(self, error):
        """Read only bounded, structured diagnostics; never log the raw body."""
        try:
            data = json.loads(error.read(16384))
            detail = data.get("error", {}) if isinstance(data, dict) else {}
            if isinstance(detail, str):
                return {"message": detail}
            return detail if isinstance(detail, dict) else {}
        except (ValueError, OSError):
            return {}
        finally:
            error.close()

    def _safe_error_detail(self, detail):
        parts = []
        for field in ("message", "code", "param"):
            value = detail.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(value if field == "message" else f"{field}={value}")
        text = " | ".join(parts)
        # Redact before truncation so long echoed credentials cannot leak prefixes.
        if self.api_key:
            text = text.replace(self.api_key, "[redacted]")
        text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", text)
        text = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[redacted]@", text)
        return " ".join(text.split())[:800]

    def _transport_error_message(self, error):
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, ssl.SSLCertVerificationError):
            category = "TLS certificate verification failed"
        elif isinstance(reason, socket.gaierror):
            category = "DNS lookup failed"
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            category = "TLS handshake timed out" if "handshake" in str(reason).lower() else "API request timed out"
        elif isinstance(reason, ConnectionRefusedError):
            category = "API/proxy connection refused"
        elif isinstance(reason, ssl.SSLError):
            category = "TLS connection failed"
        elif isinstance(reason, (ConnectionResetError, RemoteDisconnected)):
            category = "API connection closed or reset"
        else:
            category = "API connection failed"
        detail = self._safe_error_detail({"message": str(reason)})
        return f"{category} ({type(reason).__name__}): {detail or 'No additional details.'}"

    def _request_caption(self, content, max_tokens):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        token_parameter = self._token_limit_parameter
        for attempt in range(2):
            payload = {"model": self.model, "messages": [{"role": "user", "content": content}],
                       token_parameter: max_tokens, "stream": False}
            request = Request(self.endpoint, json.dumps(payload).encode("utf-8"), headers, method="POST")
            try:
                with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                    data = json.load(response)
                self._token_limit_parameter = token_parameter
                break
            except HTTPError as exc:
                status = exc.code
                try:
                    retry_after = max(0.0, float(exc.headers.get("Retry-After", "")))
                except (ValueError, TypeError):
                    retry_after = None
                detail = self._read_api_error(exc)
                message = detail.get("message", "")
                message = message if isinstance(message, str) else ""
                unsupported = (detail.get("code") in ("unsupported_parameter", "unknown_parameter", "unrecognized_parameter")
                               or re.search(r"(?:unsupported|unknown|unrecognized)\s+(?:parameter|argument)", message, re.I))
                rejected_parameter = detail.get("param")
                rejects_token_limit = (rejected_parameter == token_parameter if rejected_parameter else
                                       re.search(rf"\b{token_parameter}\b", message))
                # Retry only an explicit parameter rejection, once, with the same
                # token budget. Do not replay timeouts, auth, quota or other errors.
                if attempt == 0 and status == 400 and unsupported and rejects_token_limit:
                    token_parameter = "max_completion_tokens" if token_parameter == "max_tokens" else "max_tokens"
                    continue
                hints = {401: "Check API Key.", 403: "Access denied; check permissions.",
                         404: "Check API address and model name.", 429: "Rate limit or quota exceeded; retry later.",
                         400: "Check model name and request parameter compatibility.",
                         413: "Image payload too large; reduce resolution or video frames."}
                diagnostic = self._safe_error_detail(detail)
                hint = hints.get(status, "Service request failed; check endpoint or retry later.")
                raise ApiRequestError(f"API HTTP {status}. {diagnostic or hint}", status,
                                      detail.get("code"), retry_after) from None
            except (URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionError, RemoteDisconnected) as exc:
                raise ValueError(self._transport_error_message(exc)) from None
            except (ValueError, UnicodeError):
                raise ValueError("API returned invalid JSON; check the API address.") from None

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            if isinstance(text, list):
                text = "\n".join(part["text"] for part in text if part.get("type") == "text")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ValueError("API returned no caption text in choices[0].message.content.") from None
        if choice.get("finish_reason") == "length":
            raise ValueError("Token limit reached; the answer is incomplete and reasoning tokens also use this budget. Increase Max Tokens and test again.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("API returned no caption text in choices[0].message.content.")
        return text.strip()

    def generate_batch(self, media_engine, file_paths, prompt_text="Describe this.", trigger_word="",
                       frame_count=8, mask_paths=None, max_tokens=1024, log_callback=None, stop_event=None,
                       raise_errors=False):
        results = []
        for index, path in enumerate(file_paths):
            if stop_event and stop_event():
                return []
            try:
                prompt = prompt_text
                mask = mask_paths[index] if mask_paths and index < len(mask_paths) else None
                if os.path.splitext(path)[1].lower() in {ext.lstrip("*") for ext in VIDEO_EXTS}:
                    images = media_engine.extract_video_frames(path, num_frames=frame_count,
                                                               log_callback=log_callback, stop_event=stop_event)
                elif mask and os.path.exists(mask):
                    images = [media_engine.apply_mask(path, mask)]
                    prompt += " Describe the foreground subject ONLY. Do not mention the background or transparency."
                else:
                    with Image.open(path) as source:
                        images = [ImageOps.exif_transpose(source).convert("RGB")]
                if stop_event and stop_event():
                    return []
                if not images or any(image is None for image in images):
                    raise ValueError("Could not load image, mask or video frames.")
                content = [{"type": "text", "text": prompt}]
                content.extend(self._image_content(image) for image in images)
                del images
                if stop_event and stop_event():
                    return []
                text = self._request_caption(content, max_tokens)
                if stop_event and stop_event():
                    return []
                if trigger_word.strip():
                    text = f"{trigger_word.strip()}, {text}"
                results.append(text)
            except Exception as exc:
                if raise_errors:
                    raise
                # Never include a server response/request in errors (it may echo credentials).
                message = str(exc) if isinstance(exc, (ValueError, OSError)) else "API caption generation failed."
                if self.api_key:
                    message = message.replace(self.api_key, "[redacted]")
                results.append(f"Error: {message}")
        return results
