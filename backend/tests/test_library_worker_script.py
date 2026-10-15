"""Run the real library worker script, which the adapter tests replace with fakes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import localforge.adapters.library_worker as library_worker

STUB_OPENVINO_GENAI = """
class GenerationConfig:
    pass


class LLMPipeline:
    def __init__(self, path, device):
        self.device = device

    def generate(self, prompt, config, streamer):
        streamer("stub output")
"""


def _initialize(tmp_path: Path, model_dir: Path) -> dict[str, object]:
    packages = tmp_path / "site"
    (packages / "openvino_genai").mkdir(parents=True)
    (packages / "openvino_genai" / "__init__.py").write_text(STUB_OPENVINO_GENAI, "utf-8")
    environment = {**os.environ, "PYTHONPATH": str(packages)}
    process = subprocess.run(
        [sys.executable, library_worker.__file__, "--backend", "openvino_genai"],
        input=json.dumps(
            {"op": "initialize", "model_path": str(model_dir), "device": "CPU"}
        ).encode()
        + b"\n",
        capture_output=True,
        env=environment,
        timeout=60,
        check=False,
    )
    first_line = process.stdout.decode().splitlines()[0]
    return json.loads(first_line)  # type: ignore[no-any-return]


def test_worker_loads_the_runtime_package_not_the_adapter_module_beside_it(
    tmp_path: Path,
) -> None:
    # The worker's own folder holds LocalForge's openvino_genai.py adapter; it must not
    # shadow the real runtime package when the worker runs as a script.
    model_dir = tmp_path / "qwen2.5-1.5b-instruct-int4-ov"
    model_dir.mkdir()
    (model_dir / "openvino_model.xml").write_text("<net/>", "utf-8")

    ready = _initialize(tmp_path, model_dir)

    assert ready["type"] == "ready"
    # A dotted folder name is not a file extension.
    assert ready["model_format"] == "openvino_ir"
