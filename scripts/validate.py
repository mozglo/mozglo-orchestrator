#!/usr/bin/env python3
"""Validate repository-owned MOZGLO skill files using only the Python standard library."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "mozglo-orchestrator"
RUNNER_PATH = SKILL / "scripts" / "run.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("mozglo_runner", RUNNER_PATH)
    assert spec and spec.loader, "Cannot load packaged runner"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_file(path: Path) -> None:
    assert path.is_file(), f"Missing required file: {path.relative_to(ROOT)}"


def check_links(path: Path, text: str, boundary: Path) -> None:
    targets = re.findall(r"\]\(([^\s)]+)\)", text)
    targets += re.findall(r'(?:href|src)=["\']([^"\']+)["\']', text)
    for target in targets:
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        local = target.split("#", 1)[0].split("?", 1)[0]
        if local:
            resolved = (path.parent / local).resolve()
            assert resolved.is_relative_to(boundary) and resolved.exists(), f"Broken or non-portable link in {path.relative_to(ROOT)}: {target}"


def check_settings() -> None:
    path = SKILL / "settings.json"
    require_file(path)
    settings = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(settings, dict), "settings.json must be an object"
    assert isinstance(settings.get("models"), dict), "settings.json models must be an object"
    assert isinstance(settings.get("reasoning_effort"), dict), "settings.json reasoning_effort must be an object"
    assert isinstance(settings.get("allocation"), dict), "settings.json allocation must be an object"
    runner = load_runner()
    loaded = runner.load_settings(path)
    allocation = runner.allocation(loaded)
    assert set(loaded["models"]) == {"astra", "sol", "luna"}, "Runner must load all role models"
    assert set(loaded["reasoning_effort"]) == {"astra", "sol", "luna"}, "Runner must load effort for every role"
    assert allocation["total_agents"] == allocation["native_children"] + 1, "Allocation total must include Astra"
    assert allocation["total_agents"] <= min(runner.MAX_AGENTS, loaded["allocation"]["max_agents"]), "Allocation exceeds configured/hard maximum"


def main() -> None:
    required = [
        ROOT / name for name in (
            "README.md", "README.ru.md", "README.zh-CN.md", "LICENSE", "AGENTS.md",
            "docs/architecture.md",
        )
    ] + [
        SKILL / name for name in (
            "SKILL.md", "LICENSE", "settings.json", "agents/openai.yaml", "scripts/run.py",
            "references/report.md", "references/runtime.md",
        )
    ] + [ROOT / "assets" / name for name in ("banner.svg", "banner.png")]
    for path in required:
        require_file(path)

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text.startswith("---\nname: mozglo-orchestrator\n"), "Invalid skill name"
    assert re.search(r"^description: \S", skill_text, re.M), "Missing skill description"
    check_settings()

    assert (ROOT / "LICENSE").read_bytes() == (SKILL / "LICENSE").read_bytes(), "Root and skill licenses differ"
    license_text = (SKILL / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n\n"), "Skill license must be MIT"
    assert re.search(r"^Copyright \(c\) \d{4} mozglo$", license_text, re.M), "MIT notice must name mozglo as author"
    for clause in (
        "Permission is hereby granted, free of charge, to any person obtaining a copy",
        "The above copyright notice and this permission notice shall be included",
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND',
    ):
        assert clause in license_text, f"Incomplete MIT license: {clause}"

    # Inspect only package prose and metadata intended for public distribution.
    public_files = [ROOT / name for name in (
        "README.md", "README.ru.md", "README.zh-CN.md", "LICENSE", "AGENTS.md",
        "docs/architecture.md",
    )]
    public_files += [path for path in SKILL.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    private_patterns = (
        re.compile(r"/(?:var/(?:tmp|www)|root|home/[^/\s]+|Users/[^/\s]+)(?:/|\b)"),
        re.compile(r"remote-ssh-codex", re.I),
    )
    for path in public_files:
        if path.suffix not in {".md", ".yaml", ".yml", ".json", ".py"} and path.name != "LICENSE":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in private_patterns:
            assert not pattern.search(text), f"Private path or host naming in {path.relative_to(ROOT)}"
        if path.suffix == ".md":
            boundary = SKILL if path.is_relative_to(SKILL) else ROOT
            check_links(path, text, boundary)

    banner = ET.parse(ROOT / "assets" / "banner.svg").getroot()
    assert banner.tag.endswith("svg") and banner.get("viewBox") == "0 0 1200 320", "Unexpected banner SVG"
    for node in banner.iter():
        assert node.tag.rsplit("}", 1)[-1] not in {"script", "foreignObject"}, "Unsafe SVG element"

    print("Package valid: portable skill files, settings, allocation limits, links, MIT licenses, public text, and banner assets.")


if __name__ == "__main__":
    main()
