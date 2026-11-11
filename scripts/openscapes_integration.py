#!/usr/bin/env python3
"""Run earthaccess integration tests in an OpenScapes JupyterHub server."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests
from websocket import WebSocketTimeoutException, create_connection

EXIT_CODE_MARKER = "__EARTHACCESS_EXIT_CODE__="
DEFAULT_HUB_RUNTIME_OPTIONS = (
    '{"image--unlisted-choice": "{image}", "resource_allocation": "mem_7_gb"}'
)
KERNEL_ENV_VARS = (
    "EARTHDATA_USERNAME",
    "EARTHDATA_PASSWORD",
    "EARTHDATA_TOKEN",
    "NETRC",
    "EARTHACCESS_TEST_S3",
)


class HubAPIError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} failed ({status}): {body[:1000]}")


def _api_url(hub_url: str, path: str) -> str:
    return f"{hub_url.rstrip('/')}/hub/api/{path.lstrip('/')}"


def _server_api_url(hub_url: str, username: str, server_name: str) -> str:
    suffix = f"users/{quote(username, safe='')}"
    if server_name:
        suffix = f"{suffix}/servers/{quote(server_name, safe='')}"
    else:
        suffix = f"{suffix}/server"
    return _api_url(hub_url, suffix)


def _session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"token {token}",
            "Accept": "application/json",
        }
    )
    return session


def _request(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    timeout = kwargs.pop("timeout", 60)
    response = session.request(method, url, timeout=timeout, **kwargs)
    if response.status_code >= 400:
        raise HubAPIError(method, url, response.status_code, response.text)
    return response


def _current_user(session: requests.Session, hub_url: str) -> str:
    response = _request(session, "GET", _api_url(hub_url, "user"))
    try:
        data = response.json()
    except ValueError as exc:
        msg = (
            f"Expected JSON from {response.url}, got status {response.status_code} "
            f"and content type {response.headers.get('content-type')!r}: "
            f"{response.text[:300]!r}"
        )
        raise RuntimeError(msg) from exc
    return str(data["name"])


def _load_hub_runtime_options(raw_options: str, image: str) -> dict[str, Any] | None:
    if not raw_options:
        return None
    if "{image}" not in raw_options:
        msg = "HUB_RUNTIME_OPTIONS must contain the {image} placeholder"
        raise ValueError(msg)

    options = json.loads(raw_options)
    if not isinstance(options, dict):
        msg = "HUB_RUNTIME_OPTIONS must be a JSON object"
        raise TypeError(msg)
    if "image" in options and "image--unlisted-choice" not in options:
        options["image--unlisted-choice"] = options.pop("image")

    rendered = json.dumps(options).replace("{image}", image)
    parsed = json.loads(rendered)
    if not isinstance(parsed, dict):
        msg = "HUB_RUNTIME_OPTIONS must be a JSON object"
        raise TypeError(msg)
    return parsed


def _spawn_server(
    session: requests.Session,
    hub_url: str,
    username: str,
    server_name: str,
    runtime_options: dict[str, Any] | None,
) -> str:
    payload = runtime_options or {}

    if not server_name:
        url = _server_api_url(hub_url, username, "")
        _request(session, "POST", url, json=payload)
        return ""

    url = _server_api_url(hub_url, username, server_name)
    try:
        _request(session, "POST", url, json=payload)
    except HubAPIError as exc:
        if exc.status == 404 or (
            exc.status == 400 and "named server" in exc.body.lower()
        ):
            print(
                "Named servers are unavailable; falling back to the default server.",
                file=sys.stderr,
            )
            default_url = _server_api_url(hub_url, username, "")
            _request(session, "POST", default_url, json=payload)
            return ""
        raise
    return server_name


def _wait_for_server(
    session: requests.Session,
    hub_url: str,
    username: str,  # noqa: ARG001
    server_name: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    url = _api_url(hub_url, "user")
    deadline = time.monotonic() + timeout_seconds
    last_state = "unknown"

    while time.monotonic() < deadline:
        response = _request(session, "GET", url)
        data = response.json()
        servers = data.get("servers", {})
        model = servers.get(server_name) if server_name else servers.get("")

        if model is None:
            last_state = "not found"
        else:
            last_state = (
                "ready"
                if model.get("ready")
                else "failed"
                if model.get("failed")
                else "pending"
            )
            if model.get("ready"):
                return model
            if model.get("failed"):
                msg = f"JupyterHub server failed to start: {model}"
                raise RuntimeError(msg)
        time.sleep(5)

    msg = (
        f"Timed out after {timeout_seconds}s waiting for the OpenScapes server "
        f"to become ready (last state: {last_state})."
    )
    raise TimeoutError(msg)


def _stop_server(
    session: requests.Session,
    hub_url: str,
    username: str,
    server_name: str,
) -> None:
    url = _server_api_url(hub_url, username, server_name)
    kwargs = {"json": {"remove": True}} if server_name else {}
    try:
        response = session.delete(url, timeout=60, **kwargs)
    except requests.RequestException as exc:
        print(f"Warning: failed to stop JupyterHub server: {exc}", file=sys.stderr)
        return

    if response.status_code >= 400 and response.status_code != 404:
        print(
            "Warning: failed to stop JupyterHub server "
            f"({response.status_code}): {response.text[:1000]}",
            file=sys.stderr,
        )


def _kernel_env() -> dict[str, str]:
    return {
        key: value
        for key in KERNEL_ENV_VARS
        if (value := os.environ.get(key)) is not None
    }


def _create_kernel(
    session: requests.Session,
    server_url: str,
    timeout_seconds: int,
) -> str:
    url = f"{server_url.rstrip('/')}/api/kernels"
    response = _request(
        session,
        "POST",
        url,
        json={"name": "python3"},
        timeout=timeout_seconds,
    )
    return str(response.json()["id"])


def _delete_kernel(
    session: requests.Session,
    server_url: str,
    kernel_id: str,
) -> None:
    url = f"{server_url.rstrip('/')}/api/kernels/{quote(kernel_id, safe='')}"
    try:
        session.delete(url, timeout=60)
    except requests.RequestException as exc:
        print(f"Warning: failed to delete kernel: {exc}", file=sys.stderr)


def _websocket_url(server_url: str, kernel_id: str) -> str:
    parts = urlsplit(server_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = f"{parts.path.rstrip('/')}/api/kernels/{quote(kernel_id, safe='')}/channels"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def _build_execution_code(
    repo_dir: str,
    expected_commit: str | None,
    test_command: str,
    env: dict[str, str],
) -> str:
    command = shlex.split(test_command)
    encoded_env = base64.b64encode(json.dumps(env).encode()).decode()
    return (
        "import base64\n"
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        f'os.environ.update(json.loads(base64.b64decode("{encoded_env}")))\n'
        f"repo_dir = {repo_dir!r}\n"
        f"expected_commit = {expected_commit!r}\n"
        "actual_commit = subprocess.check_output(\n"
        '    ["git", "-C", repo_dir, "rev-parse", "HEAD"], text=True\n'
        ").strip()\n"
        'print(f"EARTHACCESS_REPO={repo_dir}")\n'
        'print(f"EARTHACCESS_COMMIT={actual_commit}")\n'
        "if expected_commit and actual_commit != expected_commit:\n"
        "    raise SystemExit(\n"
        '        f"Commit mismatch: expected {expected_commit}, got {actual_commit}"\n'
        "    )\n"
        "import earthaccess\n"
        'print(f"EARTHACCESS_VERSION={earthaccess.__version__}")\n'
        'print(f"EARTHACCESS_FILE={earthaccess.__file__}")\n'
        f"command = {command!r}\n"
        "if command:\n"
        "    proc = subprocess.run(\n"
        "        command, cwd=repo_dir, text=True, capture_output=True, check=False\n"
        "    )\n"
        '    print(proc.stdout, end="")\n'
        '    print(proc.stderr, end="", file=sys.stderr)\n'
        f'    print(f"\\n{EXIT_CODE_MARKER}{{proc.returncode}}")\n'
        "else:\n"
        f'    print(f"\\n{EXIT_CODE_MARKER}0")\n'
    )


def _execute_code(
    session: requests.Session,
    server_url: str,
    server_token: str,
    code: str,
    timeout_seconds: int,
) -> str:
    kernel_id = _create_kernel(session, server_url, timeout_seconds)
    websocket = None
    try:
        websocket = create_connection(
            _websocket_url(server_url, kernel_id),
            header=[f"Authorization: token {server_token}"],
            timeout=30,
        )
        message_id = uuid.uuid4().hex
        session_id = uuid.uuid4().hex
        request = {
            "header": {
                "msg_id": message_id,
                "username": "earthaccess-ci",
                "session": session_id,
                "msg_type": "execute_request",
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": False,
                "user_expressions": {},
                "allow_stdin": False,
                "stop_on_error": True,
            },
            "buffers": [],
            "channel": "shell",
        }
        websocket.send(json.dumps(request))
        output: list[str] = []
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            try:
                payload = json.loads(websocket.recv())
            except WebSocketTimeoutException:
                continue

            if payload.get("parent_header", {}).get("msg_id") != message_id:
                continue

            message_type = payload.get("header", {}).get("msg_type")
            content = payload.get("content", {})
            if message_type == "stream":
                output.append(str(content.get("text", "")))
            elif message_type in {"execute_result", "display_data"}:
                text = content.get("data", {}).get("text/plain")
                if text:
                    output.append(str(text))
            elif message_type == "error":
                output.extend(str(line) for line in content.get("traceback", []))
            elif message_type == "status" and content.get("execution_state") == "idle":
                return "".join(output)

        msg = f"Kernel execution timed out after {timeout_seconds}s"
        raise TimeoutError(msg)
    finally:
        if websocket is not None:
            websocket.close()
        _delete_kernel(session, server_url, kernel_id)


def _parse_count(output: str, label: str) -> int:
    matches = re.findall(rf"(\d+) {label}\b", output)
    return int(matches[-1]) if matches else 0


def _parse_commit(output: str) -> str | None:
    match = re.search(r"^EARTHACCESS_COMMIT=(.+)$", output, re.MULTILINE)
    return match.group(1).strip() if match else None


def _parse_exit_code(output: str) -> int | None:
    matches = re.findall(rf"{re.escape(EXIT_CODE_MARKER)}(-?\d+)", output)
    return int(matches[-1]) if matches else None


def _write_summary(
    result: dict[str, Any],
    output: str,
    summary_file: Path,
) -> None:
    log_tail = output[-12000:]
    lines = [
        "## OpenScapes Hub integration test",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Status | `{result['status']}` |",
        f"| PR | `{result['pr']}` |",
        f"| Commit | `{result['commit']}` |",
        f"| Image | `{result['image']}` |",
        f"| Hub | {result['hub']} |",
        f"| Server | `{result['server']}` |",
        f"| Resources | {result['resources']} |",
        f"| Duration | {result['duration_seconds']:.1f}s |",
        f"| Tests | {result['tests_passed']} passed, {result['tests_failed']} failed |",
        "",
    ]
    if result["error"]:
        lines.extend(["### Error", "", f"```text\n{result['error']}\n```", ""])
    if log_tail:
        lines.extend(
            [
                "<details>",
                "<summary>Execution log</summary>",
                "",
                f"```text\n{log_tail}\n```",
                "",
                "</details>",
                "",
            ]
        )
    summary_file.write_text("\n".join(lines))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hub-url",
        default=os.environ.get("JUPYTERHUB_URL", "https://openscapes.2i2c.cloud"),
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("JUPYTERHUB_TOKEN")
        or os.environ.get("OPENSCAPES_JUPYTERHUB_TOKEN"),
    )
    parser.add_argument(
        "--server-token",
        default=os.environ.get("OPENSCAPES_JUPYTERHUB_SERVER_TOKEN"),
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("OPENSCAPES_USERNAME"),
    )
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--server-name",
        default=os.environ.get("OPENSCAPES_SERVER_NAME", ""),
    )
    parser.add_argument(
        "--hub-runtime-options",
        default=os.environ.get("HUB_RUNTIME_OPTIONS") or DEFAULT_HUB_RUNTIME_OPTIONS,
    )
    parser.add_argument(
        "--test-command",
        default=os.environ.get(
            "OPENSCAPES_TEST_COMMAND",
            "python -m pytest tests/integration -v -rxXs",
        ),
    )
    parser.add_argument(
        "--repo-dir",
        default=os.environ.get("EARTHACCESS_REPO_DIR", "/opt/earthaccess"),
    )
    parser.add_argument(
        "--expected-commit",
        default=os.environ.get("EARTHACCESS_COMMIT"),
    )
    parser.add_argument("--pr", default=os.environ.get("GITHUB_PR_NUMBER", ""))
    parser.add_argument(
        "--resource-label",
        default=os.environ.get("OPENSCAPES_RESOURCE_LABEL", "~7 GB RAM, ~0.9 CPUs"),
    )
    parser.add_argument(
        "--startup-timeout",
        type=int,
        default=int(os.environ.get("OPENSCAPES_STARTUP_TIMEOUT", "900")),
    )
    parser.add_argument(
        "--execution-timeout",
        type=int,
        default=int(os.environ.get("OPENSCAPES_EXECUTION_TIMEOUT", "3600")),
    )
    parser.add_argument("--result-file", default="hub-results.json")
    parser.add_argument("--summary-file", default="hub-summary.md")
    parser.add_argument("--log-file", default="hub-test.log")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.token:
        msg = "JUPYTERHUB_TOKEN or OPENSCAPES_JUPYTERHUB_TOKEN is required"
        raise SystemExit(msg)
    session = _session(args.token)
    started = time.monotonic()
    server_name = args.server_name
    username = ""
    server_url = ""
    output = ""
    error = ""
    status = "error"
    tests_passed = 0
    tests_failed = 0
    result_path = Path(args.result_file)
    summary_path = Path(args.summary_file)
    log_path = Path(args.log_file)

    try:
        username = args.username or _current_user(session, args.hub_url)
        hub_runtime_options = _load_hub_runtime_options(
            args.hub_runtime_options, args.image
        )
        print(f"Hub runtime options: {json.dumps(hub_runtime_options, sort_keys=True)}")
        server_name = _spawn_server(
            session,
            args.hub_url,
            username,
            server_name,
            hub_runtime_options,
        )
        model = _wait_for_server(
            session,
            args.hub_url,
            username,
            server_name,
            args.startup_timeout,
        )
        print(
            "Hub server model: "
            + json.dumps(
                {
                    key: model.get(key)
                    for key in ("image", "user_options", "ready", "url")
                },
                default=str,
                sort_keys=True,
            )
        )
        server_url = urljoin(
            args.hub_url.rstrip("/") + "/",
            str(model["url"]).lstrip("/"),
        )
        server_token = args.server_token or args.token
        code = _build_execution_code(
            args.repo_dir,
            args.expected_commit,
            args.test_command,
            _kernel_env(),
        )
        output = _execute_code(
            session,
            server_url,
            server_token,
            code,
            args.execution_timeout,
        )
        exit_code = _parse_exit_code(output)
        if exit_code is None:
            error = "Integration test run did not report an exit code."
            status = "error"
        elif exit_code == 0:
            status = "passed"
        else:
            error = f"Integration tests exited with code {exit_code}."
            status = "failed"
        tests_passed = _parse_count(output, "passed")
        tests_failed = _parse_count(output, "failed") + _parse_count(
            output,
            "error",
        )
    except Exception:  # noqa: BLE001
        error = traceback.format_exc()
        status = "error"
    finally:
        if username:
            try:
                _stop_server(session, args.hub_url, username, server_name)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: cleanup failed: {exc}", file=sys.stderr)

    duration = time.monotonic() - started
    result = {
        "pr": int(args.pr) if args.pr else None,
        "commit": _parse_commit(output) or args.expected_commit,
        "image": args.image,
        "hub": args.hub_url,
        "server": server_name or "default",
        "resources": args.resource_label,
        "status": status,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "duration_seconds": round(duration, 1),
        "error": error,
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    log_path.write_text(output)
    _write_summary(result, output, summary_path)
    print(summary_path.read_text())

    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
