# This file is part of LensKit.
# Copyright (C) 2018-2023 Boise State University
# Copyright (C) 2023-2024 Drexel University
# Licensed under the MIT license, see LICENSE.md for details.
# SPDX-License-Identifier: MIT

# pyright: basic
from __future__ import annotations

import logging
import multiprocessing as mp
import os
from dataclasses import dataclass
from typing import Optional

import torch
from threadpoolctl import threadpool_limits

_config: Optional[ParallelConfig] = None
_log = logging.getLogger(__name__)


@dataclass
class ParallelConfig:
    processes: int
    threads: int
    child_threads: int


def initialize(
    *,
    processes: int | None = None,
    threads: int | None = None,
    child_threads: int | None = None,
):
    """
    Set up and configure LensKit parallelism.  This only needs to be called if
    you want to control when and how parallelism is set up; components using
    parallelism will call :func:`ensure_init`, which will call this function
    with its default arguments if it has not been called.

    Args:
        processes:
            The number of processes to use for multiprocessing evaluations.
            Configured from ``LK_NUM_PROCS``.  Defaults to the number of CPUs or
            4, whichever is smaller.
        threads:
            The number of threads to use for parallel model training and similar
            operations.  This is passed to :func:`torch.set_num_threads` and to
            the BLAS library's threading layer.  Environment variable is
            ``LK_NUM_THREADS``.  Defaults to the number of CPUs or 8, whichever
            is smaller, to avoid runaway thread coordination overhead on large
            machines.
        child_threads:
            The number of threads to use in the worker processes in multiprocessing
            operations.  This is like ``threads``, except it is passed to the
            underlying libraries in worker processes.  Environment variable is
            ``LK_NUM_CHILD_THREADS``.  Defaults is computed from the number
            of CPUs with a max of 4 threads per worker.
    """
    global _config
    if _config:
        _log.warning("parallelism already initialized")
        return

    # our parallel computation doesn't work with FD sharing
    torch.multiprocessing.set_sharing_strategy("file_system")

    _config = _resolve_parallel_config(processes, threads, child_threads)
    _log.debug("configuring for parallelism: %s", _config)

    torch.set_num_threads(_config.threads)
    threadpool_limits(_config.threads, "blas")


def ensure_parallel_init():
    """
    Make sure LensKit parallelism is configured, and configure with defaults if
    it is not.

    Components using parallelism or intensive computations should call this
    function before they begin training.
    """
    if not _config:
        initialize()


def get_parallel_config() -> ParallelConfig:
    """
    Ensure that parallelism is configured and return the configuration.
    """
    ensure_parallel_init()
    assert _config is not None
    return _config


def _resolve_parallel_config(
    processes: int | None = None,
    threads: int | None = None,
    child_threads: int | None = None,
) -> ParallelConfig:
    nprocs = os.environ.get("LK_NUM_PROCS", None)
    nthreads = os.environ.get("LK_NUM_THREADS", None)
    cthreads = os.environ.get("LK_NUM_CHILD_THREADS", None)
    ncpus = mp.cpu_count()

    if processes is None and nprocs:
        processes = int(nprocs)

    if threads is None and nthreads:
        threads = int(nthreads)

    if child_threads is None and cthreads:
        child_threads = int(cthreads)

    if processes is None:
        processes = min(ncpus, 4)

    if threads is None:
        threads = min(ncpus, 8)

    if child_threads is None:
        child_threads = min(ncpus // processes, 4)

    return ParallelConfig(processes, threads, child_threads)
