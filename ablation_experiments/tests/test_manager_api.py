from __future__ import annotations

import sys
import json
from pathlib import Path

import httpx


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from manager_api import ManagerApi


def test_apply_and_restore_uses_parameter_identity() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"list": [{"id": 7, "paramCode": "dialogue_policy.enabled", "paramValue": "true", "valueType": "boolean", "remark": "x"}]}})
        return httpx.Response(200, json={"code": 0, "data": None})

    api = ManagerApi("http://manager/xiaoyun", "token", transport=httpx.MockTransport(handler))
    baseline = api.read_params()
    api.apply_overrides(baseline, {"dialogue_policy.enabled": False})
    api.restore(baseline)

    assert len(requests) == 3
    assert json.loads(requests[1].content)["paramValue"] == "false"
    assert json.loads(requests[2].content)["paramValue"] == "true"


def test_flatten_overrides_uses_dot_separated_codes() -> None:
    from manager_api import flatten_overrides

    assert flatten_overrides({"dialogue_policy": {"enabled": False}}) == {
        "dialogue_policy.enabled": False
    }
