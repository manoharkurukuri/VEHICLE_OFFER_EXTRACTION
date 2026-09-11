from pathlib import Path

from app.core.run_context import get_run_context, start_run
from app.utils.output_paths import (
    get_error_directory,
    get_output_directory,
    get_zip_directory,
)


def test_output_directories_are_run_scoped():
    ctx = get_run_context()
    base = get_output_directory()
    zip_dir = get_zip_directory()
    err_dir = get_error_directory()

    assert base == ctx.run_dir
    assert zip_dir == ctx.run_dir
    assert err_dir.parent == ctx.run_dir
    assert err_dir.name == "errors"
    assert base.name.endswith(ctx.run_id)
    assert base.is_dir() and err_dir.is_dir()


def test_separate_runs_use_separate_folders():
    first = get_output_directory()
    start_run()
    second = get_output_directory()

    assert first != second
    assert Path(first).name != Path(second).name
