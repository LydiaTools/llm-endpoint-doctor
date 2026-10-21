from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from . import __version__
from .core import DiagnosticReport, normalize_base_url, run_diagnostics


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-doctor",
        description="Check OpenAI-compatible chat and optional image-generation endpoints.",
        epilog="The default run sends one chat request. Each selected check sends one extra request; image generation may incur provider charges.",
    )
    parser.add_argument("--base-url", required=True, help="API base URL, such as https://api.example.com/v1")
    parser.add_argument("--model", required=True, help="Exact model ID or provider alias to test")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds (default: 20)")
    parser.add_argument("--max-output-tokens", type=int, metavar="N", help="Explicit output cap, 1..8192; may include reasoning tokens (default: 12, or 24 for JSON)")
    parser.add_argument("--token-field", choices=("max_tokens", "max_completion_tokens"), default="max_tokens", help="Output-cap request field required by your provider (default: max_tokens)")
    parser.add_argument("--check-model-list", action="store_true", help="Also GET /models (one extra request)")
    parser.add_argument("--check-streaming", action="store_true", help="Also test SSE streaming (one extra generation)")
    parser.add_argument("--check-stream-options", action="store_true", help="Also test stream_options.include_usage (one extra generation)")
    parser.add_argument("--check-json-mode", action="store_true", help="Also test JSON mode (one extra generation)")
    parser.add_argument("--check-image-generation", action="store_true", help="Also send one image request to /images/generations using the same base URL and key; may incur provider charges")
    parser.add_argument("--image-model", help="Exact image model ID; required with --check-image-generation")
    key_group = parser.add_mutually_exclusive_group()
    key_group.add_argument("--api-key-env", metavar="NAME", help="Read the API key from an environment variable")
    key_group.add_argument("--no-prompt", action="store_true", help="Skip the hidden key prompt (for keyless endpoints)")
    parser.add_argument("--json-report", type=Path, help="Write a sanitized JSON report to a new file")
    parser.add_argument("--version", action="version", version=f"llm-doctor {__version__}")
    return parser


def _print_report(report: DiagnosticReport) -> None:
    print("LLM Endpoint Doctor")
    print(f"Target host: {report.target}")
    print(f"Model: {report.model}")
    if report.image_model is not None:
        print(f"Image model: {report.image_model}")
    print("")
    for check in report.checks:
        label = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}.get(check.status, check.status.upper())
        timing = f" ({check.elapsed_ms} ms)" if check.elapsed_ms is not None else ""
        print(f"[{label}] {check.name}{timing} — {check.detail}")
    print("")
    print("Basic chat: READY" if report.chat_ready else "Basic chat: NOT READY")
    print("No API key, prompt, or response content is included in the report.")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    if args.max_output_tokens is not None and not 1 <= args.max_output_tokens <= 8192:
        parser.error("--max-output-tokens must be between 1 and 8192")
    try:
        normalize_base_url(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.model.strip() or len(args.model.strip()) > 256 or not args.model.strip().isprintable():
        parser.error("--model must be printable and between 1 and 256 characters")
    if args.check_image_generation and not args.image_model:
        parser.error("--image-model is required with --check-image-generation")
    if not args.check_image_generation and args.image_model is not None:
        parser.error("--image-model can only be used with --check-image-generation")
    if args.image_model is not None and (
        not args.image_model.strip()
        or len(args.image_model.strip()) > 256
        or not args.image_model.strip().isprintable()
    ):
        parser.error("--image-model must be printable and between 1 and 256 characters")
    if args.json_report:
        if args.json_report.exists():
            parser.error(f"report file already exists: {args.json_report}")
        if not args.json_report.parent.exists() or not args.json_report.parent.is_dir():
            parser.error(f"report directory does not exist: {args.json_report.parent}")

    try:
        if args.api_key_env is not None:
            api_key = os.environ.get(args.api_key_env)
            if not api_key:
                parser.error(f"environment variable {args.api_key_env} is not set or empty")
        elif args.no_prompt:
            api_key = ""
        else:
            api_key = getpass.getpass("API key (press Enter for a keyless endpoint): ")
        report = run_diagnostics(
            base_url=args.base_url,
            model=args.model,
            api_key=api_key,
            timeout=args.timeout,
            check_model_list=args.check_model_list,
            check_streaming=args.check_streaming,
            check_json_mode=args.check_json_mode,
            check_stream_options=args.check_stream_options,
            max_output_tokens=args.max_output_tokens,
            token_field=args.token_field,
            check_image_generation=args.check_image_generation,
            image_model=args.image_model,
        )
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 130
    except ValueError as exc:
        parser.error(str(exc))

    _print_report(report)
    if args.json_report:
        try:
            with args.json_report.open("x", encoding="utf-8") as handle:
                json.dump(report.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except FileExistsError:
            print(f"Report not written: {args.json_report} already exists.", file=sys.stderr)
            return 2
        print(f"Sanitized report saved to {args.json_report}")
    return 0 if report.chat_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
