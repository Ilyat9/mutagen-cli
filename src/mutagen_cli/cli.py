"""The `mutagen` command line."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

from . import __version__, coverage_map
from .cache import Cache
from .generator import estimate, generate
from .invent import invent
from .models import SURVIVED, Usage
from .provider import (
    DEFAULT_PROVIDER,
    AnthropicProvider,
    OpenRouterProvider,
    ProviderError,
    default_model,
    load_config,
    missing_key_error,
    resolve_api_key,
)
from .report import (
    render_json,
    render_markdown,
    render_terminal,
    summarize,
    write_text,
)
from .runner import (
    RunnerConfig,
    check_baseline,
    collect_coverage_map,
    default_workers,
    detect_python,
    execute,
    prepare_workspace,
)
from .scope import ScopeError, collect_targets, map_tests, repo_root

console = Console()


def fail(message: str, code: int = 2) -> None:
    console.print(Text(f"error: {message}", style="bold red"))
    raise SystemExit(code)


def _group_warnings(warnings: list[str]) -> list[tuple[str, list[str]]]:
    """Collapse `"<label>: <reason>"` warnings that share a reason."""
    grouped: dict[str, list[str]] = {}
    for warning in warnings:
        label, _, reason = warning.partition(": ")
        grouped.setdefault(reason or warning, []).append(label)
    return list(grouped.items())


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="mutagen")
def main() -> None:
    """Your tests are green. Find out what they don't catch."""


@main.command()
@click.option("--base", default="main", show_default=True,
              help="Git ref to diff against when picking what to mutate.")
@click.option("--all", "all_files", is_flag=True, help="Mutate the whole codebase.")
@click.option("--path", "paths", multiple=True, type=click.Path(exists=True),
              help="Mutate specific files or directories. Repeatable.")
@click.option("--max-mutants", default=25, show_default=True, type=int)
@click.option("--max-files", default=20, show_default=True, type=int)
@click.option("--timeout", default=30.0, show_default=True, type=float,
              help="Seconds allowed per mutant before it counts as a timeout.")
@click.option("--workers", default=None, type=int, help="Parallel test runs. Default: CPUs/2.")
@click.option("--provider", default=DEFAULT_PROVIDER, show_default=True,
              type=click.Choice(["openrouter", "anthropic"]),
              help="LLM provider. OpenRouter works from Russia without a VPN.")
@click.option("--model", default=None,
              help="Model id. Default: anthropic/claude-sonnet-5 on OpenRouter, "
                   "claude-opus-5 on Anthropic.")
@click.option("--effort", default="medium", show_default=True,
              type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
              help="Anthropic only; ignored by the OpenRouter provider.")
@click.option("--python", "python_bin", default=None,
              help="Interpreter used to run the tests. Default: the project's venv.")
@click.option("--invent", "invent_flag", is_flag=True,
              help="Write a test for each surviving mutant.")
@click.option("--invent-apply", is_flag=True,
              help="Also save verified suggested tests into tests/mutagen_generated/.")
@click.option("--report-md", type=click.Path(), default=None)
@click.option("--report-json", type=click.Path(), default=None)
@click.option("--no-cache", is_flag=True, help="Ignore the on-disk LLM cache.")
@click.option("--dry-run", is_flag=True, help="Show what would run, then stop.")
@click.option("--fail-under", type=float, default=None,
              help="Exit non-zero if the mutation score is below this percentage.")
def run(
    base, all_files, paths, max_mutants, max_files, timeout, workers, provider, model,
    effort, python_bin, invent_flag, invent_apply, report_md, report_json, no_cache,
    dry_run, fail_under,
) -> None:
    """Generate mutants, run the tests against them, report the survivors."""
    started = time.monotonic()
    want_invent = invent_flag or invent_apply
    model = model or default_model(provider)

    try:
        root = repo_root(Path.cwd())
    except ScopeError as exc:
        fail(str(exc))

    # --- what to mutate -------------------------------------------------------
    try:
        targets = collect_targets(
            root, base=base, paths=list(paths), all_files=all_files, max_files=max_files
        )
    except ScopeError as exc:
        fail(str(exc))

    if not targets:
        console.print(
            Text(
                f"Nothing to mutate. No changed Python functions against '{base}'.",
                style="yellow",
            )
        )
        console.print(Text("Try --all, or --path <file>.", style="dim"))
        raise SystemExit(0)

    map_tests(root, targets)
    calls, max_expected = estimate(targets, max_mutants)
    unmapped = sum(1 for t in targets if not t.test_files)

    console.print()
    console.print(
        f"[bold]{len(targets)}[/bold] function(s) in "
        f"[bold]{len({t.path for t in targets})}[/bold] file(s) "
        f"→ up to [bold]{max_expected}[/bold] mutants, [bold]{calls}[/bold] LLM call(s) "
        f"with [cyan]{model}[/cyan]"
    )
    if unmapped:
        console.print(
            Text(
                f"{unmapped} function(s) have no matching test file; those mutants "
                "will run the full suite.",
                style="dim",
            )
        )

    if dry_run:
        console.print(
            Text(
                "mapping: heuristic (a --dry-run does not run your suite, so no "
                "coverage is measured)",
                style="dim",
            )
        )
        for target in targets:
            tests = ", ".join(target.test_files) or "(whole suite)"
            console.print(f"  {target.label}  →  {tests}", style="dim")
        raise SystemExit(0)

    # --- baseline, and the coverage map that comes with it --------------------
    # Before generation, not after: a red suite makes every number meaningless,
    # and finding that out *after* paying the model would be rude. The map the
    # instrumented run produces then tells generation which tests to show the
    # model, and execution which tests to run.
    python = python_bin or detect_python(root)
    if python_bin and not shutil.which(python_bin):
        fail(f"--python {python_bin}: no such interpreter")

    n_workers = max(1, min(workers or default_workers(), max_expected))
    cfg = RunnerConfig(root=root, python=python, timeout=timeout, workers=n_workers)
    want_coverage = coverage_map.available(python)

    # The baseline runs before generation, so say now if the run is going to
    # need the cache rather than after a full instrumented suite run.
    if not resolve_api_key(provider, root):
        console.print(
            Text(
                f"no {provider} API key found; this run can only succeed from the "
                "on-disk cache.",
                style="yellow",
            )
        )

    tmp = Path(tempfile.mkdtemp(prefix="mutagen-"))
    coverage = None
    mapping = "heuristic"
    try:
        with console.status("preparing a sandbox"):
            base_workdir = tmp / "w0"
            prepare_workspace(root, base_workdir)

        label = (
            "running your suite with coverage to find which tests reach what"
            if want_coverage
            else "checking the suite is green before mutating"
        )
        with console.status(label):
            if want_coverage:
                baseline, coverage = collect_coverage_map(cfg, base_workdir)
            else:
                baseline = check_baseline(cfg, [], base_workdir)

        if baseline.pytest_missing:
            fail(
                f"pytest is not installed for {python}. Install it there "
                "(pip install pytest), or point --python at the interpreter "
                "that runs your tests."
            )
        if not baseline.green:
            console.print(Text("Your test suite is not green.", style="bold red"))
            console.print(
                Text(
                    "Mutation results would be meaningless — every mutant would look "
                    "killed. Fix the failures first.",
                    style="dim",
                )
            )
            console.print(baseline.output.strip()[-2000:])
            raise SystemExit(2)

        if coverage:
            refined, uncovered = coverage_map.map_targets(coverage, targets)
            mapping = "coverage"
            console.print(
                Text(
                    f"mapping: coverage ({refined}/{len(targets)} function(s) mapped "
                    f"from a {baseline.duration:.1f}s instrumented baseline"
                    + (f"; {uncovered} reached by no test" if uncovered else "")
                    + ")",
                    style="dim",
                )
            )
        else:
            coverage = None
            console.print(
                Text(
                    "mapping: heuristic (install pytest-cov for precise coverage "
                    "mapping)",
                    style="yellow",
                )
            )

        # A slow suite needs a proportionally larger budget or every mutant
        # "times out" for reasons that have nothing to do with the mutation.
        adaptive = baseline.duration * 3 + 5
        if adaptive > cfg.timeout:
            cfg.timeout = adaptive
            console.print(
                Text(
                    f"baseline took {baseline.duration:.1f}s; raising the per-mutant "
                    f"timeout to {cfg.timeout:.0f}s",
                    style="dim",
                )
            )

        results, usage = _generate_and_run(
            root, targets, cfg, tmp, base_workdir, coverage,
            provider=provider, model=model, effort=effort, no_cache=no_cache,
            max_mutants=max_mutants, want_invent=want_invent, invent_apply=invent_apply,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- report ---------------------------------------------------------------
    summary = summarize(results, duration=time.monotonic() - started)
    render_terminal(console, results, summary, usage, model, mapping=mapping)

    if report_md:
        write_text(Path(report_md), render_markdown(results, summary, usage, model, mapping))
        console.print(Text(f"markdown report → {report_md}", style="dim"))
    if report_json:
        write_text(Path(report_json), render_json(results, summary, usage, model, mapping))
        console.print(Text(f"json report → {report_json}", style="dim"))

    if fail_under is not None and summary.score is not None:
        if summary.score * 100 < fail_under:
            console.print(
                Text(
                    f"mutation score {summary.score * 100:.0f}% is below the "
                    f"--fail-under threshold of {fail_under:.0f}%",
                    style="bold red",
                )
            )
            raise SystemExit(1)

    raise SystemExit(0)


def _generate_and_run(
    root, targets, cfg, tmp, base_workdir, coverage, *,
    provider, model, effort, no_cache, max_mutants, want_invent, invent_apply,
):
    """Generate mutants, run them, optionally invent tests. Returns (results, usage)."""
    cache = Cache(root, enabled=not no_cache)
    config = load_config(root)
    api_key = resolve_api_key(provider, root)
    if provider == "openrouter":
        llm = OpenRouterProvider(
            model=model,
            api_key=api_key,
            cache=cache,
            reasoning=bool(config.get("openrouter_reasoning", False)),
            price_overrides=config.get("prices"),
        )
    else:
        llm = AnthropicProvider(
            model=model,
            api_key=api_key,
            cache=cache,
            effort=effort,
            price_overrides=config.get("prices"),
        )

    usage = Usage()
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("generating mutants", total=len(targets))
        try:
            mutants, gen_usage, warnings = generate(
                root, targets, llm, model, max_mutants,
                on_progress=lambda _t, _n: progress.advance(task),
            )
        except ProviderError as exc:
            fail(str(exc))
    usage.add(gen_usage)
    # One line per function saying the same thing is noise, not information:
    # a missing key or a dead endpoint fails identically for every target.
    for reason, labels in _group_warnings(warnings):
        where = labels[0] if len(labels) == 1 else f"{len(labels)} functions"
        console.print(Text(f"warning: {where}: {reason}", style="yellow"))

    if not mutants:
        # Without a key every call failed for the same reason, and "no usable
        # mutants" would send the user off to fiddle with --model.
        if not api_key:
            fail(missing_key_error(provider))
        fail("the model returned no usable mutants. Try --max-mutants or a different --model.")

    # --- sandboxes ------------------------------------------------------------
    # w0 already exists and is warm from the baseline run; add the rest only if
    # there is enough work to keep them busy.
    n_workers = max(1, min(cfg.workers, len(mutants)))
    workdirs = [base_workdir]
    with console.status(f"preparing {n_workers} sandbox(es)"):
        for index in range(1, n_workers):
            workdir = tmp / f"w{index}"
            prepare_workspace(cfg.root, workdir)
            workdirs.append(workdir)

    # --- run ------------------------------------------------------------------
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("running mutants", total=len(mutants))
        results = execute(
            cfg, mutants, workdirs,
            on_result=lambda _r: progress.advance(task),
            coverage=coverage,
        )

    # --- invent ---------------------------------------------------------------
    if want_invent:
        survivors = sum(1 for r in results if r.verdict == SURVIVED)
        if survivors:
            with Progress(
                SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
                console=console, transient=True,
            ) as progress:
                task = progress.add_task("writing tests for survivors", total=survivors)
                inv_usage, inv_warnings = invent(
                    cfg.root, results, llm, model, cfg, base_workdir,
                    apply_to_disk=invent_apply,
                    on_progress=lambda _r: progress.advance(task),
                )
            usage.add(inv_usage)
            for warning in inv_warnings:
                console.print(Text(warning, style="dim"))

    return results, usage


if __name__ == "__main__":  # pragma: no cover
    main()
