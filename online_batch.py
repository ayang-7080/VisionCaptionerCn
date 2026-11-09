"""Bounded concurrent online requests; results retain their source paths."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
from pathlib import Path
import random
import tempfile
import time

from api_backend import ApiRequestError


def wait_before_retry(seconds, stop_event):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event and stop_event():
            return False
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    return not (stop_event and stop_event())


def iter_online_results(engine, pairs, concurrency=1, prompt_text="Describe this.", trigger_word="",
                        frame_count=8, max_tokens=1024, use_masks=True, log_callback=None, stop_event=None):
    """Keep at most N requests submitted; on stop, drain active results only.

    The caller saves results on its own thread, as soon as each request finishes.
    Active HTTP requests cannot be cancelled; their successful responses are kept.
    """
    if not 1 <= concurrency <= 32:
        raise ValueError("API concurrency must be between 1 and 32.")

    def stopped():
        return bool(stop_event and stop_event())

    def generate(pair):
        path, mask = pair
        if stopped():
            return None
        for attempt in range(3):
            try:
                captions = engine.generate_batch(
                    [path], prompt_text=prompt_text, trigger_word=trigger_word,
                    frame_count=frame_count, mask_paths=[mask if use_masks else None],
                    max_tokens=max_tokens, log_callback=log_callback, raise_errors=True)
                return captions[0] if captions else "Error: API returned no caption."
            except ApiRequestError as exc:
                retryable = (exc.status in (429, 502, 503, 504)
                             and exc.code not in ("insufficient_quota", "billing_hard_limit_reached"))
                delay = exc.retry_after if exc.retry_after is not None else 2 ** (attempt + 1) + random.random()
                if retryable and attempt < 2 and delay <= 60 and not stopped():
                    if log_callback:
                        log_callback(f"API HTTP {exc.status}: retry {attempt + 1}/2 in {delay:.1f}s ({os.path.basename(path)})")
                    if wait_before_retry(delay, stop_event):
                        continue
                return "Error: " + str(exc)
            except Exception as exc:
                client = getattr(engine, "api_client", None)
                detail = client._safe_error_detail({"message": str(exc)}) if client else str(exc)
                return "Error: " + (detail or "Online caption failed.")

    iterator = iter(pairs)
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="caption-api") as pool:
        pending = {}

        def fill():
            while len(pending) < concurrency and not stopped():
                try:
                    pair = next(iterator)
                except StopIteration:
                    break
                pending[pool.submit(generate, pair)] = pair[0]

        fill()
        while pending:
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                path = pending.pop(future)
                caption = future.result()
                if caption is not None:
                    yield path, caption
            fill()


def select_online_pairs(pairs, skip_existing=False, stop_event=None):
    selected, skipped, reserved = [], 0, set()
    for path, mask in pairs:
        if stop_event and stop_event():
            break
        target = os.path.normcase(os.path.abspath(os.path.splitext(path)[0] + '.txt'))
        if skip_existing and (os.path.exists(target) or target in reserved):
            skipped += 1
            continue
        reserved.add(target)
        selected.append((path, mask))
    return selected, skipped


def save_online_caption(path, caption, skip_existing=False):
    """Replace a sidecar only after a complete UTF-8 write succeeds."""
    destination = Path(path).with_suffix(".txt")
    if skip_existing and destination.exists():
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=destination.name + ".", suffix=".tmp", delete=False) as output:
            temporary = output.name
            output.write(caption)
        if skip_existing and destination.exists():
            return False
        os.replace(temporary, destination)
        return True
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
