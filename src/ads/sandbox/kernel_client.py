"""One-shot transport client for a persistent kernel running in the container."""

from __future__ import annotations

import json
import sys
import time
from queue import Empty
from typing import Any

from jupyter_client import BlockingKernelClient

_DATAFRAME_MIME = "application/vnd.ads.dataframe+json"
_BOOTSTRAP = r"""
if not globals().get("_ads_dataframe_formatter_installed", False):
    import json as _ads_json
    import pandas as _ads_pd
    from IPython import get_ipython as _ads_get_ipython

    def _ads_format_dataframe(frame):
        # BaseFormatter transports one MIME value, so encode the structured
        # orient=split object as JSON and decode it at the host boundary.
        return frame.to_json(orient="split", date_format="iso")

    from IPython.core.formatters import BaseFormatter as _ads_BaseFormatter

    _ads_display_formatter = _ads_get_ipython().display_formatter
    _ads_dataframe_formatter = _ads_BaseFormatter(
        parent=_ads_display_formatter, enabled=True
    )
    _ads_dataframe_formatter.for_type(_ads_pd.DataFrame, _ads_format_dataframe)
    _ads_display_formatter.formatters[
        "application/vnd.ads.dataframe+json"
    ] = _ads_dataframe_formatter
    _ads_dataframe_formatter_installed = True
"""


def _drain(client: BlockingKernelClient, message_id: str, timeout: float) -> list[dict]:
    messages: list[dict] = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Empty
        message = client.get_iopub_msg(timeout=remaining)
        if message.get("parent_header", {}).get("msg_id") != message_id:
            continue
        messages.append(message)
        if (
            message.get("msg_type") == "status"
            and message.get("content", {}).get("execution_state") == "idle"
        ):
            return messages


def _execute(client: BlockingKernelClient, code: str, timeout: float, *, silent: bool):
    message_id = client.execute(code, silent=silent, store_history=not silent)
    return _drain(client, message_id, timeout)


def _outputs(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int | None]:
    outputs: list[dict[str, Any]] = []
    execution_count: int | None = None
    for message in messages:
        kind = message.get("msg_type")
        content = message.get("content", {})
        if kind == "stream":
            outputs.append(
                {"kind": "text", "stream": content.get("name"), "text": content.get("text", "")}
            )
        elif kind == "error":
            outputs.append(
                {
                    "kind": "error",
                    "name": content.get("ename", "Error"),
                    "value": content.get("evalue", ""),
                    "traceback": content.get("traceback", []),
                }
            )
        elif kind in {"display_data", "execute_result"}:
            data = content.get("data", {})
            execution_count = content.get("execution_count", execution_count)
            if _DATAFRAME_MIME in data:
                frame = json.loads(data[_DATAFRAME_MIME])
                outputs.append({"kind": "dataframe", **frame})
            if "image/png" in data:
                outputs.append({"kind": "figure", "png_base64": data["image/png"]})
            if _DATAFRAME_MIME not in data and "image/png" not in data and "text/plain" in data:
                outputs.append({"kind": "text", "stream": "display", "text": data["text/plain"]})
    return outputs, execution_count


def main() -> int:
    connection_file, timeout_text = sys.argv[1:3]
    timeout = float(timeout_text)
    request = json.load(sys.stdin)
    client = BlockingKernelClient(connection_file=connection_file)
    client.load_connection_file()
    client.start_channels()
    try:
        client.wait_for_ready(timeout=timeout)
        _execute(client, _BOOTSTRAP, timeout, silent=True)
        try:
            messages = _execute(client, str(request["code"]), timeout, silent=False)
        except Empty:
            client.session.send(client.control_channel.socket, "interrupt_request")
            print(json.dumps({"outputs": [], "execution_count": None, "timed_out": True}))
            return 0
        outputs, execution_count = _outputs(messages)
        print(
            json.dumps(
                {
                    "outputs": outputs,
                    "execution_count": execution_count,
                    "timed_out": False,
                }
            )
        )
        return 0
    finally:
        client.stop_channels()


if __name__ == "__main__":
    raise SystemExit(main())
