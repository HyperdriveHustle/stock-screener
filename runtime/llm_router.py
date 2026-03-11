"""LLM routing — load channels from config, dispatch by task with round-robin + failover."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import requests

from runtime.utils import first_json_object as _first_json_object

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "llm_config.json"


@dataclass(frozen=True)
class LLMChannel:
    name: str
    endpoint: str
    api_key: str
    model: str
    timeout: int


def _resolve_api_key(channel_cfg: dict) -> str:
    """Resolve api_key: prefer api_key_env (from env var), fallback to literal api_key."""
    env_var = channel_cfg.get("api_key_env", "")
    if env_var:
        value = os.getenv(env_var, "")
        if value:
            return value
    return channel_cfg.get("api_key", "")


def load_config(config_path: str | Path | None = None) -> dict:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    if not path.exists():
        logger.warning("LLM config not found: %s, using empty config", path)
        return {"channels": {}, "tasks": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class LLMRouter:
    """Dispatch LLM calls by task name with round-robin and failover."""

    def __init__(self, config_path: str | Path | None = None):
        cfg = load_config(config_path)
        self._channels: dict[str, LLMChannel] = {}
        for name, ch_cfg in cfg.get("channels", {}).items():
            api_key = _resolve_api_key(ch_cfg)
            if not api_key:
                logger.warning("Channel %s has no api_key, skipping", name)
                continue
            self._channels[name] = LLMChannel(
                name=name,
                endpoint=ch_cfg["endpoint"],
                api_key=api_key,
                model=ch_cfg["model"],
                timeout=int(ch_cfg.get("timeout", 60)),
            )

        self._tasks: dict[str, dict] = cfg.get("tasks", {})
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def get_channels(self, task: str) -> list[LLMChannel]:
        """Return ordered channel list for a task."""
        task_cfg = self._tasks.get(task, {})
        channel_names = task_cfg.get("channels", [])
        return [self._channels[n] for n in channel_names if n in self._channels]

    def next_channel(self, task: str) -> LLMChannel | None:
        """Pick next channel for a task using round-robin."""
        channels = self.get_channels(task)
        if not channels:
            return None
        with self._lock:
            idx = self._counters.get(task, 0)
            self._counters[task] = idx + 1
        return channels[idx % len(channels)]

    def call_json(
        self,
        task: str,
        *,
        system_prompt: str,
        user_payload: dict,
    ) -> tuple[dict, dict, LLMChannel | None]:
        """Call LLM with failover across channels. Returns (request, response, channel_used)."""
        channels = self.get_channels(task)
        if not channels:
            logger.warning("No channels configured for task: %s", task)
            return {}, {}, None

        # Start from round-robin position, try all channels
        with self._lock:
            start_idx = self._counters.get(task, 0)
            self._counters[task] = start_idx + 1

        for i in range(len(channels)):
            channel = channels[(start_idx + i) % len(channels)]
            request_payload = {
                "model": channel.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            }
            try:
                resp = requests.post(
                    channel.endpoint,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {channel.api_key}",
                    },
                    json=request_payload,
                    timeout=channel.timeout,
                )
                resp.raise_for_status()
                body = resp.json()
                content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                parsed = _first_json_object(content)
                if isinstance(parsed, dict):
                    return request_payload, parsed, channel
                logger.warning("Channel %s returned non-JSON for task %s", channel.name, task)
            except Exception as e:
                logger.warning("Channel %s failed for task %s: %s", channel.name, task, e)
                continue

        return {}, {}, None

    def call_raw(self, channel: LLMChannel, *, system_prompt: str, user_payload: dict) -> dict | None:
        """Call a specific channel directly. Returns parsed JSON or None."""
        request_payload = {
            "model": channel.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        try:
            resp = requests.post(
                channel.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {channel.api_key}",
                },
                json=request_payload,
                timeout=channel.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            message = ((body.get("choices") or [{}])[0].get("message") or {})
            content = message.get("content", "")
            parsed = _first_json_object(content)
            if isinstance(parsed, dict):
                parsed["_think_content"] = (
                    message.get("reasoning_content")
                    or message.get("think_content")
                    or ""
                )
                return parsed
        except Exception as e:
            logger.debug("call_raw failed on %s: %s", channel.name, e)
        return None


# Module-level singleton (lazy)
_router: LLMRouter | None = None


def get_router(config_path: str | Path | None = None) -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter(config_path)
    return _router
